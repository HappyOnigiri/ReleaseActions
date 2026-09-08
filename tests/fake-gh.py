#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

# GitHub を変更せず、部分アップロードと公開失敗を再現する。
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]
state["calls"].append(args)


def finish(code=0, output=""):
    state_path.write_text(json.dumps(state))
    if output:
        print(output)
    sys.exit(code)


if args[:2] == ["api", "repos/example/repo"]:
    finish(output='{"permissions":{"push":true}}')
if args[:3] == ["api", "-X", "DELETE"]:
    if os.environ.get("FAIL_DELETE"):
        finish(1, "branch deletion failed")
    state["branch_deleted"] = True
    finish()
if args[:1] != ["release"]:
    finish(2, "unexpected gh arguments")

operation = args[1]
release = state["release"]
if operation == "view":
    if release is None or os.environ.get("FAIL_VIEW"):
        finish(1, "release unavailable")
    finish(output=str(release["draft"]).lower())
if operation == "create":
    if release is not None or os.environ.get("FAIL_CREATE"):
        finish(1, "cannot create release")
    release = {"draft": "--draft" in args, "assets": {}, "notes": ""}
    state["release"] = release
elif release is None:
    finish(1, "release missing")
elif operation == "upload":
    if not release["draft"]:
        finish(2, "published assets must not be changed")
    for value in args[3:]:
        if value == "--clobber":
            continue
        path = Path(value)
        release["assets"][path.name] = path.read_text()
        if os.environ.get("FAIL_UPLOAD"):
            finish(1, "partial upload")
elif operation == "edit":
    if "--draft=false" in args:
        if os.environ.get("FAIL_PUBLISH"):
            finish(1, "publish failed")
        release["draft"] = False
elif operation not in ("create", "edit"):
    finish(2, "unexpected release operation")

if "--notes-file" in args:
    release["notes"] = Path(args[args.index("--notes-file") + 1]).read_text()
if not release["draft"]:
    state["latest"] = args[2]
finish()
