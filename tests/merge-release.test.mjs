import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { waitForReleaseChecks } from "../actions/merge-release/wait-checks.mjs";

const head = "a".repeat(40);
const oldHead = "b".repeat(40);
const run = (databaseId, event, conclusion = "success") => ({
  databaseId,
  event,
  headSha: head,
  status: conclusion ? "completed" : "in_progress",
  conclusion,
});
const successfulRuns = () => [run(1, "push"), run(2, "pull_request")];
const successfulJobs = () => ({
  jobs: [{ name: "checks", status: "completed", conclusion: "success" }],
});
function reader() {
  return {
    time: 0,
    sleeps: 0,
    reads: 0,
    pullRequest: () => ({ state: "OPEN", headRefOid: head, mergeable: "MERGEABLE" }),
    runs: () => successfulRuns(),
    jobs: (_id) => successfulJobs(),
    now() {
      return this.time;
    },
    async sleep(ms) {
      this.sleeps++;
      this.time += ms;
    },
  };
}

await test("release waits for registration and completion of both final-commit CI events", async () => {
  const source = reader();
  source.runs = () => {
    source.reads++;
    if (source.reads === 1) return [];
    if (source.reads === 2) return [run(1, "push", "")];
    return successfulRuns();
  };
  await waitForReleaseChecks(head, source, 50, 5);
  assert.equal(source.sleeps, 2);
});

await test("old commits and missing PR CI never satisfy the wait", async () => {
  for (const candidates of [
    successfulRuns().map((candidate) => ({ ...candidate, headSha: oldHead })),
    [run(1, "push")],
    [],
  ]) {
    const source = reader();
    source.runs = () => candidates;
    await assert.rejects(waitForReleaseChecks(head, source, 10, 5), /Timed out/);
    assert.equal(source.time, 10);
  }
});

await test("failed, cancelled, timed-out, skipped and neutral CI stop the release", async () => {
  for (const conclusion of ["failure", "cancelled", "timed_out", "skipped", "neutral"]) {
    const source = reader();
    source.runs = () => [run(1, "push"), run(2, "pull_request", conclusion)];
    await assert.rejects(waitForReleaseChecks(head, source), new RegExp(conclusion, "u"));
    assert.equal(source.sleeps, 0);
  }
});

await test("successful runs must contain one successful checks job", async () => {
  for (const jobs of [
    [],
    [{ name: "other", status: "completed", conclusion: "success" }],
    [{ name: "checks", status: "completed", conclusion: "skipped" }],
    [{ name: "checks", status: "in_progress", conclusion: "" }],
    [...successfulJobs().jobs, ...successfulJobs().jobs],
  ]) {
    const source = reader();
    source.jobs = () => ({ jobs });
    await assert.rejects(waitForReleaseChecks(head, source), /successful checks job/);
  }
});

await test("latest run for each event supersedes old attempts", async () => {
  const source = reader();
  source.runs = () => [run(1, "push", "failure"), run(3, "push"), run(2, "pull_request")];
  await waitForReleaseChecks(head, source);
  source.runs = () => [run(1, "push"), run(3, "push", "cancelled"), run(2, "pull_request")];
  await assert.rejects(waitForReleaseChecks(head, source), /cancelled/);
});

await test("head changes before or during CI verification stop the release", async () => {
  for (const changeDuringJobs of [false, true]) {
    const source = reader();
    let current = changeDuringJobs ? head : oldHead;
    source.pullRequest = () => ({ state: "OPEN", headRefOid: current, mergeable: "MERGEABLE" });
    source.jobs = () => {
      current = oldHead;
      return successfulJobs();
    };
    await assert.rejects(waitForReleaseChecks(head, source), /expected commit/);
  }
});

await test("GitHub mergeability computation is awaited and conflicts stop the release", async () => {
  const source = reader();
  source.pullRequest = () => ({
    state: "OPEN",
    headRefOid: head,
    mergeable: source.sleeps ? "MERGEABLE" : "UNKNOWN",
  });
  await waitForReleaseChecks(head, source, 50, 5);
  assert.equal(source.sleeps, 1);
  source.pullRequest = () => ({ state: "OPEN", headRefOid: head, mergeable: "CONFLICTING" });
  await assert.rejects(waitForReleaseChecks(head, source), /merge conflicts/);
  source.pullRequest = () => ({ state: "CLOSED", headRefOid: head, mergeable: "MERGEABLE" });
  await assert.rejects(waitForReleaseChecks(head, source), /not open/);
});

await test("API errors, invalid responses and expired final verification never succeed", async () => {
  const source = reader();
  source.runs = () => {
    throw new Error("GitHub unavailable");
  };
  await assert.rejects(waitForReleaseChecks(head, source), /GitHub unavailable/);
  for (const invalid of [null, {}, [null], [{ databaseId: "1" }]]) {
    source.runs = () => invalid;
    await assert.rejects(waitForReleaseChecks(head, source), /Invalid CI run/);
  }
  source.runs = successfulRuns;
  source.jobs = () => ({ jobs: null });
  await assert.rejects(waitForReleaseChecks(head, source), /Invalid CI jobs/);
  source.jobs = () => {
    source.time = 10;
    return successfulJobs();
  };
  await assert.rejects(waitForReleaseChecks(head, source, 10, 5), /Timed out/);
  await assert.rejects(waitForReleaseChecks("short-sha", reader()), /full release commit SHA/);
});

await test("release checker CLI queries ci.yml for the pinned head and never writes to GitHub", async (t) => {
  const work = await mkdtemp(join(tmpdir(), "cerberus-release-checks-"));
  t.after(() => rm(work, { recursive: true, force: true }));
  const log = join(work, "calls.jsonl");
  await writeFile(
    join(work, "gh"),
    `#!/usr/bin/env node
const fs = require('node:fs');
const args = process.argv.slice(2);
fs.appendFileSync(process.env.RELEASE_PROBE_LOG, JSON.stringify(args)+'\\n');
if (args[0] === 'pr' && args[1] === 'view') {
  console.log(JSON.stringify({ state:'OPEN', headRefOid:'${head}', mergeable:'MERGEABLE' }));
} else if (args[0] === 'run' && args[1] === 'list') {
  console.log(JSON.stringify(${JSON.stringify(successfulRuns())}));
} else if (args[0] === 'run' && args[1] === 'view') {
  console.log(JSON.stringify(${JSON.stringify(successfulJobs())}));
} else process.exit(99);
`,
    { mode: 0o755 },
  );
  const result = spawnSync(
    process.execPath,
    [resolve("actions/merge-release/wait-checks.mjs"), "31", head],
    {
      env: {
        ...process.env,
        PATH: `${work}:${process.env.PATH}`,
        GH_REPO: "example/repo",
        RELEASE_PROBE_LOG: log,
      },
      encoding: "utf8",
      timeout: 30_000,
    },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Release checks passed/);
  const calls = (await readFile(log, "utf8"))
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line));
  const list = calls.find((args) => args[0] === "run" && args[1] === "list");
  assert.ok(list);
  assert.equal(list[list.indexOf("--workflow") + 1], "ci.yml");
  assert.equal(list[list.indexOf("--commit") + 1], head);
  assert.ok(calls.every((args) => args[args.indexOf("--repo") + 1] === "example/repo"));
});
