#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

# prepare-release の外部 API だけを置き換え、Git 操作は使い捨てリポジトリで実行する。
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
state["calls"].append(args)


def finish(code=0, output=""):
    state_path.write_text(json.dumps(state))
    if output:
        print(output)
    sys.exit(code)


if args[:1] == ["api"]:
    finish(output='{"permissions":{"push":true}}')
if args[:2] == ["release", "view"]:
    if not state["release_available"] or args[2].startswith("v"):
        finish(1, "release unavailable")
    if "tagName" in args:
        finish(output="v1.2.3")
    finish(output="2026-09-01T00:00:00Z")
if args[:2] == ["pr", "list"]:
    if state.get("fail_pr_list"):
        finish(1, "cannot list PRs")
    finish(output=json.dumps(state["merged_prs"]))
if args[:2] == ["pr", "view"]:
    if state["pr_state"] is None:
        finish(1, "PR unavailable")
    if "--jq" in args:
        finish(output=state["pr_state"])
    finish(output=json.dumps({"number": 42, "url": "https://github.com/example/repo/pull/42"}))
if args[:2] == ["pr", "create"]:
    state["pr_state"] = "OPEN"
    finish(output="https://github.com/example/repo/pull/42")
if args[:2] in (["pr", "edit"], ["label", "create"]):
    finish()
finish(2, "unexpected gh arguments")
