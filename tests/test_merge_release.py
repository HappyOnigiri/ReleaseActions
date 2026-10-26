import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / 'actions/merge-release'
MANIFEST = yaml.safe_load((ACTION / 'action.yml').read_text())


class MergeReleaseTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='merge release test ')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.head = 'a' * 40
        self.log = self.root / 'calls.jsonl'
        (self.root / 'gh').write_text('''#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.MERGE_PROBE_LOG, JSON.stringify(args)+'\\n');
const head = 'a'.repeat(40);
if (args[0] === 'pr' && args[1] === 'view') {
  console.log(JSON.stringify({state:'OPEN',headRefOid:head,mergeable:'MERGEABLE'}));
} else if (args[0] === 'run' && args[1] === 'list') {
  if (process.env.MERGE_PROBE_BAD_JSON === 'true') { console.log('bad JSON'); process.exit(0); }
  const events = process.env.CI_EVENTS.trim().split('\\n');
  console.log(JSON.stringify(events.map((event, i) => ({databaseId:i+1,event,headSha:head,
    status:'completed',conclusion:process.env.MERGE_PROBE_FAILURE === 'true' ? 'failure' : 'success'}))));
} else if (args[0] === 'run' && args[1] === 'view') {
  console.log(JSON.stringify({jobs:[{name:process.env.CI_CHECK_NAME,status:'completed',conclusion:'success'}]}));
} else if (args[0] === 'pr' && args[1] === 'merge') {
  if (!args.includes('--match-head-commit') || args[args.indexOf('--match-head-commit')+1] !== head) process.exit(99);
} else if (args[0] === 'api') {
  console.log(process.env.MERGE_PROBE_SETTINGS || JSON.stringify({allow_merge_commit:true}));
} else process.exit(99);
''')
        (self.root / 'gh').chmod(0o755)
        self.inputs = {key: value.get('default', '') for key, value in MANIFEST['inputs'].items()}
        self.inputs.update({'pr-number':'42', 'head-sha':self.head, 'github-token':'test-token'})
        self.env = {**os.environ, 'PATH':str(self.root) + os.pathsep + os.environ['PATH'],
                    'MERGE_PROBE_LOG':str(self.log), 'GITHUB_STEP_SUMMARY':str(self.root / 'summary')}

    def run_action(self):
        step = MANIFEST['runs']['steps'][0]
        def resolve(value):
            match = re.fullmatch(r'\$\{\{ (.*) \}\}', value)
            if not match:
                return value
            key = match[1]
            if key == 'github.action_path':
                return str(ACTION)
            if key == 'github.repository':
                return 'example/repo'
            return self.inputs[key.removeprefix('inputs.')]
        return subprocess.run(['bash', '-c', step['run']], env={**self.env, **{
            key:resolve(value) for key,value in step['env'].items()
        }}, capture_output=True, text=True, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_success_waits_for_configured_ci_then_merges_pinned_head(self):
        self.inputs.update({'workflow':'verify.yml', 'events':'workflow_dispatch', 'check-name':'verify', 'merge-method':'rebase'})
        result = self.run_action()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        calls = self.calls()
        self.assertEqual(calls[-1], ['pr','merge','42','--repo','example/repo','--rebase','--match-head-commit',self.head])
        self.assertTrue(any(call[:2] == ['run','view'] for call in calls[:-1]))
        listing = next(call for call in calls if call[:2] == ['run','list'])
        self.assertEqual(listing[listing.index('--workflow')+1], 'verify.yml')
        self.assertEqual(listing[listing.index('--commit')+1], self.head)

    def test_auto_uses_an_allowed_repository_method(self):
        for method, settings in (("merge", {"allow_merge_commit": True}),
                                 ("squash", {"allow_squash_merge": True}),
                                 ("rebase", {"allow_rebase_merge": True})):
            with self.subTest(method=method):
                self.env["MERGE_PROBE_SETTINGS"] = json.dumps(settings)
                result = self.run_action()
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn("--" + method, self.calls()[-1])

    def test_failed_ci_and_invalid_api_response_never_reach_merge(self):
        for key in ('MERGE_PROBE_FAILURE', 'MERGE_PROBE_BAD_JSON'):
            self.env[key] = 'true'
            result = self.run_action()
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(call[:2] == ['pr','merge'] for call in self.calls()))
            del self.env[key]

    def test_invalid_inputs_never_reach_merge(self):
        for key, value in (('head-sha','short'), ('pr-number','0'), ('timeout-seconds','0'),
                           ('timeout-seconds','3601'), ('merge-method','invalid'), ('events',''),
                           ('events','push\npush'), ('workflow',''), ('check-name',''), ('github-token','')):
            with self.subTest(key=key, value=value):
                before = self.inputs[key]
                self.inputs[key] = value
                self.assertNotEqual(self.run_action().returncode, 0)
                self.assertFalse(any(call[:2] == ['pr','merge'] for call in self.calls()))
                self.inputs[key] = before
