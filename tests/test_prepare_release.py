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
ACTION = ROOT / "actions/prepare-release"
MANIFEST = yaml.safe_load((ACTION / "action.yml").read_text())


class PrepareReleaseTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="prepare release test ")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        shutil.copyfile(ROOT / "tests/fake-prepare-gh.py", fake_bin / "gh")
        (fake_bin / "gh").chmod(0o755)
        self.state_path = self.root / "gh.json"
        self.state_path.write_text(json.dumps({
            "release_available": True, "merged_prs": [], "pr_state": None, "calls": [],
        }))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update({
            "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
            "FAKE_GH_STATE": str(self.state_path),
            "RUNNER_TEMP": str(self.root),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
            "GITHUB_REPOSITORY": "example/repo",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        })
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.paths = ["plugins/review/.claude-plugin/plugin.json", "plugins/display plugin.json"]
        for path in self.paths:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"name": path, "version": "1.2.3", "nested": {"version": "9.0.0"}}, indent=2) + "\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "initial")
        self.git("tag", "v1.2.3")
        self.git("init", "--quiet", "--bare", str(self.root / "remote.git"))
        self.git("remote", "add", "origin", str(self.root / "remote.git"))
        self.git("push", "--quiet", "origin", "main", "--tags")
        self.initial = self.git("rev-parse", "HEAD").strip()
        self.inputs = {key: value.get("default", "") for key, value in MANIFEST["inputs"].items()}
        self.inputs.update({"github-token": "test-token", "bump": "auto"})
        self.outputs = {}
        self.changelog_calls = 0

    def git(self, *args):
        return subprocess.check_output(
            ["git", *args], cwd=self.repo, env=self.env, text=True, stderr=subprocess.STDOUT,
        )

    def state(self, **updates):
        state = json.loads(self.state_path.read_text())
        if updates:
            state.update(updates)
            self.state_path.write_text(json.dumps(state))
        return state

    def resolve(self, value):
        match = re.fullmatch(r"\$\{\{ (.*) \}\}", value)
        if match is None:
            return value
        expression = match[1]
        if expression.startswith("inputs."):
            return self.inputs[expression.removeprefix("inputs.")]
        if expression == "github.action_path":
            return str(ACTION)
        return self.outputs.get(expression, "")

    def enabled(self, step):
        for clause in step.get("if", "").split(" && "):
            if not clause:
                continue
            match = re.fullmatch(r"(.+) (==|!=) '(.*)'", clause)
            self.assertIsNotNone(match, clause)
            left, operator, right = match.groups()
            actual = self.resolve("${{ " + left + " }}")
            if (actual == right) != (operator == "=="):
                return False
        return True

    def run_step(self, step):
        env = {**self.env, **{key: self.resolve(value) for key, value in step.get("env", {}).items()}}
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
            if not self.enabled(step):
                continue
            if "uses" in step:
                if step["uses"] == "actions/checkout@v4":
                    self.assertEqual(step["with"]["ref"], "main")
                    self.git("checkout", "--quiet", "main")
                else:
                    self.assertEqual(step["uses"], "mikepenz/release-changelog-builder-action@v5")
                    self.changelog_calls += 1
                    self.outputs["steps.changelog.outputs.changelog"] = "Release notes\n日本語の本文。"
                continue
            result = self.run_step(step)
            if result.returncode:
                return result
        return result

    def output(self, name):
        return self.resolve(MANIFEST["outputs"][name]["value"])

    def use_json(self):
        self.inputs.update({"version-bump-type": "json", "json-version-files": "\n".join(self.paths)})

    def merged(self, title, body=""):
        self.state(merged_prs=[{"number": 1, "title": title, "body": body}])

    def test_no_changes_skips_every_mutating_step_when_enabled(self):
        self.use_json()
        self.inputs["skip-if-no-changes"] = "true"
        self.merged("release: v1.2.3")
        before = {path: (self.repo / path).read_bytes() for path in self.paths}
        refs = self.git("ls-remote", "origin")
        result = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.output("skipped"), "true")
        for name in ("version", "pr-number", "pr-url"):
            self.assertEqual(self.output(name), "")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.initial)
        self.assertEqual(self.git("branch", "--format=%(refname:short)").strip(), "main")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("ls-remote", "origin"), refs)
        self.assertEqual(before, {path: (self.repo / path).read_bytes() for path in self.paths})
        self.assertEqual(self.changelog_calls, 0)
        self.assertFalse(any(call[:2] in (["pr", "create"], ["pr", "edit"], ["label", "create"]) for call in self.state()["calls"]))

    def test_no_changes_still_fails_by_default(self):
        result = self.run_action()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("マージされた PR が無い", result.stdout)

    def test_skip_does_not_hide_breaking_unrecognized_or_api_failures(self):
        self.inputs["skip-if-no-changes"] = "true"
        for title, body in (("feat!: change", ""), ("fix: change", "BREAKING CHANGE: change"), ("unknown", "")):
            with self.subTest(title=title, body=body):
                self.merged(title, body)
                result = self.run_action()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.output("skipped"), "false")
        self.state(fail_pr_list=True)
        self.assertNotEqual(self.run_action().returncode, 0)
        self.state(fail_pr_list=False, release_available=False)
        self.assertNotEqual(self.run_action().returncode, 0)

    def test_json_auto_bump_commits_all_manifests_and_returns_pr(self):
        self.use_json()
        self.inputs["skip-if-no-changes"] = "true"
        self.merged("feat: new feature")
        result = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.output("version"), "1.3.0")
        self.assertEqual(self.output("skipped"), "false")
        self.assertEqual(self.output("pr-number"), "42")
        self.assertEqual(self.output("pr-url"), "https://github.com/example/repo/pull/42")
        for path in self.paths:
            manifest = json.loads((self.repo / path).read_text())
            self.assertEqual(manifest["version"], "1.3.0")
            self.assertEqual(manifest["nested"], {"version": "9.0.0"})
            self.assertEqual(json.loads(self.git("show", f"origin/release/v1.3.0:{path}"))["version"], "1.3.0")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(self.git("log", "-1", "--format=%s").strip(), "release: v1.3.0")

    def test_patch_rules_and_existing_pr_are_preserved(self):
        self.merged("fix: bug")
        self.state(pr_state="OPEN")
        result = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(self.output("version"), "1.2.4")
        self.assertEqual(self.output("pr-number"), "42")
        self.assertTrue(any(call[:2] == ["pr", "edit"] for call in self.state()["calls"]))

    def test_explicit_version_and_bump_are_not_skipped(self):
        for values in ({"version": "2.0.0", "bump": "none"}, {"bump": "major"}):
            with self.subTest(values=values):
                self.inputs.update({"skip-if-no-changes": "true", **values})
                # このテストでは採番までを実行し、PR 作成前の戻り値を確認する。
                step = next(step for step in MANIFEST["runs"]["steps"] if step.get("id") == "resolve")
                result = self.run_step(step)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertEqual(self.output("version"), "2.0.0")
                self.assertEqual(self.output("skipped"), "false")
                self.inputs["version"] = ""

    def test_json_validation_stops_before_branch_or_file_changes(self):
        self.use_json()
        self.inputs.update({"version": "1.3.0", "bump": "none"})
        for invalid in ('{"version":"1.2.4"}', '{"version":"bad"}', '{"version":12}', '{}', '[]', 'bad json', '{"version":"1.2.3"}\n{"version":"1.2.3"}', '{"version":"1.2.3\\nwrong"}'):
            with self.subTest(invalid=invalid):
                (self.repo / self.paths[1]).write_text(invalid)
                before = (self.repo / self.paths[0]).read_bytes()
                result = self.run_action()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("::error::", result.stdout)
                self.assertEqual((self.repo / self.paths[0]).read_bytes(), before)
                self.assertEqual((self.repo / self.paths[1]).read_text(), invalid)
                self.assertEqual(self.git("branch", "--format=%(refname:short)").strip(), "main")
        (self.repo / self.paths[1]).unlink()
        self.assertNotEqual(self.run_action().returncode, 0)

    def test_npm_manual_release_ignores_skip_and_returns_created_pr(self):
        package = self.repo / "package.json"
        package.write_text(json.dumps({"name": "example-package", "version": "1.2.3"}) + "\n")
        self.git("add", "package.json")
        self.git("commit", "--quiet", "-m", "package")
        self.inputs.update({
            "version-bump-type": "npm", "bump": "patch", "skip-if-no-changes": "true",
        })
        result = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(package.read_text())["version"], "1.2.4")
        self.assertEqual(self.output("skipped"), "false")
        self.assertEqual(self.output("pr-number"), "42")
        self.assertTrue(any(call[:2] == ["pr", "create"] for call in self.state()["calls"]))

    def test_invalid_inputs_fail_before_api_calls(self):
        for values in ({"skip-if-no-changes": "yes"}, {"version-bump-type": "json", "json-version-files": ""}):
            with self.subTest(values=values):
                self.inputs.update(values)
                result = self.run_action()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state()["calls"], [])
                self.inputs["skip-if-no-changes"] = "false"
