#!/usr/bin/env python3
"""Select merged PRs by commit reachability, independent of publication time."""
import json
import os
import re
import subprocess
import sys

base, target = sys.argv[1:]
revision_args = [target] + ([f"^{base}"] if base else [])
commits = set(subprocess.check_output(
    ["git", "rev-list", *revision_args], text=True,
).splitlines())
pages = json.loads(subprocess.check_output([
    "gh", "api", "--paginate", "--slurp",
    f"repos/{os.environ['GITHUB_REPOSITORY']}/pulls?state=closed&base=main&per_page=100",
], text=True))
for page in pages:
    for pr in page:
        if pr.get("merged_at") and not re.fullmatch(r"[0-9a-f]{40}", pr.get("merge_commit_sha") or ""):
            raise ValueError(f"Merged PR #{pr.get('number')} has no valid merge commit SHA")
prs = [pr for page in pages for pr in page
       if pr.get("merged_at") and pr.get("merge_commit_sha") in commits
       and not re.match(r"^release(\(.+\))?!?:", pr["title"])]
prs.sort(key=lambda pr: (pr["merged_at"], pr["number"]))
if os.environ.get("RELEASE_NOTES") == "true":
    for pr in prs:
        print(f"- [#{pr['number']}]({pr['html_url']}) {pr['title']}")
else:
    print(json.dumps(prs))
