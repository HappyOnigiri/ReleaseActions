import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { setTimeout as pause } from "node:timers/promises";
import { pathToFileURL } from "node:url";

const defaults = { events: ["push", "pull_request"], checkName: "checks" };
function record(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function pullRequest(value, head) {
  if (
    !record(value) ||
    value.state !== "OPEN" ||
    value.headRefOid !== head ||
    typeof value.mergeable !== "string" ||
    !["MERGEABLE", "UNKNOWN", "CONFLICTING"].includes(value.mergeable)
  )
    throw new Error("Release PR is not open at the expected commit, or its response is invalid");
  if (value.mergeable === "CONFLICTING") throw new Error("Release PR has merge conflicts");
  return value.mergeable;
}
function runs(value) {
  if (!Array.isArray(value)) throw new Error("Invalid CI run response");
  return value.map((item) => {
    if (
      !record(item) ||
      typeof item.databaseId !== "number" ||
      !Number.isSafeInteger(item.databaseId) ||
      item.databaseId <= 0 ||
      typeof item.event !== "string" ||
      typeof item.headSha !== "string" ||
      typeof item.status !== "string" ||
      typeof item.conclusion !== "string"
    )
      throw new Error("Invalid CI run response");
    return {
      databaseId: item.databaseId,
      event: item.event,
      headSha: item.headSha,
      status: item.status,
      conclusion: item.conclusion,
    };
  });
}
function checkJobs(value, id, checkName) {
  if (!record(value) || !Array.isArray(value.jobs)) throw new Error("Invalid CI jobs response");
  const checks = value.jobs.filter((job) => record(job) && job.name === checkName);
  if (
    checks.length !== 1 ||
    !checks.every(
      (job) => record(job) && job.status === "completed" && job.conclusion === "success",
    )
  )
    throw new Error(`CI run ${id} does not have one successful ${checkName} job`);
}

// Every configured event must finish for the expected commit. Missing checks are never success.
export async function waitForReleaseChecks(
  head,
  reader,
  timeoutMs = 600_000,
  intervalMs = 5_000,
  selection = defaults,
) {
  if (!/^[0-9a-f]{40}$/u.test(head)) throw new Error("Expected a full release commit SHA");
  const deadline = reader.now() + timeoutMs;
  const checkDeadline = () => {
    if (reader.now() >= deadline) throw new Error("Timed out waiting for release CI");
  };
  for (;;) {
    checkDeadline();
    const mergeable = pullRequest(reader.pullRequest(), head);
    const candidates = runs(reader.runs()).filter((run) => run.headSha === head);
    let ready = true;
    for (const event of selection.events) {
      // A newer run supersedes an earlier run for the same event and commit.
      const run = candidates
        .filter((candidate) => candidate.event === event)
        .sort((a, b) => b.databaseId - a.databaseId)[0];
      if (!run || run.status !== "completed") {
        ready = false;
        continue;
      }
      if (run.conclusion !== "success")
        throw new Error(`Release CI ${event} run ${run.databaseId}: ${run.conclusion}`);
      checkJobs(reader.jobs(run.databaseId), run.databaseId, selection.checkName);
    }
    checkDeadline();
    if (ready && mergeable === "MERGEABLE") {
      const latest = pullRequest(reader.pullRequest(), head);
      checkDeadline();
      if (latest === "MERGEABLE") return;
    }
    await reader.sleep(Math.min(intervalMs, deadline - reader.now()));
  }
}

if (import.meta.url === pathToFileURL(resolve(process.argv[1] ?? "")).href) {
  const [pr, head] = process.argv.slice(2);
  const repo = process.env.GH_REPO;
  if (!pr || !/^[1-9][0-9]*$/u.test(pr) || !head || !repo)
    throw new Error("Usage: GH_REPO=owner/repo node wait-checks.mjs <PR> <SHA>");
  const workflow = process.env.CI_WORKFLOW ?? "ci.yml";
  const checkName = process.env.CI_CHECK_NAME ?? "checks";
  const events = (process.env.CI_EVENTS ?? "push\npull_request")
    .split("\n")
    .map((v) => v.trim())
    .filter(Boolean);
  if (!workflow.trim() || !checkName.trim())
    throw new Error("workflow and check-name must not be empty");
  const timeoutSeconds = Number(process.env.WAIT_TIMEOUT_SECONDS || "600");
  if (
    !events.length ||
    new Set(events).size !== events.length ||
    events.some((v) => !/^[a-z_]+$/u.test(v))
  )
    throw new Error("CI events must be unique event names, one per line");
  if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 3600)
    throw new Error("timeout-seconds must be an integer from 1 through 3600");
  const gh = (args) =>
    JSON.parse(
      execFileSync("gh", [...args, "--repo", repo], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "inherit"],
        timeout: 30_000,
      }),
    );
  await waitForReleaseChecks(
    head,
    {
      pullRequest: () => gh(["pr", "view", pr, "--json", "state,headRefOid,mergeable"]),
      runs: () =>
        gh([
          "run",
          "list",
          "--workflow",
          workflow,
          "--commit",
          head,
          "--limit",
          "100",
          "--json",
          "databaseId,event,headSha,status,conclusion",
        ]),
      jobs: (id) => gh(["run", "view", String(id), "--json", "jobs"]),
      now: Date.now,
      sleep: pause,
    },
    timeoutSeconds * 1000,
    5000,
    { events, checkName },
  );
  console.log(`Release checks passed for ${head}`);
}
