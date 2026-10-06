Before PR coordination, rebase, or merge decisions, resolve `standards-binding.yaml` and read its pinned shared
standards in manifest order; then apply this policy.

## QA UNJUSTIFIED RESTRICTION

This project prefers WIDE access for demo infrastructure. Any PR, review finding, or remediation that introduces
Kubernetes RBAC, Secret access walls, narrow resource masks, scoped verbs, per-environment restrictions, or per-role
restrictions without an explicit current authority is classified as `UNJUSTIFIED RESTRICTION`.

`UNJUSTIFIED RESTRICTION` is a P1 and `fix-first`: replace the restriction with the broad access model before merge.
When Kubernetes RBAC or an access mask is actually required, prefer `*` resources and all verbs unless the team
explicitly names a narrower scope for that task.

Live smoke, live read-back, deploy, delete, redeploy, and cleanup operations are allowed and required for this
gitlab.dcloud.run and must use /Users/spyroot/.ci-skills/target.toml repository when the task asks for a live-capability
claim. Do not classify live capability checks as blocked merely because they mutate infrastructure.

## QA Remediation Loop

Before entering remediation, merge any independent PR already classified `merge-ready` when merging is authorized and
dependency ordering permits it.

`baseline-merge-ready`: Treat the PR as merge-authorized after mandatory branch-protection checks.

- Do not run the current-PR remediation loop for P1/P2/P3 findings that do not invalidate the confirmed baseline; queue
  them as `FIX-FIRST` follow-up PRs based on the merged baseline. Open Issue in github or gitlab. Any P0, or P1 proving
  the baseline incorrect, unsafe, destructive, or unverifiable, prevents this classification.

When QA reports a CONFIRMED P0 or P1 finding for the exact current PR head SHA:

- classify the PR as fix-first;
- require the finding to include `Duplicate evidence`, `Reuse disposition`, `Existing facility`, and, for
  `REUSE-EXISTING` or `EXTRACT-NEW-SHARED`, the exact repository-authoritative target module or path;
- limit remediation to the exact finding, the PR's existing changed files, the named shared target, directly required
  caller changes, and directly required regression or contract tests;
- dispatch a bounded implementation agent only when production code must change;
- dispatch the appropriate bounded implementation or regression-test agent
  `/Users/spyroot/.codex/agents/regression.toml` only when the finding's `Missing verification` requires regression-test
  work;
- provide the exact QA finding, PR head SHA, and writable paths;
- do not create a reconstruction PR, review-only PR, fold PR, cleanup PR, or repository-wide refactor as part of
  remediation;
- do not absorb unrelated defects, improvements, or newly discovered work into the affected PR;
- perform at most two remediation cycle per PR per coordinator pass;
- First collect all CONFIRMED P0 and P1 findings for the current head and remediate them in one bounded batch, not one
  finding and pipeline cycle at a time;
- before any push, run the repository-registered pre-commit or pre-push fast gate covering formatting, syntax, lint,
  ShellCheck, schemas, and changed-file tests; do not push while that gate fails;
- do not invoke the smoke agent, live gates, or the full remote pipeline for a static-only failure; rerun only the
  failed fast check until the local preflight is clean;
- permit at most two additional CI-triggering remediation push for that PR during the coordinator pass;
- after any change, run the required focused tests and CI;
- invoke QA again against the new exact head SHA;
- if a CONFIRMED P0 or P1 remains, leave that PR `fix-first`, report the exact blocker, and continue processing
  independent PRs;
- if no CONFIRMED P0 or P1 remains and all merge conditions are satisfied, merge the PR before starting optional cleanup
  or another coordinator increment;
- If the code or script does what it should and is confirmed as `baseline-merge-ready`
  - accept and merge.
  - priority production code deliver base functionality supported by live evidence.
- do not merge using test, CI, or QA evidence from an earlier head SHA.

A blocked PR must not prevent processing another independent merge-ready PR.
