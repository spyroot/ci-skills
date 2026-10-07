# CI-REFERENCE adversarial review

Target: PR #11, head `e60cc60613ddf0f606938984312cbed29edb8515`.
The `validate` check passed for this head. This reviews the proposed plan;
its adapter, declaration and gate have not been implemented.

## Findings

- **P1 — Command paths do not establish read-only behavior.** Lines 33-39,
  63 and 172-176 list `gh api`, `glab api` and `kubectl exec` among the
  skill's read capabilities, then propose one `mutates` bit per command.
  Both API commands can issue POST requests; `kubectl exec` runs arbitrary
  commands. Model the allowed operation as the command plus HTTP method,
  endpoint class and relevant flags, or the exact argv after `--`; classify
  unbounded API/exec calls as potentially mutating. Current code explicitly
  sets `glab api --method GET` (`access.py:56-65`) and bounds the exec
  payload (`access.py:615-627`). See the
  [gh API manual](https://cli.github.com/manual/gh_api),
  [glab API manual](https://docs.gitlab.com/cli/api/) and
  [kubectl exec reference](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_exec/).
- **P1 — The installed-binary check has no required execution route.** Lines
  143, 228-231 and 262-263 check old recorded fixtures in CI but leave
  the live check's location as action item 6. A tool upgrade could pass
  fixture CI while removing a used flag. Define the approved executor,
  exact PR-head and binary version/digest evidence, required status, and
  failure behavior before implementation. A fixture check remains a parser
  test, not installed-binary compatibility proof.
- **P1 — This phase has no test or order slot.** `CI-PHASES.md:15` adds
  CI-REFERENCE, but its order stops at CI-HOOKS (`:19-27`) and its merge
  rule requires the tests CI-TESTS lists for each phase (`:29-37`).
  CI-TESTS has no CI-REFERENCE section. Specify this phase's dependency,
  test cases and required gate before claiming it can land.
- **P2 — `__complete` is not a complete command registry.** Lines 28-31
  treat each line as a command or flag. Cobra also emits argument choices,
  active-help entries and a terminal directive; flag discovery depends on
  the input prefix, and completion callbacks may execute. Define bounded
  queries, parsing, directive/error handling, timeout/output limits and
  exclusion of argument values. Test real recorded output plus hostile
  fixtures. See
  [Cobra's completion guide](https://github.com/spf13/cobra/blob/main/site/content/completions/_index.md).
- **P2 — The current capability inventory is incomplete.** Lines 33-39
  omit `kubectl auth can-i`, called repeatedly in `access.py:539-599`.
  Derive the declared invocation list from every current call site before
  claiming read-only coverage.
- **P2 — The new agent-facing verb lacks an output contract.** Lines 232-234
  give `tools/ci_skills.py tools` no JSON schema, status/exit codes,
  bounded output or safe next step for a missing binary. The pinned
  `agent-grade-tools.md` requires those. The example at lines 69-84 also
  omits the defined `digest`; specify whether it hashes the executable or
  source text and how the version is read.

## Smaller path and proof

Refine option D into a declaration of exact operations and used flags, then
require a live contract check on the approved executor. Treat option C's
completion crawl as optional navigation until its protocol and safety are
proven. In the gate, assert no unbounded API/exec call is labeled read-only,
every declared operation resolves on the pinned installed binary, and every
required flag survives an upgrade. No local test or live command was run for
this review.
