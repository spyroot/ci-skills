# Review: GAL phase docs, CLAUDE.md and the approved plan

Scope: the documents Claude produced in this repository session:

- `CLAUDE.md`: repo root, local, ignored by `.gitignore:26`.
- The approved plan: `~/.claude/plans/snappy-imagining-raccoon.md`. Reviewer line numbers refer to
  this copy.
- The design. It and the plan were folded into `docs/phases/GAL-*.md` on 2026-10-02 because the
  instruction was "all should go to docs/ pr polish clear name not a zoo of doc".

The local task record is `.internal/plans/GAL-phases.md`.

Requested on 2026-10-02:

- Heisenberg checks correctness.
- Banach checks excess and duplication.
- The docs-structure curator (`~/.codex/agents/docs-structure-curator.toml`, run in review mode)
  checks structure.
- QA reviews the resulting edits.

Edits must be narrow, supported by these reviews, and keep the stronger wording.

The system design written later the same day was added to every reviewer's scope. Its content now
lives in `docs/phases/`.

| Reviewer | State | Section |
| --- | --- | --- |
| Structure (curator) | done, two passes | [Structure](#structure-review-docs-structure-curator) |
| Correctness (Heisenberg) | done; full report at `~/.claude/projects/-Users-spyroot-dev-ci-skills/1f71c81b-cfcd-43e7-b066-5671ffe5b9c3/tool-results/toolu_01TvLsRwtnk51yHJ5P9tFnod.txt` | [Correctness](#correctness-review-heisenberg) |
| Concision (Banach) | done | [Concision](#concision-review-banach) |
| QA of the phase-docs diff | done: P0=0, P1=0, P2=5, P3=3; fixed before PR #11 | [QA and the docs PR](#qa-and-the-docs-pr) |
| Decisions on each finding | recorded | [Decisions](#decisions) |

## Structure review (docs-structure-curator)

Read-only; no file changed. Standards read at the pinned revision `dca62de`.

### Corrections to the review brief

- `TEAM_GUIDE.md` exists: 161 lines, untracked, ignored by `.gitignore:28`, modified
  2026-10-02 01:02 local. It holds "Mandatory Shared Standards — Read First" and the local read
  order (`TEAM_GUIDE.md:1-24`). It is the binding's `projectAdditions` entry
  (`standards-binding.yaml:22-23`), so it is an existing canonical destination.
- Nothing is staged. `Makefile` is untracked (`??`). `.gitignore` and `standards-binding.yaml`
  were committed in `7930c2f`.
- `.internal/` holds only `gitlab.dcloud.run.token.env`. `MAP.md`, `INDEX.yaml`, `POLICY.md`,
  `STATE.md`, `queue/` and `plans/` were missing at review time.

### Classification of CLAUDE.md sections

| CLAUDE.md lines | Section | Class |
| --- | --- | --- |
| 1-6 | title, purpose, routing | project-specific stable routing; incomplete |
| 8-25 | Commands | project-specific stable rule and duplicate; line 23 stale |
| 27-32 | Architecture, layout | duplicate |
| 34-52 | Invocation flow | project-specific stable rule (code map) |
| 54-58 | Status semantics | duplicate, except the `core/runtime.py` sentence (57-58) |
| 60-72 | Single sources of truth | project-specific stable rule; 68-72 duplicate |
| 74-83 | Constraints that will fail the gate | project-specific stable rule; 79-83 duplicate |
| 85-90 | Hooks intro | roadmap or temporary plan; 88-90 mutable local state |
| 92-116 | Git hooks | roadmap or temporary plan; 94-96 machine-local; 97 stale ("staged Makefile") |
| 118-129 | Claude Code hooks | roadmap or temporary plan |

### Proposed move map (nothing goes to a public file)

| Source | Destination | Pointer left |
| --- | --- | --- |
| 8-25 Commands | `TEAM_GUIDE.md` (pinned `docs/agents/protocols/project-consumer.md`: "local commands and wrappers"). Unique lines only: 14 neutrality, 20 single test, 24-25 Markdown-only skip. Line 23 becomes a pointer to `validate.yml` | 1 line |
| 27-52 Architecture, flow | `TEAM_GUIDE.md` "Repository Structure" (139-161) as one section; drop 29 (duplicates `AGENTS.md:5`, `TEAM_GUIDE.md:143-146`) | 1 line |
| 54-58 Status | drop restatement; move 57-58 with architecture | 1 line: `core/status.py`, SKILL.md §4 |
| 60-72 Sources of truth | 62-67 with architecture; 68-72 → pointer to `references/access.md:138-150`, `../../tests/acceptance` | covered |
| 74-83 Constraints | `TEAM_GUIDE.md` ("stricter local additions"); merge 79-80 into `AGENTS.md:28` keeping `*.example.test` | 1 line |
| 85-129 Hooks proposal | own plan under `.internal/plans/`; 88-90 and 94-96 → `.internal/STATE.md` (missing); drop "staged" at 97 | 1 line |

Estimate: CLAUDE.md from 129 to about 12-15 lines.

### Missing pieces (curator contract ORDER)

| Item | In CLAUDE.md | Exists at | Authority |
| --- | --- | --- | --- |
| Shared Standards — Read First | no | `TEAM_GUIDE.md:1-24` | curator contract; pinned standards README:10-24 |
| Local authority and read order | partial (AGENTS.md, README only) | `TEAM_GUIDE.md:6-21` | pinned `agent-workspace.md:54-67` |
| Safety boundary (read-only default, write authorization) | no | `TEAM_GUIDE.md:86-116` | curator contract |
| Pointers to roadmap and mutable state | no | `../../tests/acceptance`; STATE.md, plans/ missing | pinned manifest |
| Local map | MAP.md, INDEX.yaml missing | — | pinned manifest `requiredForAgentOperation: true` |

No guide points to `TEAM_GUIDE.md`; `CLAUDE.md:5` sends an agent only to AGENTS.md and README.

### Duplicates (14 topics; the CLAUDE.md side becomes a pointer or is dropped)

Toolchain; validate commands; CI check list; layout; `tools.json` generated; option tiers;
target resolution order (owner `references/access.md` per `docs/field-notes.md:197-199`); gate
and publication bundle; output mode and provenance; status semantics; receipt provenance; secrets
and mocks (owner `AGENTS.md:28`); installer clean subtree; global commit-msg hook.

### Authority conflicts (BLOCKED until decided)

| # | Location A | Location B | Decision needed |
| --- | --- | --- | --- |
| C1 | `AGENTS.md:9` "Run validation commands in approved Kubernetes CI" | `CLAUDE.md:10-11`, `validate.yml:9` (`ubuntu-latest`), `expected.toml:18` (`required_checks = ["validate"]`) | which surface is authoritative for ci-skills |
| C2 | `CLAUDE.md:112` pre-push hook runs both pytest suites locally | pinned `agent-workspace.md:172` "Never run test on laptop, use CI, use allocate host." | drop the row, or a binding exception (`exceptions: []` today) |
| C3 | `CLAUDE.md:5` names AGENTS.md as the layout owner | pinned `project-consumer.md` gives structure and commands to `TEAM_GUIDE.md` | one owner |
| C4 | `TEAM_GUIDE.md:17-21` and curator `toml:48` (missing map: report the gap) | pinned `agent-workspace.md:3-6` and curator `toml:54-55` (stop / BLOCKER) | missing-map behavior |

SHARED STATE (C3, C4 are common-pattern candidates):

- **Blocker:** shared rules disagree on missing-map behavior; the pinned standards give no role
  to AGENTS.md or CLAUDE.md.
- **Category:** standards and contract consistency.
- **Evidence:**
  - curator `toml:48` against `toml:54-55`, and both against `agent-workspace.md:3-6`;
  - `dca62de` never mentions CLAUDE.md;
  - AGENTS.md appears only at `agent-workspace.md:51`.
- **Cross-project recurrence:** unknown; other repositories were out of scope.
- **Human action required:** decide C3 and C4.

### Stale facts

- `CLAUDE.md:23` "yamllint (workflow only)". `validate.yml:53` also lints
  `standards-binding.yaml`, added in `7930c2f`.
- `CLAUDE.md:97` "staged Makefile". The Makefile is untracked and nothing is staged.

### Wording to keep at full strength

- `CLAUDE.md:64` "never hand-edit it"
- `CLAUDE.md:72` "never from a mock or a hand edit"
- `CLAUDE.md:78` "Do not paste config from other projects"
- `CLAUDE.md:97-99` "Do not use … `install-hooks`", minus the word "staged"
- `CLAUDE.md:125-126` blocking hook rows
- Commands must sit under `AGENTS.md:9` (a laptop run is not authoritative) and the MUST rules at
  `TEAM_GUIDE.md:131-134`.

### Plan findings

- **Class and home.** The plan is a roadmap or temporary plan. Its home is `.internal/plans/`;
  the only authority for that folder is the global `~/.claude/CLAUDE.md`, since the pinned
  template has no `plans/`.
- **Queue item.** The queue item should reference the plan, not copy it.
- **Plan line 178** lists `CLAUDE.md` as a file a PR modifies. It is ignored and must never be
  committed, so replace it with a local-only step.
- **Plan lines 183-184 and 208-209** call `Makefile`, `standards-binding.yaml` and `.gitignore`
  "staged". That is stale.
- **Step 0** bootstraps `.internal/` while another agent is doing it. It should read back that
  bootstrap instead.
- **Overlap.** The hooks list (`CLAUDE.md:85-129`) and plan Step 5 overlap. Hooks are their own
  capability and belong in one hooks plan under `.internal/plans/`.
- **Agent-file hook.** The agent-file hook row's authority is the global `~/.claude/CLAUDE.md`
  ("Agent artifacts and data stay out of git"), not `validate` or AGENTS.md.

### Side observations (outside the scope; nothing touched)

- **README.md uncommitted edits** would fail `validate` if committed:
  - markdownlint-cli2 0.23.2 reports 8 findings on lines 18-28;
  - `git diff --check` flags lines 20 and 24;
  - line 23 contains a U+200B zero-width space.
- **`docs/field-notes.md:179-180`** says nothing lints `standards-binding.yaml`; `validate.yml:53`
  has linted it since `7930c2f`.
- **`TEAM_GUIDE.md:4`** uses an absolute `~/dev/standards`; the binding uses
  `~/dev/standards`.

## Correctness review (Heisenberg)

Read-only. Part 1 covers `CLAUDE.md` (25 edits, E1-E25 plus ES1); Part 2 covers the plan (19 edits,
PE1-PE19). P1 findings, with where each now stands:

| Finding | Disposition |
| --- | --- |
| `python` on PATH is 3.10; the checks need `tomllib` (E1, PE7) | applied: `CLAUDE.md` Commands; `environment.yml` in GAL-VENDOR |
| "the steps validate.yml runs" includes `render_manifest --check`, which is no step (E2, PE3) | applied: `CLAUDE.md`; GAL-HOOKS names the pytest counterpart |
| Receipt digest rule missing (E3, PE1) | applied: `CLAUDE.md` Constraints; GAL-ROUTING "Live receipt" |
| "staged Makefile" stale; `make` silently disables global hooks (E4) | applied: `CLAUDE.md` "Do not run `make` here" |
| "revision only claimed when clean" wrong (E5) | applied: `CLAUDE.md` step 4 |
| `tools/core/` collides with the skill's `core`; moving `tree_digest` breaks installed copies (PE2) | applied: GAL-VENDOR "Code placement" |
| wrapper must keep five names for the tests (PE4) | applied: GAL-CATALOG |
| `SKILL.md` must not point at a repo-only file (PE5, PE18) | applied: GAL-ROUTING installed-copies note; `glab` named as a skill |
| one branch for several PRs; stale base facts (PE6) | applied: one phase per PR; worktree off `origin/main` |

P2 and P3 findings:

- **Applied:** E6, E14, E15, E16, E17, E21, E22, E23, E24, E25, PE8, PE9, PE10, PE14 and PE19, in
  `CLAUDE.md` or the phase docs.
- **Moot:** E7-E12, E18-E20 and ES1 concerned the `CLAUDE.md` hooks section, which moved to
  GAL-HOOKS. Their substance was carried over there, each row with its CI counterpart or
  difference. PE11-PE13 and PE15-PE17 were plan details superseded by the phase docs.

Decisions it raised: D1-D6 (see `.internal/plans/GAL-phases.md`).

## Concision review (Banach)

Read-only. It proposed 16 `CLAUDE.md` edits (C1-C16, net -46 lines) and 16 plan edits (P1-P16,
net -23 lines).

| Finding | Disposition |
| --- | --- |
| C1 delete the `/init` prefix line | not applied: `/init` requires that prefix |
| C2 add `TEAM_GUIDE.md` to the owner map | applied |
| C3-C12, C14 replace restated declarations with pointers; fix the wrong claims | applied |
| C13 host paths are not gated | applied, wording kept strict |
| C15 move the hooks proposal out of `CLAUDE.md` | applied: now `docs/phases/GAL-HOOKS.md` (the instruction was docs/, not `.internal/plans/`) |
| C16 receipt rule | applied |
| P1 no render step in CI | applied: the runtime index needs no renderer |
| P4 import direction tools → skill only | applied: GAL-VENDOR |
| P5 receipt for the routing PR | applied: GAL-ROUTING |
| P6 keep safety rules in the `SKILL.md` router | applied: GAL-ROUTING step list |
| P7 install interface; `--force` | applied: no `--force`; refuse-to-overwrite kept |
| P8-P11, P14-P16 plan structure and drift | superseded by the phase docs |
| P12 workflow glob for the markdownlint exclusion | not applied: a committed `.markdownlint-cli2.yaml` instead, so local runs see it too (Heisenberg PE9); tested 2026-10-02, the config's `ignores` apply alongside CLI globs |
| P13 markdownlint counts | applied: `glab` 2, `glab-stack` 30 MD013, re-measured |

Side observations, all outside our documents and not acted on:

- The `README.md` working copy fails markdownlint.
- `docs/field-notes.md:180` is stale.
- The untracked `Makefile` carries the marker.

## Decisions

- **Applied.** Every finding marked applied above. The phase docs passed the CI-pinned
  markdownlint, `git diff --check`, the neutrality check and gitleaks on 2026-10-02.
- **Not applied, with reasons.** Banach C1 and P12, as recorded above.
- **Needs a decision.** D1-D7 in `.internal/plans/GAL-phases.md`, and curator conflicts C1-C4
  (C1 is D3, C2 resolved by GAL-HOOKS "tests run in CI", C3 and C4 are D6).

## Codex coordinator read-back, 2026-10-02

The four phase documents are staged, not committed, in the Claude worktree at
`nero/gal-phase-docs`. The current GitHub PR inventory has no phase-docs PR.
No finished QA report for this staged diff is recorded in this review file.

- **Required gate route (GAL-VENDOR, GAL-CATALOG, GAL-HOOKS):**
  `.github/workflows/validate.yml` runs `pytest` on `ubuntu-latest`.
  `AGENTS.md` requires authoritative tests in approved Kubernetes CI. The
  phase documents call the GitHub `validate` check sufficient without defining
  how that job runs on the approved test surface. Define the exact runner or
  dispatch route and read back the same PR head; keep the required check.
- **Live receipt (GAL-ROUTING):** `../../tests/acceptance` declares only
  `operator-laptop` at `mac.lan`, while `AGENTS.md` forbids laptop release
  evidence. Name an approved executor in the expectation contract and capture
  the fresh receipt there after the final skill edit. A mock or reused receipt
  cannot satisfy this gate.
- **Vendor update transaction (GAL-VENDOR):** the text promises that a failed
  multi-skill update leaves both skill trees and `vendor.lock.json` unchanged,
  but gives no commit or rollback order for those three paths. Specify the
  reusable transaction and failure recovery before implementing `update`.
- **Catalog file boundary (GAL-CATALOG):** `get NAME [PATH]` does not define
  how `PATH` is resolved or kept within the named skill. Define path
  containment and allowed files, then test traversal and unknown names.
- **Hook ownership (GAL-HOOKS):** the plan proposes laptop pre-commit static
  gates while the repository instruction forbids local gates. Its shared
  script has no defined interface or CI caller, so the promised hook/CI parity
  is not yet enforceable. State the exact advisory versus authoritative role,
  script arguments, CI wiring, and recovery behavior.
- **Optional decisions:** `orbit` remains excluded until its license is
  decided. JSON Schema for `skill_index` and `skill_vendor_lock` remains a
  documented design choice; no phase should claim those schemas already exist.

## QA and the docs PR

- **QA result:** P0=0, P1=0, P2=5, P3=3. It reviewed the 4-doc diff, ahead of the rewrite.
  All of the following were applied in the commit that opened PR #11.

  | Finding | Fix |
  | --- | --- |
  | F1 | per-file `sha256` is computed in skillkit; `tree_digest` gives only the tree |
  | F2 | the hand declarations moved to `skills/vendor.toml`; `update` preserves them; the lock is generated only |
  | F3 | `source` and reason tokens are defined once; steps renumbered; per-source index fields |
  | F4 | live acceptance moved to pre-push, which reads the working tree |
  | F5 | GAL-HOOKS gained a Block table, calls `./scripts/check.sh` like CI does, fails closed, and uses `git rev-parse --git-path hooks` |
  | F6 | size labels say "single-line JSON"; record sizes are measured |
  | F7 | all four `glab` verbs, and the two differences |
  | F8 | "Kept CI gates", and `depends_on` derived from `points_to` |

- **Later passes**, also applied:
  - **Banach second pass:**
    - R-c: `./scripts/check.sh` is a MUST for local runs and CI.
    - R-e: bounded retry before `BLOCKED`.
    - R-a and R-b, for `CLAUDE.md`, are still pending.
  - **Heisenberg final report**, applied in the phase docs:
    - P-E2/P-E3/P-E5/P-E6: the lock, lazy YAML import, per-file hashes, the transaction.
    - P-E8: the `glab` read-back runs outside CI.
    - P-E12: the capture command.
    - P-E13: matching semantics.
    - P-E15: the literal `references/access.md` link.
    - P-E17: source mapping.
    - P-E18: receipt expiry.
- **Codex record asks**, answered in the phase docs:
  - VENDOR: the multi-file update transaction; tests run in CI only.
  - CATALOG: `get PATH` containment and its tests.
  - ROUTING: the capture command; the receipt host goes to GAL-GATES G5.
  - HOOKS: the `./scripts/check.sh` interface, the CI caller, and static checks only.
- **PR:** https://github.com/spyroot/ci-skills/pull/11
  - head `7b0f9099202aa768bbabf69c569400ed8cfe5d8a`, 6 files under `docs/phases/`
  - `validate` `success` for that head (check-run API); mergeable, `CLEAN`
- **Merge** waits for Codex's six reviews (`.internal/queue/codex/active/gal-*-brainstorm.yaml`)
  and for the operator reading the docs.

## Open PR inventory (pr-coordinator), 2026-10-02

Read-only, from `gh pr list`, `gh pr view` and the check-run API.

| PR | Classification | Checks | Mergeability | Risk | Recommendation |
| --- | --- | --- | --- | --- | --- |
| #11 `nero/gal-phase-docs` | hold | `validate` pending on `4edb396`; green on earlier heads | MERGEABLE/CLEAN | docs only | apply Codex reviews, QA on final head, merge on operator go |
| #10 `nero/field-notes` | hold | `validate` pass | MERGEABLE/CLEAN | `docs/field-notes.md:180` stale (yamllint now covers the binding) | no review recorded for its exact head; operator decides |
| #2 `nero/ci-skills-delivery` (draft) | fix-first | none: 0 check runs on its head | CONFLICTING/DIRTY | overlaps root `README.md`, `.gitignore`, `standards-binding.yaml`; adds `scripts/check.sh` | rebase on `main` (after #10) so `validate` runs |
| #5 `neroshige/gal-19-node-diagnostics` | fix-first | `validate` fail: Live acceptance receipts | CONFLICTING/DIRTY | skill digest changed, no new receipt | rebase; capture a new receipt on the declared executor |
| #7 (draft, on #5) | hold | `validate` fail: Live acceptance receipts | MERGEABLE/UNSTABLE | same digest blocker | lands after #5 |
| #8 (draft, on #7) | hold | `validate` fail: Live acceptance receipts | MERGEABLE/UNSTABLE | same digest blocker | lands after #7 |
| #9 (draft, on #8) | hold | `validate` fail: Live acceptance receipts (inherited) | MERGEABLE/UNSTABLE | benchmarks only; failure comes from its base | lands after #8 |

**Effect on the GAL docs.** Applied in PR #11, head `4edb396`:

- #2's `scripts/check.sh` and `bin/ci-*` tools are reused, not duplicated.
- The tools from #5 and #7 are added.
- GAL-ROUTING lands after #5, #7 and #8, so one receipt covers every skill change.
- Short names are proposed: `skills/k8s-diag`, `bin/ci-skills`.

## Codex reviews: dispositions (applied in PR #11 head `b76c3a6`)

Reviews are in `.internal/brainstorm/GAL-*.md`. Before applying any finding we checked its key
claims against the code:

- `git rev-parse --git-path hooks` returns the global hook directory;
- `glab api` is called with `--method GET`;
- `kubectl auth can-i` is called;
- the pinned 21-item mutating matrix;
- the binding's `stricter-or-temporary-block-only` exception rule.

| Doc | Codex finding | Disposition |
| --- | --- | --- |
| PHASES | P1 HOOKS also needs VENDOR | applied: dependency table |
| PHASES | P1 GATES and VENDOR both change `validate.yml` | applied: GATES first, then VENDOR |
| PHASES | P1 review not enforced | open decision: GAL-GATES G3 |
| PHASES | P2 "only that phase's files" too narrow | applied: shared-file allowance |
| GATES | P1 G6 is not optional | applied: G6 required, no exception |
| GATES | P1 test route undefined | recorded: G8, open decision |
| GATES | P1 exact head needs two SHAs | applied: four commits named |
| GATES | P1 review has no merge control | open decision: G3 |
| GATES | P2 wrapper versus aggregator | applied: two routes; aggregator in G6 |
| VENDOR | P1 symlinks evade verify | applied: `symlink_unexpected` in staged and committed trees |
| VENDOR | P2 interrupted update | applied: transaction marker and recovery |
| VENDOR | P2 bootstrap order | applied: `environment.yml` is step 1 |
| VENDOR | P2 `glab skills list` parsing; envelope | applied: declared sources in `vendor.toml`; GAL-CLI envelope |
| CATALOG | P1 NAME containment | applied: names only from discovered map |
| CATALOG | P2 verify before vendored install | applied: `vendor_unverified` |
| CATALOG | P2 dependencies | applied: `dependency_missing` |
| CATALOG | P2 failure envelope | applied: GAL-CLI |
| ROUTING | P1 receipt executor | open decision: G5 |
| ROUTING | P2 dependency not installed | applied: refused until `glab` installed |
| ROUTING | P2 closed-world check | applied: `schemas` gate |
| HOOKS | P1 global hook directory | applied: install into `--git-common-dir`/hooks |
| HOOKS | P1 staged versus working tree | applied: index snapshot |
| HOOKS | P1 local gates versus route | applied: hooks advisory; `validate` is the gate |
| HOOKS | P2 installer contract | applied: GAL-CLI contract |
| TESTS | P1 full matrix | applied |
| TESTS | P1 idempotency misstated | applied: same digest no-op, different refused |
| TESTS | P1 hook installer checks | applied |
| TESTS | P1 pre-push fail-closed | applied |
| TESTS | P2 runner fixture | applied: `run_tool` |
| TESTS | P2 coverage route | applied: pinned coverage.py in the tests gate |
| TESTS | P2 agent hooks | applied: outside the deliverable |
| REFERENCE | P1 commands are not read-only | applied: operations with method, argv and `mutates` |
| REFERENCE | P1 live check route | open decision: executor |
| REFERENCE | P1 no test or order slot | applied: order step 6; GAL-TESTS section |
| REFERENCE | P2 `__complete` registry | applied: bounded, optional navigation |
| REFERENCE | P2 missing `auth can-i` | applied |
| REFERENCE | P2 `tools` contract and digest | applied: GAL-CLI contract; digest of the executable |

Our own additions in the same revision, from the 2026-10-02 requests:

- GAL-SCHEMA: the schemas, validation tool, version promotion and pointers.
- GAL-CLI: the common CLI contract and its interface gate.
- The discovery walk and read in GAL-CATALOG.
- G7, the scheduled check of `main`.
