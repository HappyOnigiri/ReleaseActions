import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/publish-release"
MANIFEST = yaml.safe_load((ACTION / "action.yml").read_text())


class PublishReleaseTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="release action test ")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.runner_temp = self.root / "runner temp"
        self.runner_temp.mkdir()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        shutil.copyfile(ROOT / "tests/fake-gh.py", fake_bin / "gh")
        (fake_bin / "gh").chmod(0o755)
        self.state_path = self.root / "gh.json"
        self.state_path.write_text(json.dumps({
            "release": None, "latest": "v1.1.0", "calls": [],
            "branch_deleted": False,
        }))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update({
            "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
            "FAKE_GH_STATE": str(self.state_path),
            "RUNNER_TEMP": str(self.runner_temp),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            "GITHUB_REPOSITORY": "example/repo",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("commit", "--quiet", "--allow-empty", "-m", "initial")
        self.initial = self.git("rev-parse", "HEAD").strip()
        remote = self.root / "remote.git"
        self.git("init", "--quiet", "--bare", str(remote))
        self.git("remote", "add", "origin", str(remote))
        self.inputs = {
            key: value.get("default", "") for key, value in MANIFEST["inputs"].items()
        }
        self.inputs.update({
            "branch": "release/v1.2.3", "github-token": "test-token",
            "pr-body": "Release notes\n\n日本語の本文。\n",
        })
        self.outputs = {}

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env=self.env, text=True,
            stderr=subprocess.STDOUT,
        )

    def state(self):
        return json.loads(self.state_path.read_text())

    def resolve(self, value):
        match = re.fullmatch(r"\$\{\{ (.*) \}\}", value)
        if match is None:
            return value
        for expression in match[1].split(" || "):
            if expression.startswith("inputs."):
                result = self.inputs[expression.removeprefix("inputs.")]
            elif expression == "github.action_path":
                result = str(ACTION)
            elif expression == "github.sha":
                result = self.initial
            else:
                result = self.outputs.get(expression, "")
            if result:
                return result
        return ""

    def run_step(self, step):
        env = {**self.env, **{
            key: self.resolve(value) for key, value in step.get("env", {}).items()
        }}
        output = Path(env["GITHUB_OUTPUT"])
        output.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=self.repo, env=env, capture_output=True, text=True,
        )
        for line in output.read_text().splitlines():
            key, value = line.split("=", 1)
            self.outputs[f"steps.{step.get('id', '')}.outputs.{key}"] = value
        return result

    def run_action(self):
        for step in MANIFEST["runs"]["steps"]:
            condition = step.get("if", "")
            if condition == "inputs.assets != ''" and not self.inputs["assets"]:
                continue
            if condition == "inputs.update-major-tag == 'true'" and self.inputs["update-major-tag"] != "true":
                continue
            if "uses" in step:
                self.assertEqual(step["uses"], "actions/checkout@v4")
                self.assertEqual(step["with"]["fetch-depth"], 0)
                self.git("clean", "-ffdx")
                self.git("checkout", "--detach", self.resolve(step["with"]["ref"]))
                self.git("fetch", "--tags", "origin")
                continue
            result = self.run_step(step)
            if result.returncode:
                return result
        return result

    def assets(self):
        dist = self.repo / "build output"
        dist.mkdir(exist_ok=True)
        for name, content in {"install.sh": "installer", "wx.tar.gz": "binary"}.items():
            (dist / name).write_text(content)
        self.inputs["assets"] = "build output/install.sh\nbuild output/wx.tar.gz\n"

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assets_survive_checkout_and_publish_only_after_upload(self):
        self.assets()
        self.assert_success(self.run_action())
        state = self.state()
        self.assertEqual(state["release"]["assets"], {"install.sh": "installer", "wx.tar.gz": "binary"})
        self.assertFalse(state["release"]["draft"])
        self.assertEqual(state["latest"], "v1.2.3")
        self.assertTrue(state["branch_deleted"])
        calls = [call for call in state["calls"] if call[0] == "release"]
        self.assertEqual([call[1] for call in calls], ["view", "create", "upload", "edit"])
        self.assertIn("--draft", calls[1])
        self.assertIn("--verify-tag", calls[1])
        self.assertIn("--draft=false", calls[3])
        self.assertEqual(self.git("rev-parse", "refs/tags/v1.2.3").strip(), self.initial)

    def test_failed_upload_keeps_draft_and_retry_completes(self):
        self.assets()
        self.env["FAIL_UPLOAD"] = "1"
        result = self.run_action()
        self.assertNotEqual(result.returncode, 0)
        state = self.state()
        self.assertTrue(state["release"]["draft"])
        self.assertEqual(len(state["release"]["assets"]), 1)
        self.assertEqual(state["latest"], "v1.1.0")
        self.assertFalse(state["branch_deleted"])
        self.assertIn("draft", (self.root / "summary").read_text())
        del self.env["FAIL_UPLOAD"]
        self.assets()
        self.assert_success(self.run_action())
        self.assertFalse(self.state()["release"]["draft"])
        self.assertEqual(len(self.state()["release"]["assets"]), 2)

    def test_failed_publish_retries_from_draft(self):
        self.assets()
        self.env["FAIL_PUBLISH"] = "1"
        self.assertNotEqual(self.run_action().returncode, 0)
        self.assertTrue(self.state()["release"]["draft"])
        self.assertEqual(self.state()["latest"], "v1.1.0")
        del self.env["FAIL_PUBLISH"]
        self.assets()
        self.assert_success(self.run_action())
        self.assertFalse(self.state()["release"]["draft"])

    def test_published_retry_does_not_change_assets_notes_or_latest(self):
        self.assets()
        self.assert_success(self.run_action())
        state = self.state()
        state["latest"] = "v2.0.0"
        state["calls"] = []
        self.state_path.write_text(json.dumps(state))
        self.assets()
        (self.repo / "build output/install.sh").write_text("different installer")
        self.inputs["pr-body"] = "different notes"
        self.assert_success(self.run_action())
        self.assertEqual(self.state()["release"], state["release"])
        self.assertEqual(self.state()["latest"], "v2.0.0")
        self.assertEqual([c[1] for c in self.state()["calls"] if c[0] == "release"], ["view"])

    def test_tag_mismatch_refuses_before_release_mutation(self):
        self.git("tag", "v1.2.3")
        self.git("push", "origin", "v1.2.3")
        self.git("commit", "--quiet", "--allow-empty", "-m", "different target")
        self.inputs["target-commit"] = self.git("rev-parse", "HEAD").strip()
        self.assets()
        result = self.run_action()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("一致しません", result.stdout)
        self.assertIsNone(self.state()["release"])
        self.assertFalse(self.state()["branch_deleted"])

    def test_matching_annotated_tag_is_accepted(self):
        self.git("tag", "-a", "v1.2.3", "-m", "release")
        self.git("push", "origin", "v1.2.3")
        self.assets()
        self.assert_success(self.run_action())
        self.assertFalse(self.state()["release"]["draft"])

    def test_branch_cleanup_failure_can_retry_without_reuploading(self):
        self.assets()
        self.env["FAIL_DELETE"] = "1"
        self.assertNotEqual(self.run_action().returncode, 0)
        self.assertFalse(self.state()["release"]["draft"])
        self.assertFalse(self.state()["branch_deleted"])
        del self.env["FAIL_DELETE"]
        self.assets()
        self.assert_success(self.run_action())
        self.assertTrue(self.state()["branch_deleted"])
        uploads = [c for c in self.state()["calls"] if c[:2] == ["release", "upload"]]
        self.assertEqual(len(uploads), 1)

    def test_explicit_target_commit_pins_checkout_and_tag(self):
        self.git("commit", "--quiet", "--allow-empty", "-m", "target")
        target = self.git("rev-parse", "HEAD").strip()
        self.inputs["target-commit"] = target
        self.git("checkout", "--detach", self.initial)
        self.assets()
        self.assert_success(self.run_action())
        self.assertEqual(self.git("rev-parse", "refs/tags/v1.2.3").strip(), target)

    def test_invalid_assets_stop_before_tag_creation(self):
        self.assets()
        other = self.repo / "other"
        other.mkdir()
        (other / "install.sh").write_text("duplicate")
        (other / "bad#label").write_text("bad label")
        cases = [
            "missing", "build output/*.sh", "\n\n",
            "build output/install.sh\nother/install.sh", "other/bad#label",
        ]
        for assets in cases:
            with self.subTest(assets=assets):
                self.inputs["assets"] = assets
                self.assertNotEqual(self.run_action().returncode, 0)
                self.assertEqual(self.git("tag", "--list"), "")
                self.assertIsNone(self.state()["release"])

    def test_invalid_target_commit_stops_before_checkout(self):
        self.inputs["target-commit"] = "main"
        result = self.run_action()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("完全なコミット SHA", result.stdout)
        self.assertEqual(self.state()["calls"], [])

    def test_create_failure_does_not_upload_or_delete_branch(self):
        self.assets()
        self.env["FAIL_CREATE"] = "1"
        self.assertNotEqual(self.run_action().returncode, 0)
        self.assertIsNone(self.state()["release"])
        self.assertFalse(self.state()["branch_deleted"])
        self.assertNotIn("upload", [c[1] for c in self.state()["calls"]])

    def test_view_failure_does_not_overwrite_published_release(self):
        self.assets()
        self.assert_success(self.run_action())
        original = self.state()["release"]
        self.assets()
        self.env["FAIL_VIEW"] = "1"
        self.assertNotEqual(self.run_action().returncode, 0)
        self.assertEqual(self.state()["release"], original)

    def test_without_assets_creates_release_directly_and_updates_notes_on_retry(self):
        self.assert_success(self.run_action())
        self.assertFalse(self.state()["release"]["draft"])
        self.assertEqual(self.state()["release"]["assets"], {})
        self.inputs["pr-body"] = "updated notes"
        self.assert_success(self.run_action())
        self.assertEqual(self.state()["release"]["notes"], "updated notes\n")
        calls = [c for c in self.state()["calls"] if c[0] == "release"]
        self.assertEqual([c[1] for c in calls], ["view", "create", "view", "edit"])
        self.assertNotIn("--draft", calls[1])

    def test_without_assets_keeps_existing_draft_unpublished(self):
        state = self.state()
        state["release"] = {"draft": True, "assets": {}, "notes": "old notes"}
        self.state_path.write_text(json.dumps(state))
        self.assert_success(self.run_action())
        self.assertTrue(self.state()["release"]["draft"])
        self.assertEqual(self.state()["latest"], "v1.1.0")
        self.assertEqual(self.state()["release"]["notes"], self.inputs["pr-body"] + "\n")


if __name__ == "__main__":
    unittest.main()
