# Plan: GitLab CI YAML keyword reference loaded on demand, and finishing the CIxx phase-doc re-adjustment

Repository: `ci-skills` (branch `main` = `origin/main` at `e85c7c8`, clean).
Mode: PLAN (read-only). Nothing below has been applied. Every claim carries how it was verified.

## Context

The operator asked for three things:

1. A proposal for a reference to <https://docs.gitlab.com/ci/yaml/#triggerforward> that a skill can
   load on demand, grounded in the Claude and OpenAI (Codex) skill documentation.
2. A check of `docs/` and the queue after the move to the new `CIxx-NAME` doc format: the
   filenames were renamed, the bodies were not; each phase doc still lacks grounded detail
   (concrete algorithms, load/knowledge/memory/skills optimisation), and many decisions are open.
3. The docs must reflect two decisions the operator already made in the tree: one layout
   (`ci-skills/bin` thin mains, `ci-skills/lib/core` Python library, `ci-skills/lib/bash/*` Bash
   libraries) and "no dual implementation; every tool is a small main that calls the library".

"Memory" and "lazy loading" mean indexing pointers, tags and schema locations so a caller loads only what it needs.

## Verified findings that shape the plan

### F1. The merge gate named everywhere no longer exists (report, do not resolve)

- [fact: `git log -1 1108cca`] commit `1108cca` (2026-10-04 18:02 +0100) "Delete .github/workflows/validate.yml",
  119 lines removed. `git ls-files | grep '^.github'` is empty.
- [fact: `gh api repos/spyroot/ci-skills/contents/.github/workflows`] 404.
  [fact: `gh api .../branches/main/protection`] "Branch protection has been disabled on this repository".
  [fact: `gh run list --workflow validate`] "could not find any workflows named validate".
- Still naming `validate` as the gate: `.coordination/pr-coordinator-policy.md:4-5`,
  `docs/phases/CI03-GATES.md:11-13`, and `tests/acceptance/expected.toml:26`.
- No local gate functions either: [fact: `ls`] `bless.sh:15-45` sources `automation/lib/*` (missing),
  `scripts/check.sh:6` sources `lib/ci/check.bash` (deleted on `main` in `88bd9f6`, merged via PR #26),
  `Makefile:26-27,10` reference `lib/bash/toolchain/jobs.bash` and `scripts/toolchain/install.sh` (missing),
  `.githooks` absent, `git config --local core.hooksPath` unset. `Makefile:1-2` says it is a copy from another repo,
  not yet adapted. `scripts/bash/core/{install.sh,conda.sh}` are executables inside a library directory that source
  a missing `scripts/lib/bash/toolchain/*.bash`; `scripts/bash/core/{basic_auth,digest,exit_codes,result}.bash` have
  no caller anywhere in the tree [fact: layout audit, Grep].
- The deleted workflow (read with `git show 1108cca^:.github/workflows/validate.yml`) ran: classify changed paths,
  `git diff --check`, actionlint 1.7.12, yamllint 1.38.0, markdownlint-cli2 0.23.2, gitleaks 8.30.1, neutrality,
  then (non-Markdown changes only) pip install, `bash -n`/shellcheck/shfmt over a hard-coded list of the OLD
  `skills/ci-skills/...` paths, `bats --tap tests/*.bats` (the files are now `tests/bash/*.bats`), `ruff check`,
  `ruff format --check`, the installed-package smoke and pytest, and finally the unconditional live-acceptance check.
  Restoring it verbatim would fail on the shell list, the bats glob and the old test paths.
- Consequence: the pinned standard `agent-workspace.md:172` ("Never run test on laptop, use CI") plus no CI means
  there is currently NO surface on which the Python test suite runs. Every "tests pass" claim in this plan is
  BLOCKED until the operator names the gate route (open decision D-GATE).
- The committed live receipts are already stale, independent of any new work [fact: gates design agent ran
  `tools/check_live_acceptance.py --root . --expected tests/acceptance/expected.toml --receipts tests/acceptance/receipts
  --skill ci-skills --json` read-only]: the current `ci-skills/` digest `a3f92c5a...` over 62 files differs from the
  receipt's `62c951b7...` over 53, so the checker reports BLOCKED with 18 problems (17 `skill_digest_mismatch`, one
  `runner_smoke_cleanup_unproven`). The expiry the docs quote (2026-11-01, `CI03-GATES.md:114`, `CI05-VENDOR.md:226`)
  belongs to a superseded receipt; the committed one expires 2026-11-03T03:47:04Z.
- No new receipt can be captured while protection is disabled: `ci-skills/lib/core/access.py:261-283` reads
  `main`'s required status checks and blocks on the 404, and `:292-310` compares them with `github.required_checks`
  in the target file (`tests/acceptance/expected.toml:26` names `validate`). So D-GATE precedes every receipt.

### F2. The binding requires contracts the pinned revision does not define (report)

- [fact: `standards-binding.yaml:11`] pins `dca62dec9bdb93af1c75aa58504f3ea2463e186c`.
- [fact: `git -C ~/dev/standards show dca62de:manifest.yaml`] `readOrder` ids at that revision:
  ci, unit-testing, smoke-testing, blockers (`docs/agents/contracts/blockers.md`), agent-workspace, automation,
  project-consumer, provider-escalation.
- [fact: `standards-binding.yaml:21-25`] requires `bash-shell-standards`, `helm-ci-standards`,
  `policy-authority-standards`, `software-design`, none defined at the pinned revision; `automation` is listed
  twice (`:18`, `:24`). At `~/dev/standards` HEAD (`56a579c`) those contracts exist and `blockers.md` was renamed
  `blockers-contract.md`.
- [fact: standards audit, schema read at both revisions] the binding validates against NO revision's schema: the
  pinned `schemas/project-standards-binding.schema.json@dca62de:62-77` requires exactly 8 ids (it rejects entries
  9-13), the HEAD schema requires exactly 15 ids in order (it rejects the duplicate `automation` at position 12 and
  the omitted `documentation-contract`, `documentation-rules`, `python-standards`). The standards repo's own
  `standards-binding.yaml` fails its HEAD schema too, so `templates/standards-binding.yaml:10-25` at HEAD is the
  usable reference when the operator bumps the pin.
- Resolution (bump the pin to a revision whose manifest defines every required id, then list all ids in schema
  order) is the operator's; this plan reads the HEAD contracts the operator evidently intends
  (`software-design.md`, `documentation-contract.md`, `python-standards-contract.md`,
  `bash-shell-standards-contract.md`) and tags rules from them `[HEAD-only]` where it relies on them.
- Two more contradictions inside the standards, reported not resolved: `agent-workspace.md:198-199` ("always lint,
  shellcheck, and code-check locally") against `:200` ("Never run test on laptop"), which leaves offline pytest/bats
  ambiguous; and `automation.md:177-191` (9 Bash doc fields) against `documentation-contract.md:33-35` (5 fields,
  fixed order), which `scripts/bash/core/result.bash:20-25` follows the first of.

### F3. The layout move landed, its consumers did not follow (prerequisite repairs)

The operator's layout is real: [fact: `git log --diff-filter=R`] `3d4088d` moved `skills/ci-skills/*` to `ci-skills/*`,
`af0814f` moved `scripts/core/*` to `lib/core/*`, `d9629d7` moved `lib/{ci,automation,core}/*.bash` to `lib/bash/...`,
`b7e0a24` moved `scripts/*.py` to `bin/*.py` and renamed `docs/phases/CI-*.md` to `CI05..CI10-*.md`.
Every Python main in `ci-skills/bin/` is 12-49 lines except `gitlab_access.py` (125) and `gitlab_pipeline.py` (127)
[fact: `wc -l`]; `lib/core` is 9,514 lines in 30 modules.

Still pointing at the old tree [fact: grep + `ls`]:

| File | Line | Points at | Effect |
| --- | --- | --- | --- |
| `ci-skills/bin/ci-api` | 6 | `lib/ci/api.bash` (now `lib/bash/ci/api.bash`) | `ci-api --help` fails: "No such file or directory" (observed) |
| `ci-skills/bin/ci-binary-build` | 6 | `lib/automation/binary_build.bash` (now `lib/bash/automation/`) | same class of failure (by inspection) |
| `bin/ci-api`, `bin/ci-binary-build` | 5 | `skills/ci-skills/bin/...` | root adapters exec a missing path |
| `tests/python/conftest.py` | 18 | `skills/ci-skills/scripts` | every test using `import_script_module` cannot import |
| `tests/python/test_installed_package.py` | 16 | `skills/ci-skills` | |
| `tests/python/test_skill_package.py` | 11 | `skills/ci-skills` | |
| `tools/render_manifest.py` | 27-32, 60 | `skills/ci-skills/scripts/core/catalog.py` | `tools.json` cannot be regenerated or checked; the reference proposal depends on this |
| `tools/check_live_acceptance.py` | 451, 643, 657 | `skills/ci-skills/scripts` | |
| `tools/install_ci_skills.py` | 29, 35, 58 | `skills/ci-skills` | |
| `README.md` | 22 (broken link `ci-skills/scripts/`), 29, 89, 214, 221, 279, 343, 346 | old paths and commands | user-facing |
| `scripts/README.md` | 5-8 | `skills/k8s-admin-diagnostics/scripts/{bash,python}/core` | stale tree picture |
| `ci-skills/SKILL.md` | 14 (`../scripts`), 219 (`../bin/ci-api`) | old relative paths | agent-facing |

Also [fact: layout audit]: `tests/python/conftest.py:17` computes `REPO_ROOT = parents[1]`, which from
`tests/python/` is `tests/`, not the repo root (off by one since the tests moved); `ci-skills/lib/bash/ci/api.bash:5,7`
and `lib/bash/automation/binary_build.bash:5,7` climb `../..` to `ci-skills/lib` and look for `lib/core/runtime.bash`
under `ci-skills/lib/lib/`; `tests/bash/{api,binary_build,check}.bats:4-5` run `../bin/...` and source
`../lib/ci/check.bash` relative to `tests/bash/`; the shellcheck `source=` directives in `ci-api:5`, `api.bash:6`,
`binary_build.bash:6` still name `skills/ci-skills/...`. Nothing under `ci-skills/bin` can import `core` today:
no `sys.path` edit, no `.pth`, no install step puts `ci-skills/lib` on the path (`find_spec("core")` is None).
The installed copy at `~/.codex/skills/ci-skills` carries both `bin/` and `scripts/`, so it is not a clean "after".

These repairs are a prerequisite block, not part of the reference feature (see Delivery, block 0).

### F3a. The project-neutrality gate fails on `main` today

[fact: `conda run -n ci-skills python tools/check_project_neutrality.py --root . --json` on 2026-10-06] `status: FAIL`,
three violations, all `content` matches in tracked files: `Makefile` (lines 1 and 77), `pyproject.toml` (line 29),
`scripts/bash/core/result.bash` (lines 163 and 201). The marker is the other project's name and is not written here.
`Makefile:1-2` says the file was copied from that project and not yet adapted; `pyproject.toml:10-27` still carries
that project's package name, build backend and wheel settings. Nothing runs the checker any more (the deleted
workflow did; `bless.sh`, `Makefile` and `scripts/` do not call it), which is why the violation reached `main`.
Repair belongs in block 0: strip or replace the three files' foreign content, then the checker reports `PASS`.

### F3b. Dual implementations the operator noticed, inventoried (layout audit, file:line verified)

The layout holds (13 of 15 Python mains are thin, both Bash mains are 7 lines), but the code is not free of
second implementations. Each row is one behaviour written more than once; the plan's reference work must reuse the
first column, and the docs must name these as the reuse debt CI02-CLI and CI03-GATES resolve:

| Behaviour | Implementations |
| --- | --- |
| GitLab command flow (describe, dry-run guard, required options, resolve target, bind session, gate, emit, failure) | `ci-skills/lib/core/cli.py:487-557` `execute_gitlab_job`; again in `ci-skills/bin/gitlab_access.py:67-121` and `ci-skills/bin/gitlab_pipeline.py:47-123` (both import the private `core.cli._failure`); a fourth shape in `lib/core/gitlab_actions.py:527-696` `run_action_cli` |
| Exit-code table | `ci-skills/lib/core/catalog.py:550-553` (0/2, published in `tools.json`); `ci-skills/lib/bash/core/runtime.bash:6-9` (`CI_EXIT_BLOCKED=69`); `scripts/bash/core/exit_codes.bash:9-23` (`CI_EXIT_BLOCKED=2`) |
| `glab api` transport with retry and Retry-After | `ci-skills/lib/core/gitlab_api.py:114-167,296-372`; `ci-skills/lib/bash/ci/api.bash:64-197` (different budgets: 30 s vs 5 s Retry-After cap) |
| Result envelope | `ci-skills/lib/core/report.py:44-61,221-259`; `scripts/bash/core/result.bash:168-230` (different schema and status words); hand-built envelopes in five more Python places |
| Provider error classification | `ci-skills/lib/core/runtime.py:324-353`; `scripts/bash/core/result.bash:100-123`; `ci-skills/lib/bash/ci/api.bash:78-97` |
| Diagnostic logger for the same four `--log-*` flags | `ci-skills/lib/core/cli.py:247-298`; `ci-skills/lib/core/mtu_consistency.py:57-94`; `ci-skills/lib/bash/core/runtime.bash:24-49` (three record shapes) |
| Secret patterns | `ci-skills/lib/core/runtime.py:32-94` (redacts); `scripts/bash/core/result.bash:68-77` (blocks) |
| Single-token-file rule (non-empty, one value, <= 4096 bytes) | `ci-skills/lib/core/credentials.py:41-51`; `ci-skills/lib/bash/core/runtime.bash:55-75` |
| Clean-checkout revision check | `ci-skills/lib/core/provenance.py:106-130`; `ci-skills/lib/bash/core/runtime.bash:97-118` |
| SHA-256 of stdin | `ci-skills/lib/bash/core/runtime.bash:79-91`; `scripts/bash/core/digest.bash:12-44` (opposite tool preference) |
| argparse subclass turning bad arguments into a BLOCKED envelope | `ci-skills/lib/core/cli.py:123-158`; `lib/core/argument_parser.py:15-47` (imported by nothing); `lib/core/node_local_cli.py:29-64` |
| Advisory file lock polling `flock` | `tools/install_ci_skills.py:94-124`; `ci-skills/lib/core/gitlab_api.py:211-259` |
| "Find the skill library and put it on sys.path" | hand-copied in `tools/install_ci_skills.py:29-32`, `tools/render_manifest.py:26-37`, `tools/check_live_acceptance.py:448-458`, `tests/python/conftest.py:17-19,34-39`, `ci-skills/benchmarks/event_trace_worker.py:36-43`; all five hold the stale path |
| Common `--log-*` flag parsing in Bash | `ci-skills/lib/bash/ci/api.bash:207,226-269`; `lib/bash/automation/binary_build.bash:75,80-113` |
| Plan fingerprint | `lib/bash/automation/binary_build.bash:137-141`; `lib/core/mtu_consistency.py:280-281`; `lib/core/gitlab_actions.py:65`; `tools/install_ci_skills.py:61-65` |

The reference work adds none of these: one locator (block 0), one envelope (`report.emit`), one exit table
(`catalog.EXIT_CODES` until CI02-CLI), one digest (`provenance.tree_digest`), one transport.

### F4. What the external grounding says (fetched 2026-10-06)

- Agent Skills spec (<https://agentskills.io/specification>): layout `SKILL.md` + optional `scripts/`, `references/`,
  `assets/`; progressive disclosure: metadata ~100 tokens at startup, `SKILL.md` body < 5,000 tokens on activation,
  resources "loaded only when required"; "Keep your main SKILL.md under 500 lines"; "Keep file references one level
  deep from SKILL.md"; `references/`: "Agents load these on demand, so smaller files mean less use of context";
  frontmatter `name` (1-64, `[a-z0-9-]`, equals directory name), `description` (1-1024), optional `license`,
  `compatibility`, `metadata`, `allowed-tools`.
- Claude best practices (<https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>):
  "Keep SKILL.md body under 500 lines"; Pattern 2 "Domain-specific organization" (one file per domain, SKILL.md is the
  navigation); "Keep references one level deep"; "For reference files longer than 100 lines, include a table of
  contents"; "No context penalty for large files ... until actually read"; prefer utility scripts for deterministic
  operations and say whether to execute or read them; "Name files descriptively".
- Claude Code (<https://code.claude.com/docs/en/skills>): "Claude Code skills follow the Agent Skills open standard";
  only the six spec fields are portable; descriptions load at startup, body on invocation; supporting files are
  "loaded when needed".
- Codex / ChatGPT (<https://learn.chatgpt.com/docs/build-skills>, the target of the
  `developers.openai.com/codex/skills` 308 redirect; the Codex repo's `docs/skills.md` points there too):
  same layout plus optional `agents/openai.yaml` (`display_name`, `short_description`, `default_prompt`,
  `allow_implicit_invocation`); "ChatGPT and Codex start with each skill's name and description, then load the full
  SKILL.md instructions when they decide to use that skill"; the skills list "uses at most 2% of the model's context
  window, or 8,000 characters"; "Skills build on the open agent skills standard"; discovery paths listed are
  `$CWD/.agents/skills` up to `$REPO_ROOT/.agents/skills`, `$HOME/.agents/skills`, `/etc/codex/skills`, bundled.
  UNVERIFIED: whether the installed Codex CLI still reads `~/.codex/skills` (the installer's target, `README.md:242`);
  on disk `~/.codex/skills/.system` and `~/.codex/skills/ci-skills` (installed 2026-10-04, carrying BOTH `bin/` and
  `scripts/`) suggest it does; `codex` is not on this shell's PATH so `codex --version` could not be read.
- `glab skills --help` (glab 1.120.0): "Skills follow the Agent Skills specification and work with any compatible
  agent, including GitLab Duo Agent Platform, Claude Code, Codex, and Gemini CLI"; verbs `get`, `install`, `list`,
  `update`; still marked experimental. `glab skills list` shows `glab`, `glab-stack` (bundled) and `orbit` (remote).
- Official Codex/ChatGPT layout and scopes (<https://learn.chatgpt.com/docs/build-skills>, operator-supplied and
  re-fetched 2026-10-06), which this plan adopts as the target shape for `ci-skills`:

  | Entry | Status | Purpose (quoted) |
  | --- | --- | --- |
  | `SKILL.md` | required | "instructions + metadata" |
  | `scripts/` | optional | "executable code" |
  | `references/` | optional | "documentation" |
  | `assets/` | optional | "templates, resources" |
  | `agents/openai.yaml` | optional | "appearance and dependencies" |

  Invocation: explicit (`@` in ChatGPT; `/skills` or `$` in Codex) or implicit ("ChatGPT or Codex can choose a skill
  when your task matches the skill `description`"). Scopes: REPO `$CWD/.agents/skills`, `$CWD/../.agents/skills`
  (parents up to the Git root), `$REPO_ROOT/.agents/skills`; USER `$HOME/.agents/skills`; ADMIN `/etc/codex/skills`;
  SYSTEM bundled by OpenAI. Duplicate names across scopes are not merged: "both can appear in skill selectors".
  `agents/openai.yaml` keys: `interface` (`display_name`, `short_description`, `icon_small`, `icon_large`,
  `brand_color`, `default_prompt`), `policy` (`allow_implicit_invocation`), `dependencies.tools[]`
  (`type: mcp`, `value`, `description`, `transport`, `url`).
- Two OpenAI-curated skills, read as worked examples (<https://github.com/openai/skills>, `skills/.curated/`):
  - `gh-fix-ci`: `SKILL.md` (3,651 bytes), `LICENSE.txt`, `agents/openai.yaml` (display name "GitHub Fix CI", short
    description "Debug failing GitHub Actions CI", a `default_prompt`, icons `github-small.svg` and `github.png`
    under `assets/`), and `scripts/inspect_pr_checks.py` (about 500 lines, standard library only, calls
    `gh auth status`, `gh pr view --json number`, `gh pr checks <pr> --json ...`, `gh run view <id> --json ...`,
    `gh run view <id> --log`, `gh api /repos/{slug}/actions/jobs/{id}/logs`; options `--repo .`, `--pr`,
    `--max-lines 160`, `--context 30`, `--json`; JSON `{"pr": ..., "results": [{name, detailsUrl, runId, jobId,
    status: ok|external|log_pending|log_unavailable, run, logSnippet, logTail, note, error}]}`; exit 0 when nothing
    fails, 1 otherwise; external providers reported by URL only; no retry). The `SKILL.md` body says "execute the
    script", not read it.
  - `openai-docs`: `SKILL.md` (18,302 bytes), bundled fallbacks under `references/` (`latest-model.md`,
    `upgrade-guide.md`, `prompting-guide.md`), and two scripts: `scripts/fetch-codex-manual.mjs` (16,085 bytes)
    fetches the live Codex manual, checks freshness, caches it under `$TMPDIR/openai-docs-cache`, and writes
    `codex-manual.outline.md`, "a mapping of sections to line ranges", which the agent queries before reading;
    `scripts/resolve-latest-model-info.js` returns guide URLs to fetch. Pattern: live fetch first, bundled copy as
    fallback, an outline so the agent reads one section, not the manual.

### F5. What the upstream GitLab reference looks like (measured 2026-10-06)

- Source of truth: `gitlab-org/gitlab` `doc/ci/yaml/_index.md`, front matter `title: CI/CD YAML syntax reference`.
  [fact: `curl | awk`] 7,511 lines, 254,048 bytes, 5 H2 sections (`Keywords`, `Global keywords`, `Header keywords`,
  `Job keywords`, `variables`), 40 H3 keyword headings (`` ### `trigger` ``), 111 H4 sub-keyword headings
  (`` #### `trigger:forward` ``). Fixed sub-structure under a keyword: **Keyword type**, **Supported values**,
  **Example of `x`**, **Additional details**, **Related topics**; Hugo shortcodes `{{< history >}}`, `{{< details >}}`,
  `{{< icon >}}`, `{{< flag >}}` and `[!note]`-style alerts occur.
- Keyword headings carry backticks: 1 at H2 (`` ## `variables` ``), 40 at H3, 111 at H4, 152 in all; no keyword path
  appears twice; three H3/H4 headings under the keyword sections are prose, not keywords ("Dynamic environments",
  "Job `variables`", "Default `variables`") [fact: prototype run against the pinned SHA, 2026-10-06].
- Chunk rule (a chunk runs from its keyword heading to the next keyword heading of any level or the next H2 section
  heading; a prose H3/H4 folds into the chunk it sits in): 152 chunks totalling 247,210 bytes; smallest 93 bytes
  (`release:milestones`), median 1,269, p90 2,829, largest 9,463 (`rules:changes`); 7 chunks exceed 100 lines.
  `trigger:forward` is 84 lines / 2,587 bytes (source lines 7031-7114); the `trigger` intro is 3,273 bytes.
  (The first draft of this plan let an H3 chunk swallow its H4 children, which is why it quoted 12,507 and 28,279
  bytes for `trigger` and `rules`; those were nested totals, not chunks.)
- Anchor rule, verified two ways: the rendered page has `<h4 id=triggerforward>` [fact: `curl docs.gitlab.com/ci/yaml/`],
  and GitLab's own schema links 78 distinct `docs.gitlab.com/ci/yaml/#...` anchors [fact: grep of `ci.json`]. Rule:
  lowercase the heading text, drop every character not in `[a-z0-9_ -]` (backticks and `:` vanish, `_` stays),
  replace spaces with `-`. The rule reproduces 77 of the 78 from headings; exactly one dangles at this SHA
  (`globally-defined-image-services-cache-before_script-after_script`, a section that no longer exists), so the
  oracle test asserts the 77 and records the one dangling link as a fixture. (An earlier pass of this plan reported
  5 missing anchors; 4 of them were H5 headings that its H2-H4 regex never saw.)
- Corrected heading census (prototype re-run with H2-H5, matching the code-design pass): keyword headings 1 H2
  (`variables`), 40 H3, 111 H4, 15 H5 (`cache:key:files`, `spec:inputs:*`, `rules:changes:*`, `rules:exists:*`),
  167 in all; 7 prose headings (the four section H2s, `Dynamic environments`, ``Job `variables` ``,
  ``Default `variables` ``). With the own-text rule (every heading, keyword or prose, ends the chunk before it) the
  167 keyword chunks, 7 section chunks and a 1,635-byte preamble partition the file exactly (sum = 254,048 bytes).
  Keyword chunks: smallest 93 bytes, median 1,236, p90 2,632, largest 5,000 (`after_script`); 2 exceed 100 lines;
  `trigger:forward` 2,587 bytes / 84 lines.
- The `ci.json` join resolves 130 of the 152 H2-H4 keywords to a schema node and yields a one-line `description`
  for 82; unmatched keywords carry no description (never invented). A prototype `index.json` is about 65 KB compact
  for 152 entries (roughly 430 bytes per entry); with 174 entries and the children fields the design pass estimates
  about 80 KB.
- Machine-readable companion: `app/assets/javascripts/editor/schema/ci.json` [fact: fetched] 128,122 bytes,
  draft-07, `$id: https://gitlab.com/.gitlab-ci.yml`, 58 `definitions`, 120 `markdownDescription` nodes of which
  102 carry a `docs.gitlab.com` link. `trigger.forward` is `{yaml_variables: boolean, default true;
  pipeline_variables: boolean, default false}` with one-line descriptions.
- Pinning: latest upstream commit touching both files is `9892f2e6cf006fa1acc3f4d744f707757111db58`
  (2026-10-02, "Increase the CI cache key file limit to 10") [fact: GitLab API `repository/commits?path=`];
  the raw-by-ref URL answers HTTP 200 with `content-length: 254048`.
- License [fact: `gitlab-org/gitlab/LICENSE`]: "All content residing under the 'doc/' directory of this repository is
  licensed under 'Creative Commons: CC BY-SA 4.0 license'"; everything else (so `ci.json`) "MIT Expat".
  This repository has no `LICENSE` or notice file [fact: `git ls-files | grep -i licen` empty].
- The upstream example block contains literal `---` lines between comment lines (verified with `sed -n l`); vendored
  text is kept byte-for-byte, so the chunk will show them too.

### F6. Current skill load footprint (measured)

| File | Bytes | Lines |
| --- | --- | --- |
| `ci-skills/SKILL.md` | 12,501 | 229 |
| `ci-skills/tools.json` | 26,432 | 872 |
| `ci-skills/references/access.md` | 9,443 | 174 |
| `ci-skills/references/project-binding.md` | 4,323 | 99 |

`SKILL.md` is under the 500-line limit but already restates things declared in `tools.json`; CI08-ROUTING's planned
split (sections 4-6 to `references/reading-reports.md`) stands.

### F7. Standards constraints that bind the design

- `documentation-contract.md:71-73` (HEAD): "Original third party, vendor doc must be always excluded. Example
  `(?:^|[\/])(?:vendor|third[-_]?party|external)(?:[\/]|$)`. QA must always flag it." A vendored reference tree must
  sit under a path segment that matches this regex, or every doc gate lints upstream text.
- `software-design.md:9-23` (HEAD): reuse before implementation, no duplicate implementations, evaluate the standard
  library first, cross-repo reuse via declared dependencies only.
- `bash-shell-standards-contract.md:64-72` (HEAD): `bin/` thin entrypoints, `lib/core/`, `lib/automation/`.
- `agent-grade-tools.md` (`~/dev/standards`): non-interactive, deterministic, machine-readable, bounded output,
  next step on failure, zero hidden state.
- `ci-skills/lib/core/provenance.py:54-65`: `tree_digest` includes every regular file under `ci-skills/` except
  `__pycache__`, `*.pyc/.pyo`, `.DS_Store`. A vendored reference inside `ci-skills/` changes the skill digest on every
  upstream refresh and invalidates the committed receipts; `docs/field-notes.md:152-155` already asks to weigh
  separating the code digest from the prose digest (open decision D-DIGEST).
- Operator rules: compute rather than copy where reliable; one declaration per fact; CI offline and deterministic;
  upstream text keeps its license; no implicit files.

## Proposal A: the on-demand knowledge reference (home: `docs/phases/CI09-REFERENCE.md`, second reference kind)

CI09 today covers one kind, tool *operations* (what our code calls). This adds the second kind, upstream
*knowledge* (what an agent reads), in the same doc and the same Block/Steps/Gates shape, so the doc set stays
"not a zoo". The GitLab CI YAML syntax reference is the first instance; the mechanism is generic (any upstream
Markdown page with stable headings).

### Operator correction of 2026-10-06, applied throughout this proposal

The agent must not read the index and pick a file itself, and the mechanism must not be wired to one GitLab page.
The interface is a black box: the caller passes a domain it is already anchored on (`gitlab`, `kubernetes`) and
zero or more tags; the tool answers with a small, bounded set of tags to choose from and, for each, where it leads
next (another `next` call, a `get` of one chunk, or a command to run). The agent runs the command it chose; another
agent can pass the tag instead. The same black box serves every declared reference and the skill's own commands,
so "where to look next" is one tool for knowledge and for tools.

### Block

| Part | Value |
| --- | --- |
| Capability | vendor any number of upstream reference pages as verbatim chunks with a derived index, and answer "where do I look next" for a domain and tags with a bounded menu that ends in one chunk or one command |
| Owner | `ci-skills/lib/core/reference.py` (chunk, anchor, index, verify, lookup) and `ci-skills/lib/core/navigate.py` (the tag tree over every reference index plus `tools.json` routing); fetch and transaction in `tools/skillkit/` (Placement) |
| Entrypoints | installed: `ci-skills/bin/reference.py next\|get\|list\|verify`; maintenance: `tools/update_reference.py` (thin main) |
| Result | `reference_next` (a menu: node, choices, leaf), `reference_record` (`get`), `reference_index` (committed `index.json`), lock entries, `reference_verify` |
| Read-back | `verify` recomputes every chunk digest and the index from the committed files; every `next` answer is reproducible from the committed indexes and `tools.json` alone |

### The navigator (`reference.py next`)

```text
reference.py next                       -> domains: gitlab, kubernetes (derived: every reference's domain and every command's `requires`)
reference.py next gitlab                -> areas: ci-yaml (reference), commands (gitlab_* tools), later api, glab
reference.py next gitlab ci-yaml        -> top keywords, grouped by section: global (default, include, ...), header (spec), job (trigger, rules, ...)
reference.py next gitlab ci-yaml trigger -> children: forward, include, inputs, project, strategy; leaf: get gitlab-ci-yaml trigger
reference.py next gitlab pipeline       -> a tag that is not a path: ranked matches over derived tags (pipeline -> trigger, workflow, needs, gitlab_pipeline.py ...)
```

- **Input**: `DOMAIN [TAG ...]`, `--json|--yaml|--human`, `--limit N` (default 12). Offline, no credentials.
- **Output**, bounded to one screen: `{"kind": "reference_next", "domain", "path": [...], "node": {"id", "kind":
  domain|area|section|keyword|command, "summary"}, "choices": [{"tag", "kind", "summary", "bytes", "next": "<the
  exact command to run>"}], "leaf": {"get": "...", "command": "...", "url": "..."} }`. Each choice is one line; the
  answer for `trigger` is about 0.8 KB; when more than `--limit` choices match, the answer says how many were cut
  and which tag narrows them (no silent truncation).
- **Tree sources, all derived, no hand-written tree**: reference indexes (keyword `parent`/`children`, the H2
  section, `ci.json` subkeys, the keyword's own path tokens, the slugs of its **Related topics** links) and
  `tools.json` (`routing` phrases, `commands[*].requires` for the domain, `use_when`, `subcommands`). A declared
  reference carries one `domain` field; nothing else is typed by hand.
- **Matching**: an exact child tag wins; otherwise case-insensitive token match over the derived tags of every node
  under the current path, ranked by (exact tag, path token, section, related-topic slug, use_when word); ties are
  listed, never guessed; `tag_unknown` returns the nearest tags. Deterministic: the same inputs give the same menu.
- **Leaves**: a keyword node offers `get <reference> <keyword>` (chunk, `--part`, `--card`); a command node offers
  `<command> --describe` and the routing phrase it answers. The agent runs what it chose and stops.
- **Why it is smaller**: an agent never loads `index.json` (65-80 KB) or `tools.json` (26 KB); it reads three to
  four menus of under 1 KB each and then one chunk, and the choice can be delegated to another agent by passing the
  tag.
- **It is also the skill's programmatic self-advertisement** (operator, 2026-10-06): `next` with no arguments is
  "this is what I can do", `next harbor` is "this is what I can do about Harbor": every command the catalog
  declares (by the authority it `requires`) and every reference it vendors, in one bounded answer. A simple query
  costs one call, not a read of the manifest. As tools are added (Proposal F) they appear here with no extra work,
  because the tree is derived from `tools.json` and the reference indexes.

Two real answers, derived on 2026-10-06 from the pinned upstream page and the committed `tools.json` with the rule
above (prototype, nothing written): `next gitlab ci-yaml trigger` is 904 bytes indented and lists the five children
(`forward` 2,587 B, `include` 2,212 B, `inputs` 451 B, `project` 1,374 B, `strategy` 2,108 B) plus the leaf
`get gitlab-ci-yaml trigger`; `next gitlab pipeline` is 622 bytes and lists `needs:pipeline`, `needs:pipeline:job`,
`gitlab_job.py --describe` and `gitlab_pipeline.py --describe` (the keywords by path token, the commands by their
routing phrase and `requires: gitlab`).

### Layout (one level deep from `SKILL.md`; `vendor` segment satisfies the exclusion regex)

```text
ci-skills/references/vendor/gitlab-ci-yaml/
├── LICENSE.md            local origin: CC BY-SA 4.0 notice, attribution, source URL and pinned SHA; MIT note for ci.json fields
├── index.json            generated by update; kind reference_index; validated by the schemas gate
├── _preamble.md          upstream bytes before the first heading (1,635 B)
├── keywords.md ...       the 7 prose headings as section chunks (job-keywords.md, dynamic-environments.md, ...)
├── variables.md          the one H2 keyword
├── trigger.md            the H3 text up to its first H4 (keyword type, supported values, example, details)
├── trigger.forward.md    one H4 chunk, upstream bytes verbatim (2,587 B)
├── cache.key.files.md    one of the 15 H5 keywords
└── ...                   167 keyword chunks + 7 section chunks + preamble + LICENSE.md + index.json = 176 files; no symlinks
```

File name = heading text with `:` replaced by `.`, plus `.md` (section chunks use the heading's anchor). Chunk bytes =
the exact slice of the upstream file from a heading line to the line before the next heading of any level (the
own-text rule). Concatenating `_preamble.md` and every chunk in document order reproduces the upstream file byte for
byte, which `verify` checks offline as a byte-sum invariant. No local text inside a chunk (no table of contents: the
index's `lines` and `bytes` fields and the 5,000-byte maximum make one unnecessary); metadata lives only in
`index.json`, so per-file digests are reproducible from (SHA, heading).

### Index record (the "memory": pointers, machine-readable schema, where is what, tags)

```json
{
  "schema_version": "1.0",
  "kind": "reference_index",
  "reference": "gitlab-ci-yaml",
  "upstream": {
    "project": "gitlab-org/gitlab", "path": "doc/ci/yaml/_index.md",
    "sha": "9892f2e6cf006fa1acc3f4d744f707757111db58", "license": "CC-BY-SA-4.0",
    "link_base": "https://docs.gitlab.com/ci/yaml/",
    "schema": {"path": "app/assets/javascripts/editor/schema/ci.json", "sha": "9892f2e6...", "license": "MIT"}
  },
  "entries": [
    {
      "keyword": "trigger:forward", "parent": "trigger", "level": 4,
      "file": "trigger.forward.md", "sha256": "...", "bytes": 2587, "lines": 84,
      "anchor": "triggerforward", "url": "https://docs.gitlab.com/ci/yaml/#triggerforward",
      "keyword_type": "job",
      "description": "Specify what to forward to the downstream pipeline.",
      "schema_path": "/definitions/job_template/properties/trigger/oneOf[0]/properties/forward",
      "subkeys": ["yaml_variables", "pipeline_variables"],
      "load_when": ["trigger:forward", "forward:", "yaml_variables", "pipeline_variables"]
    }
  ]
}
```

Every field is derived, none is hand-written: `description`, `schema_path` and `subkeys` come from `ci.json`
(matched by keyword path); `keyword_type` from the H2 the heading sits under; `load_when` from the keyword and its
subkeys; `tags` are omitted until a declared source for them exists (no invented tags). Entries are sorted by keyword.

### Algorithms (what an implementer executes)

1. **Fetch** (maintenance only, network): GET `https://gitlab.com/gitlab-org/gitlab/-/raw/<sha>/doc/ci/yaml/_index.md`
   and `.../raw/<sha>/app/assets/javascripts/editor/schema/ci.json` with a timeout and a byte bound (both files
   measured under 256 KiB; bound 1 MiB); `<sha>` is an argument, never `master`. Default run prints the plan (URLs,
   SHA, expected byte counts) and exits 0; `--confirm` fetches. Standard-library `urllib.request` with timeout, or
   `core.runtime.run_command_bounded(["curl", ...])` if the operator prefers one transport; not a third one.
2. **Chunk**: scan lines, treating fenced code blocks as opaque; a heading is `^(#{2,6}) (.+)$`, a keyword heading
   one whose text is a backticked keyword path `^`([a-z0-9_]+(?::[a-z0-9_]+)*)`$`, anything else a section heading.
   A chunk runs from its heading to the line before the next heading of any level; the bytes before the first heading
   are the preamble. Reject: a duplicate anchor or file name, a chunk larger than `max_chunk_bytes` (64 KiB, well
   above the measured 5,000), heading depth 6, and a byte sum that does not equal the upstream size.
3. **Anchor**: `lower(text)`, drop chars not in `[a-z0-9_ -]`, spaces to `-`. Test oracle: the 78 anchors found in
   `ci.json` links; every anchor that has a heading at the pinned SHA must be reproduced (73 today), the 5 that have
   none are a recorded fixture; plus the rendered `<h4 id=triggerforward>`.
4. **Index**: for each chunk build the record above; look up `ci.json` by keyword path to fill `description`,
   `schema_path`, `subkeys`; emit `index.json` with sorted keys and `\n`; validate against `reference-index`
   (CI07-SCHEMA) before writing.
5. **Lock**: per file `sha256` and `origin` (`upstream` for chunks, `local` for `LICENSE.md` and `index.json`), and the
   tree digest from `core.provenance.tree_digest`, written into the same lock file CI05-VENDOR defines (one lock for
   every vendored tree: skills and references).
6. **Transaction**: CI05-VENDOR's staging/backup/rename/marker/recovery, reused as is (one implementation).
7. **Lookup** (installed, offline): `reference.py get gitlab-ci-yaml trigger:forward --json` reads `index.json`, finds the
   entry (exact keyword, else `--search` substring over keyword and `load_when`), checks the chunk's `sha256`, and
   prints the record with the chunk text in `content`. `list` prints keyword, bytes, url per entry. `verify` checks
   lock, digests, symlinks, index validity. Reason tokens, each defined once in `core.reference`:
   `reference_unknown`, `keyword_unknown`, `keyword_ambiguous`, `index_invalid`, `digest_mismatch`,
   `file_missing`, `unexpected_file`, `symlink_unexpected`, `chunk_too_large`, `fetch_failed`, `fetch_too_large`.
   Exit codes and envelope follow the existing catalog (`EXIT_CODES`, `STATUS_MEANING`) until CI02-CLI lands its
   shared table.

### Code design, reuse first (design pass of 2026-10-06, every reuse cited to the current tree)

| File | Action | What it owns | Reuses (never duplicates) |
| --- | --- | --- | --- |
| `ci-skills/lib/core/reference.py` | create | `Reason` enum with one `SAFE_NEXT_STEP` per token; `Heading`, `Chunk`, `IndexEntry` dataclasses; `anchor`, `chunk`, `build_index`, `verify_tree`, `lookup`; the parser and runner for `get`, `list`, `verify` | `catalog.SCHEMA_VERSION`; `report.report`/`emit`; `status.exit_code`; `provenance.file_digest`/`included_files`; `runtime.sanitize`; `paths.SKILL_ROOT` |
| `ci-skills/lib/core/paths.py` | create | the one `SKILL_ROOT` declaration (`parents[2]`), `REFERENCES_DIR`, `VENDOR_DIR` | replaces the two copies in `credentials.py:111,132` |
| `ci-skills/lib/core/cli.py` | modify | `offline_parser(...)` and `execute_offline(...)`: the describe, parse, run, emit, log, exit sequence for a target-free, credential-free command | `MachineArgumentParser` (`cli.py:123-158`), `output_mode`, `log_event`, `_failure`; `parser()` is recomposed on top so every existing option set stays byte-identical |
| `ci-skills/lib/core/catalog.py` | modify | `OFFLINE_OPTIONS` (derived from `UNIVERSAL_OPTIONS`), `OFFLINE_COMMANDS['reference.py']`, `describe_offline`, `REFERENCES` (kind, index, notice, command, upstream pin without digests), a top-level `references` key in `manifest()`, one routing row | `test_catalog.py:58-65,175,216` forbid a target-free entry in `COMMANDS`, hence a fourth table beside `NODE_LOCAL_COMMANDS` and `BASH_COMMANDS` |
| `ci-skills/lib/core/report.py` | modify | `emit(..., verbatim=frozenset())`: named top-level keys bypass `redact_tree`; `human()` renders keyword, file, bytes, url | required: `runtime.redact` turns `secrets:token` into `secrets:[REDACTED]`, and 11 of 174 chunks change under it (the `secrets:*` and `services:*` entries); vendored, digest-declared text is not captured output (assumption D-REDACT below) |
| `ci-skills/lib/core/provenance.py` | modify | `file_digest`, `included_files` (public names for the inline sha256 and `_included`), symlink refusal shared with the installer | digest algorithm unchanged |
| `ci-skills/bin/_bootstrap.py` + one `import _bootstrap` line in each of the 15 mains | create | the one declaration that the library is `../lib` relative to `bin/` (block 0; today `env -i python ci-skills/bin/storage_report.py --describe` fails with `No module named 'core'`) | mirrors the Bash mains' single `source` line |
| `ci-skills/lib/core/navigate.py` | create | the derived tag tree (`Node`, `Choice`, `Menu` dataclasses), `build_tree(indexes, manifest)`, `next(tree, domain, tags, limit)`, ranking and `tag_unknown` with nearest tags; bounded rendering | reads `reference.ReferenceIndex` and `catalog.manifest()` only; no new declaration, no network |
| `ci-skills/bin/reference.py` | create | 13-line thin main, `build_parser()` for `test_catalog`; verbs `next`, `get`, `list`, `verify` | `core.reference.build_parser`/`run_cli`, `core.navigate` |
| `tools/skillkit/transaction.py` | create (extracted) | journal, lock, stage, swap, read-back, recover: `replace_tree(..., keep_previous, require_absent)` | moved out of `tools/install_ci_skills.py:94-397`; two consumers: the installer (`keep_previous=True`) and the in-tree reference update (`keep_previous=False`, lock removed, so no dot-residue enters `tree_digest`) |
| `tools/skillkit/fetch.py` | create | bounded `urllib.request` GET (`timeout`, `max_bytes` 1 MiB), `Fetcher` protocol for tests | not `run_command_bounded` (decodes with `errors="replace"`, not byte-exact) and not `GlabAPIClient` (JSON bodies only, needs a credential) |
| `tools/skillkit/reference_update.py` + `tools/update_reference.py` | create | `plan` (fetch both files, build, diff, write nothing) and `apply` (re-plan, `upstream_mismatch` if the bytes moved, transaction, verify read-back); thin main with `--reference`, `--sha` (40 hex, required), `--confirm`, `--timeout`, `--root`, output flags | `provenance.SHA_PATTERN`, `tree_digest`; `render_manifest.py`'s root pattern |
| `tools/install_ci_skills.py` | modify | installer policy only; keeps every name `test_installer.py` loads by path (`SKILL_NAME`, `_write_journal`, `recover_install`, `tree_digest`) | `skillkit.transaction`, `provenance.included_files` |
| `schemas/reference-index.schema.json` | create | 2020-12 schema for `index.json` (closed records, digests `^[0-9a-f]{64}$`, file names without `/` or `..`) | CI07-SCHEMA rules; `schemas/` exists and is empty |
| `ci-skills/SKILL.md`, `.markdownlint-cli2.yaml`, `tests/python/test_skill_package.py` | modify | the routing row and one sentence; `ignores += ci-skills/references/vendor/**`; `REQUIRED_PACKAGE_PATHS += bin/reference.py, index.json, LICENSE.md` | |

Test files the design names (every reason token has a producer): `test_reference_anchor.py` (6 cases, the 78-anchor
fixture with the one dangling entry), `test_reference_chunker.py` (10, synthetic skeleton fixture plus fences, H6,
duplicates, oversize), `test_reference_index.py` (8, the `trigger:forward` join to two `oneOf` pointers and two
subkeys), `test_reference_verify.py` (9), `test_reference_cli.py` (11, including `secrets:token` unredacted and
`--search` still redacted), `test_update_reference.py` (10, default run writes nothing; `upstream_mismatch`;
interrupted transaction recovered), `test_transaction.py` (7, ported from `test_installer.py:192-315`),
`test_catalog.py` (+6 offline mirrors), `test_installed_package.py` (+3), `test_redaction.py` (+2),
`test_report_human.py` (+2), `test_provenance.py` (+2).

Harness facts that shape the design (all from `tests/python`, read 2026-10-06): `test_catalog.py:58-65` makes every
`COMMANDS` entry accept the full universal tier and `:175` requires a non-empty `requires`, so `reference.py` lives in
`OFFLINE_COMMANDS`; `:103-118` runs `--describe` with `PATH=/nonexistent HOME=/nonexistent` and no `PYTHONPATH`, so
the bins must self-locate `lib/`; `:121-130` and `test_render_manifest.py:47-52` compare `tools.json` byte for
byte, so catalog and manifest land together; `test_installed_package.py:503` needs a clean committed subtree, so the
vendored tree is committed before the installed smoke can pass, and `:672` pops `PYTHONPATH`; `conftest.py` must
keep a `SCRIPT_ROOT` whose parent is the skill root (`SCRIPT_ROOT = ci-skills/bin`) because four tests derive
`SKILL_ROOT` from it; `test_cli_contract.py:141` requires positional `action` choices, not argparse subparsers,
because `MachineArgumentParser` records argv only on the top-level parser.

Assumptions this design proceeds on (recommendation stated; the operator may object): **D-REDACT** extend `emit`
with the narrow `verbatim` allowlist (tested: `filters`/`errors` stay redacted, `emit` without it is byte-identical);
**D-LOCK** the vendor declarations and the one lock live at the repository root as `vendor/vendor.toml` and
`vendor/vendor.lock.json` (outside the skill tree, under a `vendor` segment, shared by `vendor/skills/*` and
`ci-skills/references/vendor/*`); `index.json` starts at schema version `0.1` while CI09 is in review and becomes
`1.0` on merge (CI07-SCHEMA's own rule); the three empty tracked `__init__.py` files under `ci-skills/` (ruff N999,
and they do not make `core` importable) are removed in block 0.

### Lifecycle of a reference (operator request: "fetch, collect, structure, etc.")

One state machine per declared reference, the same shape as CI05-VENDOR's skill vendoring and the installer's
plan/confirm/read-back, so no second transaction or lock is written. Maintenance stages run from a checkout and may
use the network; runtime stages run from an installed copy and never do.

| Stage | Command (thin main -> library) | Reads | Writes | Evidence recorded | Failure tokens |
| --- | --- | --- | --- | --- | --- |
| 1 declare | hand edit of `vendor.toml` `[references.<name>]` | the upstream project, path, SHA, license, `schema_path`, chunker profile (`markdown-headings`, levels 2-4, keyword pattern), `max_chunk_bytes`, `fetch.{attempts,backoff_seconds,max_bytes}`, `max_age_days` | the declaration | `schemas` gate validates it | `declaration_invalid` |
| 2 fetch | `tools/update_reference.py <name> [--sha S]` plan by default; `--confirm` fetches | raw files at the pinned SHA | staging dir only | HTTP status, `content-length`, raw `sha256`, `fetched_with` | `fetch_failed`, `fetch_too_large`, `sha_invalid` |
| 3 structure | same command, pure library step (`core.reference.chunk`, `anchor`, `join`, `build_index`) | staged raw bytes, `ci.json` | chunk files and `index.json` in staging | entries, bytes, lines, per-file `sha256`, unresolved-anchor fixture | `duplicate_keyword`, `chunk_too_large`, `heading_outside_sections`, `index_invalid` |
| 4 verify | `bin/reference.py verify <name>` (also run by `update` before publishing) | lock, files, index | nothing | PASS or the first failing file, path and rule | `digest_mismatch`, `file_missing`, `unexpected_file`, `symlink_unexpected`, `lock_unreadable` |
| 5 publish | the CI05 transaction inside `update --confirm`: backup, rename staging into `references/vendor/<name>/`, write the lock, re-run verify, drop the backup; one PR per refresh | staging, current tree | the vendored tree and the lock | `git diff` per keyword file; receipts record the subtree digest (D-DIGEST) | `transaction_recovered` (reported, not an error) |
| 6 serve | `bin/reference.py get <name> <keyword>`, `list`, `--search`; `SKILL.md` routes here | `index.json`, one chunk | nothing | the record with `url`, `link_base`, `sha256`, `bytes` | `reference_unknown`, `keyword_unknown`, `keyword_ambiguous` |
| 7 check-upstream | `tools/update_reference.py <name> --dry-run` | the pinned SHA, the latest upstream commit touching the path (`repository/commits?path=`), today's date | nothing | `upstream_ahead` with the commit list, or `current`; `reference_stale` when `max_age_days` has passed | none (a report); whether `reference_stale` blocks a gate is the operator's rule |
| 8 refresh | stages 2-5 at the new SHA | | | the PR diff shows exactly which keywords changed | as above |
| 9 retire | remove the declaration, run `update` (plan shows the removals), `--confirm` removes the tree and lock entry | | | closed-world gate fails on any dangling `SKILL.md` line or index pointer | `dangling_reference` |

Contract-first, so the second reference adds no code path: a `ReferenceSource` contract with one implementation today
(`gitlab-raw`: a file at a commit from the GitLab raw endpoint; `github-raw` or an HTTP page later), a `Chunker`
profile with one implementation (`markdown-headings`), and an optional `Joiner` (`gitlab-ci-schema`). Each is a small
function group in `core/reference.py`, selected by the declaration, never by name checks inside the library
(`software-design.md:27-35`). Stages 2, 7 and 8 need the network and run where the operator runs maintenance;
stages 4 and 6 are offline and run in CI and on installed copies.

### How an agent loads it (the black box replaces the three hops of CI08-ROUTING)

| Step | The agent runs | Cost |
| --- | --- | --- |
| L0 | nothing; the skill `description` names "GitLab CI YAML keyword reference and navigator" | ~100 tokens, always loaded |
| L1 | `bin/reference.py next gitlab <tag>` (it is already anchored on `gitlab`), then the `next` command of the tag it chooses, two or three times | under 1 KB per answer |
| L2 | the leaf it was handed: `get gitlab-ci-yaml trigger:forward` (2,587 B, about 650 tokens), `--part values` (about 0.7 KB) or `--card` (about 0.5 KB); or `<command> --describe` | one chunk, never the page (254,048 B) |

`index.json` (about 65-80 KB, indented) and `tools.json` (26 KB) are inputs of the tool, never reading material;
the `SKILL.md` sentence says so. Relative links inside a chunk are returned resolved to the published URLs in
`links`; the bytes stay verbatim.

### Gates and tests (offline, deterministic; which surface runs them is D-GATE)

- Unit: anchor oracle (78 anchors, 77 reproduced, 1 recorded as dangling, plus the rendered id); chunker on a recorded
  fixture of the heading skeleton plus the real `trigger` section and a hostile fixture (duplicate heading, a
  heading inside a fence, H6, oversized chunk, shortcodes); index builder joins `ci.json` pointers for
  `trigger:forward`; every reason token produced once; `verify` passes on the committed tree and fails on one
  flipped byte, a deleted `LICENSE.md`, a stray file, a symlink; `update` default run writes nothing; interrupted
  transaction recovers (reuses the installer's tests).
- Navigator: `next` with no arguments lists exactly the derived domains; `next gitlab` lists `ci-yaml` and
  `commands`; `next gitlab ci-yaml trigger` lists its five children and the `get` leaf; `next gitlab pipeline` returns
  a ranked, bounded menu containing `trigger`, `workflow`, `needs` and `gitlab_pipeline.py`; `next gitlab nosuchtag`
  returns `tag_unknown` with nearest tags; every answer is under the declared byte bound; a second reference added
  to the fixture appears under its domain with no code change; the same inputs give byte-identical answers.
- Contract: every index entry's file exists and nothing else is in the tree (closed world, CI07-SCHEMA); `load_when`
  values are unique across entries; chunk bytes <= `max_chunk_bytes`.
- Lint: the vendored tree is excluded from markdownlint and doc gates via the `vendor` segment and the committed
  `.markdownlint-cli2.yaml` `ignores` (CI05-VENDOR already plans this for skills).
- Receipt: see D-DIGEST.

### How this compares with OpenAI's own docs skill, and the two choices it leaves

`openai-docs` keeps the manual as one file plus an outline of line ranges and fetches live first; this plan keeps
pinned, licensed copies first because the gates are offline and deterministic (F7) and the operator forbids
implicit files, which rules out a `$TMPDIR` cache. Two layout choices follow, each stated with its cost:

- **D-LAYOUT** (changes the file count, the lock and the tool):
  - *Per-keyword files* (recommended): 152 chunk files; worst-case read 9,463 bytes even when an agent ignores the
    index; `grep`-able and diffable per keyword; one `sha256` per chunk in the lock; matches the Claude guidance
    "organize by domain, one level deep".
  - *One file plus outline*, the `openai-docs` shape: `_index.md` kept whole (254,048 bytes, one `sha256`) and the
    index carries `start_line`/`end_line`; an agent reads a range (`Read` with offset and limit, or `sed -n`) and
    `reference.py get` slices at run time. Fewer files and the simplest license story, but one careless whole-file
    read costs about 64k tokens, which is the failure this proposal exists to prevent.
- **`--live`** (no decision needed, an option on `get`): fetch the same keyword section from upstream at `--sha` or
  `master` into memory and print it, writing no file; the vendored copy advances only through `update`. This is the
  "live first" half of the OpenAI pattern without its implicit cache.

### Open decisions this proposal needs (operator)

- **D-DIGEST**: keep vendored references inside `tree_digest` (every upstream refresh needs a new live receipt) or
  exclude `references/vendor/**` from the executed-code digest and digest it separately in the lock
  (`docs/field-notes.md:152-155`). Recommendation: exclude, with each vendored subtree's own `tree_digest` reported
  beside `skill.digest` as information the acceptance gate does not compare. The smallest SAFE change is not a
  one-token edit of `EXCLUDED_DIRECTORIES` (`provenance.py:43`): `tools/install_ci_skills.py:87` enumerates the copy
  set with the same `_included`, so that edit would silently drop the vendored tree from every installed copy. Add a
  `VENDOR_DIRECTORIES = ("vendor",)` declaration (the same segment the documentation contract's regex names), give
  `_included` and `tree_digest` an `excluded` parameter, pass the vendor exclusion only from the digest path, and add
  the `test_provenance.py` row beside `:142-151` that proves a byte change under `references/vendor/` leaves
  `skill.digest` unchanged while the subtree digest moves.
- **D-LICENSE**: the repository has no license file; CC BY-SA 4.0 requires attribution and share-alike for the chunks.
  Recommendation: per-tree `LICENSE.md` (the CI05 pattern) and no repo-wide change in this work.
- **D-HOME**: extend CI09-REFERENCE (recommended) or add `CI11-KNOWLEDGE.md`.

## Proposal B: finish the CIxx re-adjustment and add grounded detail (one PR, docs only)

Naming rule, applied everywhere: the phase id is the filename stem, `CI01-CATALOG` ... `CI10-PHASES`; bodies,
titles, dependency lines and cross-references use that id; the former ids and un-numbered `CI-*` disappear. Observed today
[fact: `git grep`]: 24 stale names in `CI01-CATALOG.md`, 9 in `CI02-CLI.md`, 5 in `CI03-GATES.md`, 3 in `CI04-HOOKS.md`,
22 in `CI05-VENDOR.md`, 11 in `CI06-TESTS.md`, 9 in `CI07-SCHEMA.md`, 15 in `CI08-ROUTING.md`, 12 in `CI09-REFERENCE.md`,
25 in `CI10-PHASES.md`; titles of CI05-CI09 still read `CI-VENDOR` etc.

Path rule, applied everywhere: `scripts/core/` -> `ci-skills/lib/core/`; `skills/ci-skills/scripts/*.py` ->
`ci-skills/bin/*.py`; `lib/ci/*.bash` -> `ci-skills/lib/bash/ci/`; `tools/skillkit/` -> the Placement decision below;
`skills/<vendored>/` -> `vendor/skills/<name>/` (matches the exclusion regex; installed separately from `ci-skills`);
`bin/ci-skills` (the repo maintenance command) keeps its name; `../../ci-skills` relative links are rewritten to
repo-relative paths.

Audit counts per doc (read-only audit of 2026-10-06; stale = a line naming an old phase, a missing path, a missing
command, or a 2026-10-02 state that no longer holds; under = a step an implementer cannot execute without inventing
inputs, outputs, tokens, order, bound, gate or read-back):

| Doc | Title still old | Block table | Stale lines | Open decisions | Underspecified steps |
| --- | --- | --- | --- | --- | --- |
| `CI01-CATALOG.md` | no | yes | 43 | 4 | 25 |
| `CI02-CLI.md` | no | no | 27 | 3 | 22 |
| `CI03-GATES.md` | no | no | 31 | 4 | 21 |
| `CI04-HOOKS.md` | no | yes | 24 | 5 | 22 |
| `CI05-VENDOR.md` | yes (`CI-VENDOR`) | yes | 47 | 3 | 32 |
| `CI06-TESTS.md` | yes (`CI-TESTS`, "every former-id phase") | no | 32 | 6 | 28 |
| `CI07-SCHEMA.md` | yes (`CI-SCHEMA`) | yes | 28 | 0 | 27 |
| `CI08-ROUTING.md` | yes (`CI-ROUTING`) | yes | 36 | 4 | 19 |
| `CI09-REFERENCE.md` | yes (`CI-REFERENCE`) | no | 26 | 3 | 18 |
| `CI10-PHASES.md` | partly (`CI Skills phases`, un-numbered table) | no | 58 | 9 | 17 |
| `docs/field-notes.md` | n/a (field notes) | n/a | 17 | 11 | 10 |
| `docs/gitlab-toolbelt-plan.md` | n/a (delivery plan, all six blocks shipped) | yes | 15 | 6 | 22 |

What the independent verification pass corrected: a large share of the "stale" lines in CI01, CI05, CI07, CI08 and
CI10 are forward references to deliverables that were never built (`bin/ci-skills`, `skills/`, `tools/skillkit/`,
the schema files, `scripts/hooks/`), not renamed paths. Those lines do not need a rename; they need the home decided
(Placement, and `vendor/skills/` for vendored skills) and the status marked "proposed, unbuilt". Doc-relative links of
the form `../../tests/...` resolve correctly from `docs/phases/` and are a convention question only. The verified
stale classes are: phase ids; `scripts/core`, `skills/ci-skills/scripts`, `lib/ci`, `acceptance/` paths; every
statement that `validate` exists or must pass; the 2026-10-02 measurements and PR status.

New facts the verification surfaced (each verified by the agent with git or gh, cited in the journal):

- Package delivery is DONE: PR #21 (`d6bba3d`, 2026-10-04) consolidated the package; `tools/install_ci_skills.py` is
  the retargeted installer and `install.sh` its thin adapter. `CI10-PHASES.md:42-45,78,97-98` still call it proposed.
- PR #2 was closed unmerged (2026-10-04T02:51:30Z); its tools shipped through PR #16. Every "(PR #2)" tag is history.
- The committed receipt records `file_count` 53 and `git ls-files ci-skills` lists 62 files; `field-notes.md:93`
  ("22 files") and the installed copy (`bin/` and `scripts/` both present) are out of date.
- All "five deferred defects" in `field-notes.md:183-188` are fixed on `main` (access.py:551, collect.py:175 and
  :471/559-562, credentials.py:110-111, report.py:239); the doc still lists them open.
- CI08's premise that the skill is read-only and hands every GitLab write to the `glab` skill is false now:
  `tools.json` has `read_only: false` and five commands declare `mutates: True`; its byte table is stale
  (`SKILL.md` 12,501; `access.md` 9,443; `tools.json` 26,432 with 17 commands; routing table 983 bytes single-line;
  `storage_report.py` entry 811 bytes).
- CI09's operation inventory must be re-derived from `ci-skills/lib/core/*.py` and `lib/bash/**`: besides
  `gh/glab auth status`, `glab api --method GET` and `kubectl auth can-i`, the code runs two fixed `cilium-health`
  argv, `cilium-dbg` through `kubectl exec`, `ceph` through `oc exec`, `oc debug node/` with `chroot /host ip -d -j`,
  and `journalctl -k` through an existing Pod. "Revisit when a non-cobra tool is added" has already happened.
- `tests/python/test_validate_workflow_policy.py:8` and `tests/python/test_event_trace_benchmark.py:361-363` read the
  deleted workflow file, so they fail on any surface until D-GATE lands.
- The deleted `lib/ci/check.bash` (readable at `88bd9f6^`) refused to run outside a Kubernetes pod
  (`KUBERNETES_SERVICE_HOST`), used exit codes 0/64/69, and ran `bats --tap tests` non-recursively; the bats files
  are now under `tests/bash/`.

Highlights the per-doc edits must address (beyond names and paths): CI02 counts 5 commands where there are 15 mains
plus 2 Bash tools, 13 universal options (it says 8), `--namespace` with three meanings (it says two), five
`mutates: True` commands using `--apply --confirm-plan` (it says `--confirm`), three exit tables (it says two);
CI03 is written against the deleted workflow and the deleted `lib/ci/check.bash`, and names no gate registry file,
no evidence path, no `gh pr checks` binding to a SHA; CI04 says `.coordination/` is never committed while three
files under it are tracked, and ignores that `Makefile:48-49` sets a local `core.hooksPath`; CI05 and CI01 build on
`skills/`, `tools/skillkit/`, `bin/ci-skills` and three schemas, none of which exist, and `schemas/` is an empty
untracked directory; CI06 has no deliverable name, no Depends-on, and its "174 tests in 22 files" is now about 398
test functions in 40 files under `tests/python/`.

The docs design pass produced the per-line edit lists (line, old text, new text), the sections to add and the
sentences to drop as restatements of another phase's rule. Counts: CI01 29 edits; CI02 18 edits, 1 drop (the copied
agent-grade checklist); CI03 22 edits plus a new "G0. A gate route exists (D-GATE)" gap; CI04 10 edits, 1 drop;
CI05 25 edits, 2 drops (title becomes "vendored trees, glab agent skills and upstream references"); CI06 18 edits,
3 drops, plus a "Knowledge kind" test section; CI07 13 edits, 1 drop; CI08 19 edits, 1 drop; CI09 15 edits, 1 drop,
plus the full "Knowledge references: upstream text read on demand" section (11,885 characters, drafted); CI10 13
edits, 1 drop (order gains a block 0 and the 2026-10-06 PR read-back); README 9 edits; `scripts/README.md` 1
(the whole tree picture). The implementation used these edit lists to update the phase documents.
Two corrections apply before use: the drafted CI09 section and the CI06 test section still carry this plan's first
numbers (151 chunks, 60 anchors, 12,507-byte `trigger`, "next heading of the same or higher level") and must take the
corrected ones above (167 keyword chunks, 174 with sections, 176 files, 78 anchors with 1 dangling, 5,000-byte
maximum, own-text rule); and the drafted labels D-PLACE and D-LOCK map to this plan's Placement decision and D-LOCK.

Per-doc grounded detail to add:

- `CI10-PHASES.md`: number every phase in the table and order; add CI09's second reference kind to the order
  (after CI05-VENDOR and CI07-SCHEMA, with CI08-ROUTING); replace the "Pull request status" paragraph with the
  current read-back (PR #23 and #28 open, drafts, CONFLICTING; `validate` absent); add D-GATE, D-DIGEST, D-LICENSE
  to "Open decisions"; add the layout decision as taken.
- `CI09-REFERENCE.md`: the Proposal A text above, in the house shape; keep the operations kind; name the executor
  question as D-GATE's dependent.
- `CI08-ROUTING.md`: `REFERENCES` gains `kind: operations|knowledge`, `index` (path to `index.json`), and the
  measured sizes above; the three hops table gets the reference lookup row; `load_when` matching algorithm stated
  once (exact status token, else case-insensitive substring over task text, then over `index.json.load_when`).
- `CI07-SCHEMA.md`: add `reference-index` and the lock's reference entries to the schema table; the pointer section
  gains `references[].kind`.
- `CI05-VENDOR.md`: generalise "vendored tree" to skills and references; one lock; transaction reused; placement per
  the decision; `skills/` -> `vendor/skills/`.
- `CI01-CATALOG.md`: discovery walks `ci-skills/` (the one local skill) and `vendor/skills/`; `list` reports each
  skill's references with `kind`; wrapper paragraph updated to the real `tools/install_ci_skills.py` imports.
- `CI02-CLI.md`: the "Two styles" table uses `ci-skills/bin/*.py` and `ci-skills/bin/ci-*`; the `cli` gate covers
  `reference.py`; the open `PARTIAL` and `ci-k8s` decisions stay open.
- `CI03-GATES.md`: replace "`validate` is the one required check, read back 2026-10-02" with the 2026-10-06 read-back
  (deleted in `1108cca`, protection disabled) and make D-GATE the first gap; G1 names the real local scripts and what
  they source today.
- `CI04-HOOKS.md`: `scripts/core/catalog.py` -> `ci-skills/lib/core/catalog.py`; note `Makefile:48-49` sets
  `core.hooksPath .githooks` (absent) and that this plan does not install hooks.
- `CI06-TESTS.md`: section per phase keeps its name; add the CI09 knowledge tests above; "Existing coverage" gets the
  2026-10-06 observation that the suite cannot import (F3) and that no execution surface exists (F1).
- `README.md`: fix the eight stale paths/commands in F3; `scripts/README.md`: replace the tree with the real one.

## Proposal D: make `ci-skills` an Agent-Skills package that works in every Codex scope (operator requirement)

The operator's instruction: the skill must work the same way in the Codex scopes above. What that takes:

1. **Self-contained package.** Everything an installed copy needs sits under the skill root: `SKILL.md`, `tools.json`,
   `bin/`, `lib/`, `references/`, plus the two official optional entries this plan adds, `agents/openai.yaml` and
   `assets/`. No path may reach outside the skill root at run time (block 0's single locator resolves `lib/` from the
   main's own location, never from the repository). Target files keep resolving through `catalog.TARGET_PROTOCOL`
   (argument, `CI_SKILLS_TARGET`, `./.ci-skills/target.toml`, `~/.ci-skills/target.toml`), which is independent of
   where the skill is installed, so behaviour is identical in every scope.
2. **Scope-aware install, no invented default.** `tools/install_ci_skills.py` (and `install.sh`) gain `--scope` with
   the official destinations, derived from the scope table, never typed twice:

   | `--scope` | Destination | Who commits or owns it |
   | --- | --- | --- |
   | `repo` | `<git root>/.agents/skills/ci-skills` | a consuming repository (teams check the copy in) |
   | `user` | `$HOME/.agents/skills/ci-skills` | the operator's machine |
   | `admin` | `/etc/codex/skills/ci-skills` | machine or container image |
   | `codex-home` | `$CODEX_HOME/skills` or `~/.codex/skills` (today's behaviour) | kept until verified obsolete (F4) |
   | `claude-user` / `claude-project` | `~/.claude/skills/ci-skills` / `.claude/skills/ci-skills` | Claude Code equivalents |

   `--skills-dir PATH` stays for any other location. The current default is not changed by this plan; which scope
   becomes the default is the operator's (open decision D-SCOPE). For this repository itself no `.agents/skills/`
   copy is added: `ci-skills/` is the source of truth and a committed self-copy would be a second implementation.
   Whether Codex follows a symlink at `.agents/skills/ci-skills -> ../../ci-skills` is unverified; the installer
   refuses symlinks on purpose (CI01-CATALOG), so a copy is the supported form.
3. **`agents/openai.yaml`, generated, one declaration.** Rendered by `tools/render_manifest.py` from the catalog and
   the `SKILL.md` frontmatter beside `tools.json`, and byte-compared by the same test, so `display_name`,
   `short_description` and `default_prompt` cannot drift from the skill `description`:

   ```yaml
   interface:
     display_name: "CI Skills"
     short_description: "GitLab CI, Kubernetes and OpenShift diagnostics and operations"
     icon_small: "./assets/ci-skills-small.svg"
     icon_large: "./assets/ci-skills.png"
     brand_color: "#..."
     default_prompt: "Diagnose the failing CI job, pipeline or cluster component with ci-skills and report evidence."
   policy:
     allow_implicit_invocation: true
   dependencies:
     tools: []
   ```

   Facts behind the values: the skill has no MCP dependency (its tools are `gh`, `glab`, `kubectl`, `oc`, `jq`,
   `yq`, already declared per command in `tools.json` `required_tools`; whether `openai.yaml` accepts a non-MCP tool
   type is unverified, so none is declared). `allow_implicit_invocation: true` is the recommendation because every
   mutating command already defaults to a dry-run plan and requires `--apply --confirm-plan DIGEST` (gh-fix-ci sets
   `false` because it edits code); the operator decides. Icons and `brand_color` are operator-supplied assets; the
   plan adds the `assets/` directory and the two filenames, not invented artwork.
4. **Description written for implicit invocation.** The `SKILL.md` `description` (max 1,024 characters) must name
   the trigger terms the scope table's implicit mode matches on: GitLab job, pipeline, merge request, runner,
   milestone, issue, wiki, Kubernetes storage, events, Cilium, Ceph, MTU, `.gitlab-ci.yml` keyword. Today's text
   covers most of these and misses "merge request" and the YAML keyword reference.
5. **Receipt and digest.** `agents/openai.yaml` and `assets/` are skill bytes inside `tree_digest`; they change the
   skill digest once, like any metadata change (D-DIGEST concerns only `references/vendor/**`).

## Proposal E: a merge-request checker, modeled on `gh-fix-ci`, reusing the job and pipeline readers

The operator asked whether to add an MR checker and whether job and pipeline checkers are needed. Job and pipeline
readers already exist: `ci-skills/bin/gitlab_job.py` (one job with pipeline, runner and a bounded 200-line trace)
and `ci-skills/bin/gitlab_pipeline.py` (one pipeline with stage and job counts). What is missing is the MR level
that `inspect_pr_checks.py` provides for GitHub: from one merge request to its failing checks and their failure
context, in one bounded record.

| Part | Value |
| --- | --- |
| Capability | read one merge request's merge state, head pipeline, failing jobs and a failure snippet per failing job |
| Owner | `ci-skills/lib/core/gitlab_merge_requests.py` (new), composing `gitlab_pipelines` and the trace reader in `collect.py` |
| Entrypoint | `ci-skills/bin/gitlab_mr.py check --mr-url URL` or `--project PATH --mr-iid N` (thin main, catalog entry, read-only) |
| Result | `gitlab_mr` record: `merge_request` (iid, state, `detailed_merge_status`, `has_conflicts`, approvals, unresolved discussion count), `pipeline` (id, status, sha), `results[]` per failing job (name, stage, `web_url`, status `failed|external|trace_pending|trace_unavailable`, `log_snippet`, `log_tail`) |
| Read-back | the same GET reads twice give the same record; `--search TEXT` filters `results[]` |

Algorithm, reusing one transport (`core.gitlab_api.GlabAPIClient`, GET only): resolve the project and iid from the
URL or options; `GET /projects/:id/merge_requests/:iid` (state, `detailed_merge_status`, `has_conflicts`,
`head_pipeline`); `GET .../merge_requests/:iid/approvals`; discussions with `resolvable && !resolved` counted;
`GET /projects/:id/pipelines/:pid/jobs?scope=failed` (and `bridges` for downstream triggers, reported as `external`
with URL only, the gh-fix-ci rule); per failing job the existing bounded trace read with the tail (`--max-lines`,
default 160) and a failure window (`--context`, default 30, around the first `ERROR|error:|FAIL|Traceback|exit code`
marker); everything sanitized by `core.runtime.redact`. Status: `PASS` when the head pipeline succeeded and nothing
failed; `PARTIAL` with `access_proven: true` when failing jobs are reported (the same semantics as an unhealthy
Cilium agent); `BLOCKED` when the MR, pipeline or jobs could not be read. Tests: mocked `glab` for a green MR, a
red MR with two failed jobs, a bridge job, a trace that is pending, a 401, a 429 with Retry-After, redaction, and
the no-pipeline case; `tests/acceptance/expected.toml` gains one `gitlab_mr` receipt kind when the operator names
the MR to prove against. Routing: "a merge request's checks failed" -> `gitlab_mr.py`. Proposed, not authorized.

## Proposal F: the tool catalogue and the port of the source repo's scripts (operator instruction, 2026-10-06)

Operator requirements, verbatim in substance: no open-ended implementation and no invention; each tool does one
task the operator names, with a machine-readable spec; GitLab job fetch, track and logs; the same for pipelines;
pipeline schedules; OpenShift views for routes, HA and machines, and the build-ISO action; toolbox build, push to
Harbor, create Harbor robot, standardized for any Harbor from a `~/.ci-skills` config entry; port the source
repo's Bash scripts (`$SOURCE_REPO/scripts/`, read-only, operator-authorized) to Python with the one
interface; run independent collection steps concurrently; and for each tool the phase doc must name the source
script by full path, where it is copied to in `ci-skills`, and how it is adapted.

### The catalogue (one row per tool; source column filled from the inventories, never guessed)

| Operator task | Tool (thin main) | Library module | Authority | Mutates | Config keys (`target.toml`) | Source script (full path) | Concurrency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GitLab job fetch | `gitlab_job.py fetch` (today's default) | `core/collect.py`, `core/gitlab_pipelines.py` | gitlab | no | `[gitlab] url` | exists in `ci-skills`; source rows pending inventory | job, pipeline, runner reads in parallel |
| GitLab job track | `gitlab_job.py track --until terminal --interval S --timeout S` | `core/gitlab_jobs.py` (new; poll loop with bounded attempts) | gitlab | no | same | pending inventory | one poll per interval |
| GitLab job logs | `gitlab_job.py logs --lines N --search TEXT --failure-window N` | `core/gitlab_jobs.py` reusing the bounded trace read | gitlab | no | same | pending inventory | none |
| GitLab pipeline fetch | `gitlab_pipeline.py fetch` (today's default) | `core/gitlab_pipelines.py` | gitlab | no | same | exists; pending inventory | stage and job reads in parallel |
| GitLab pipeline track | `gitlab_pipeline.py track ...` | `core/gitlab_pipelines.py` | gitlab | no | same | pending inventory | one poll per interval |
| GitLab pipeline logs | `gitlab_pipeline.py logs` (failed jobs' bounded traces) | `core/gitlab_jobs.py` | gitlab | no | same | pending inventory | one trace read per failed job, in parallel |
| GitLab pipeline schedules | `gitlab_schedule.py list\|play\|create\|update` | `core/gitlab_schedules.py` | gitlab | `play`, `create`, `update` | same | pending inventory | none |
| Merge-request checks | `gitlab_mr.py check` (Proposal E) | `core/gitlab_merge_requests.py` | gitlab | no | same | pending inventory | trace reads in parallel |
| OpenShift routes, full view | `ocp_route.py` | `core/ocp_routes.py` | kubernetes (OpenShift API) | no | `[kubernetes]` | pending inventory | per-route readiness probes in parallel |
| OpenShift HA view | `ocp_ha.py` | `core/ocp_ha.py` | kubernetes | no | `[kubernetes]` | pending inventory | nodes, etcd, cluster operators, MCP reads in parallel |
| OpenShift machine view | `ocp_machine.py` | `core/ocp_machines.py` (MachineSets, Machines, nodes against the reference node spec) | kubernetes | no | `[kubernetes]`, `[openshift.node_spec]` | pending inventory | per-machine reads in parallel |
| OpenShift build ISO | `ocp_iso.py plan\|build\|serve` | `core/ocp_iso.py` | kubernetes, local tools | `build`, `serve` | `[openshift.iso]` | pending inventory | per-node ISO renders in parallel |
| k8s state snapshot | `k8s_state.py` | `core/collect.py` (existing collectors composed) | kubernetes | no | `[kubernetes]` | pending inventory (the serial source) | every independent list read in parallel |
| Toolbox build | `toolbox_build.py plan\|apply` | `core/toolbox.py` composing the existing binary-build plan | kubernetes or local podman, harbor | `apply` | `[toolbox]`, `[harbor]` | pending inventory | none (one build) |
| Push to Harbor | `harbor_push.py plan\|apply` | `core/harbor_api.py` + `core/toolbox.py` | harbor | `apply` | `[harbor]` | pending inventory | none |
| Create Harbor robot | `harbor_robot.py create` (`--token-out`, like runner create) | `core/harbor_api.py` | harbor | yes | `[harbor]` | pending inventory | none |
| Harbor sanity | `harbor_sanity.py` | `core/harbor_api.py` | harbor | no | `[harbor]` | pending inventory | project, repository, robot reads in parallel |

Rows marked "pending inventory" are filled from the four read-only inventories of the source repo running on
2026-10-06 (GitLab utilities, OpenShift and k8s, toolbox and Harbor, core libraries). A row with no source script
and no operator sentence behind it does not exist: nothing is added on speculation.

### The port recipe (the same ten steps for every row; the phase doc repeats them per row with the real paths)

1. **Point.** Name the source script by full path and the function(s) and lines that implement the task; name its
   tests in the source repo.
2. **Bound the task.** One tool does one operator-named task. A source script that does several becomes several
   thin mains over one library module; nothing a source script does not do is added.
3. **Library first.** Put the logic in `ci-skills/lib/core/<module>.py` as functions over the existing transports:
   `core.gitlab_api.GlabAPIClient` for GitLab, `core.runtime.run_command_bounded` for `oc`, `kubectl`, `podman`,
   `skopeo`, one new bounded HTTP client `core/http.py` (standard library) shared by the Harbor API and the
   reference fetch, never a second transport, envelope, logger, exit table or redaction.
4. **Declare.** Add the command to `catalog.COMMANDS` (`requires`, options, subcommands, `mutates`, `returns`,
   `required_tools`), add a new authority where one is needed (`harbor`: target `[harbor]` `url`, `project`,
   `robot_file`; credential chain target file -> `HARBOR_ROBOT_FILE`/declared variables -> none, reported in
   `credential_sources` like the others), regenerate `tools.json`; the navigator picks it up with no further work.
5. **Thin main.** `ci-skills/bin/<name>.py`: `build_parser()` plus one `execute` call, with `import _bootstrap`.
6. **Strip the project.** Every host, project, group, namespace, image name, robot name, path and timeout moves to a
   `target.toml` key or a flag; the neutrality gate (F3a) enforces the absence of the source project's name.
7. **Mutations.** Plan by default with `plan_digest`, `--apply --confirm-plan DIGEST`, read the resource before and
   after, independent read-back, cleanup on every exit, idempotent second run; the plan/apply/read-back skeleton is
   extracted once from `core/gitlab_actions.py` (today GitLab-specific) into a shared `core/action.py` so Harbor and
   toolbox actions reuse it.
8. **Concurrency.** Independent reads run through the executor pattern `core/collect.py` already uses; the
   inventory names which serial steps become parallel, with the bound on workers and the per-call timeout.
9. **Tests.** Mocked external commands via `conftest.py` fixtures; the pinned mutating matrix for every `apply`;
   a receipt kind in `tests/acceptance/expected.toml` for live proof when the operator names the target.
10. **Docs.** The catalog entry renders `tools.json`; one routing row in `SKILL.md`; the phase doc's row carries
    source path, destination paths, adaptation notes and the receipt kind. The source repo is not edited.

### Config entries this adds to `target.toml.template` (nonsecret; credentials by file reference only)

```toml
[harbor]
url = "https://harbor.example.test"
project = "toolbox"
# robot_file = "/home/operator/.ci-skills/credentials/harbor.robot"   # robot name and secret, mode 0600

[toolbox]
image = "toolbox"                 # repository name under harbor.project
containerfile = "platforms/component/toolbox/Containerfile"
context = "."
build = "openshift"               # or "podman"; keys below follow the chosen builder
namespace = "ci-build"            # OpenShift build namespace

[openshift.iso]
source = "rhcos"                  # which ISO source the source repo's script supports
node_spec = "scripts/ocp/reference-node-spec.yaml"   # path in the consuming project
serve_port = 8080
```

Every key's exact name and meaning is taken from the source script that reads it (inventory), not invented here.

## Placement decision (operator choice, recommendation given)

Where does the maintenance code (vendor update, reference update, discovery, install) live?

- Recommended, the split the design pass derived from the harness: everything an installed copy executes lives in
  `ci-skills/lib/core/` (`reference.py`, `paths.py`, the offline CLI path), because `get`, `list` and `verify` must
  run offline from `~/.codex/skills` with no `tools/`; everything only a checkout runs lives in a maintenance package
  `tools/skillkit/` (`transaction.py`, `fetch.py`, `reference_update.py`, later `vendor.py` and `discover.py`),
  imported by the thin mains `tools/update_reference.py` and `tools/install_ci_skills.py`. `skillkit` imports `core`
  from `ci-skills/lib`; there is one `core` package, so the collision that `CI05-VENDOR.md:175-187` worried about
  does not arise, and no inert maintenance code ships in the skill or churns its digest. The repository layout guide must then draw `ci-skills/lib/core/` (not `lib/python/core/`) and `tools/skillkit/`.
- Alternative, pure Option 1: all Python under `ci-skills/lib/core/`, `tools/*.py` thin. Cost: the installed skill
  carries the transaction, fetch and update code it never runs, and every edit to them changes the skill digest.
- Alternative, Option 2: a second library at the repository root (`lib/python/...`, which the tree does not have). Cost: two Python libraries and an import direction to police.

The repository layout guide draws `lib/bash/core/` and `lib/python/core/` while the tree has `ci-skills/lib/core/`
(Python) and `ci-skills/lib/bash/{core,ci,automation}/`; whichever the operator confirms, the docs and the guide
must draw the same tree.

## Delivery (one PR per block; order)

0. **Prerequisite repairs** (code, small): re-point the F3 consumers to the current tree through ONE shared locator
   (`ci-skills/bin/_bootstrap.py` plus one import line per main; `tools/*.py` and `tests/python/conftest.py` point at
   `ci-skills/lib`); the exact file:line list is in the design journal under `plan:code-reuse`
   (`prerequisite_repairs`, 30 entries: the 15 mains, `ci-api:5-6`, `ci-binary-build:5-6`, `lib/bash/ci/api.bash:5-7`
   and `lib/bash/automation/binary_build.bash:5-7` (`../..` -> `../../..`), the root adapters, `conftest.py:18-19`,
   `test_installed_package.py:16` and its eight `scripts/` -> `bin/` paths, `test_skill_package.py:11,19-28`,
   `test_render_manifest.py:21,39-40`, `tools/render_manifest.py:27,32`, `tools/install_ci_skills.py:29-32`,
   `tools/check_live_acceptance.py:451,643,657`, `ci-skills/benchmarks/event_trace_worker.py:38-42`,
   `tests/bash/api.bats:4`, `tests/bash/install.bats:5,...`, `SKILL.md:14,38,186,187,201,204,219`, `README.md`,
   `credentials.py:111,132` via `paths.py`); fix `conftest.py:17`; remove the foreign content behind the three
   neutrality violations (F3a); drop the three empty `__init__.py` files. Exit criteria: `tools/render_manifest.py
   --check` reports `CURRENT`, `ci-skills/bin/ci-api --help` exits 0, `env -i python ci-skills/bin/storage_report.py
   --describe` prints the contract, the neutrality checker reports `PASS`. No behaviour change to any command.
   Reported, not repaired here: `scripts/check.sh:6` and `tests/bash/check.bats:5` source the deleted
   `lib/ci/check.bash` (D-GATE, G1 restores it at `ci-skills/lib/bash/ci/check.bash`).
1. **Docs PR** (Proposal B + the CI09 text of Proposal A): `docs/phases/*.md`, `README.md`, `scripts/README.md`;
   no skill bytes change, so no receipt.
2. **CI09 implementation PR**: `core/reference.py`, `bin/reference.py`, `tools/update_reference.py`, schema, tests,
   `ci-skills/references/vendor/gitlab-ci-yaml/` (152 chunks or one file plus outline per D-LAYOUT, `LICENSE.md`,
   `index.json`), catalog `REFERENCES` entry rendered into `tools.json`, one `SKILL.md` line. Depends on
   CI05-VENDOR's transaction and CI07-SCHEMA's gate existing, or lands them minimally within this PR per CI10's
   shared-file allowance.
3. **Scopes and `agents/openai.yaml` PR** (Proposal D): installer `--scope`, the rendered `agents/openai.yaml`,
   `assets/` with operator-supplied icons, the description terms, README install section per scope; one fresh
   receipt (skill bytes change).
4. **MR checker PR** (Proposal E), only if authorized: `core/gitlab_merge_requests.py`, `bin/gitlab_mr.py`, catalog
   and routing entries, tests, `expected.toml` receipt kind, one fresh receipt.

## Verification

- Local, allowed now: `ruff check` and `ruff format --check` in the `ci-skills` conda env; markdownlint-cli2 with the
  committed config; the relative-link scan used in F3 (one broken link today); `tools/render_manifest.py --check`
  once block 0 lands; `tools/check_project_neutrality.py --root . --json` (FAIL today, F3a; PASS is block 0's exit
  criterion).
- Static results observed read-only on 2026-10-06 by the gates design pass (the state block 0 and the docs PR start
  from): `ruff check .` 58 errors (I001 30, UP017 22, N999 5 on `ci-skills/**/__init__.py` because `ci-skills` is not
  an importable package name, FURB162 1) and 5 files unformatted; markdownlint-cli2 0.23.2 13 issues in 2 files
  (`scripts/README.md`, `docs/gitlab-toolbelt-plan.md`); `git diff --check` 9 findings, all in
  `docs/gitlab-toolbelt-plan.md`; gitleaks 2 historical findings (a fixture at `038aa79`, file no longer at HEAD;
  `.gitleaks.toml:4-6` allowlists only `docs/`); neutrality FAIL (F3a); shellcheck SC1091 x18 (missing sourced files);
  yamllint PASS; `bash -n` PASS.
- Tests: BLOCKED until D-GATE names a surface (no CI, and `agent-workspace.md:172` forbids laptop runs). The plan
  names the tests; it does not claim they ran.
- Live: `reference.py get` needs no credentials; the only live step is `update --confirm` against gitlab.com at the
  pinned SHA, run by the operator or on the named executor, with the fetched byte counts read back against the
  index.
- Read-back after block 3: `bin/reference.py get gitlab-ci-yaml trigger:forward --json` returns one record whose
  `bytes` equals 2587 and whose `url` ends in `#triggerforward`; `verify` reports PASS; `tools/render_manifest.py
  --check` reports CURRENT.

## Requirement traceability

| Requirement | Implementation | Test / gate | Observed status |
| --- | --- | --- | --- |
| Reference for `trigger:forward` loadable on demand, grounded in Claude + OpenAI skill docs | Proposal A in `docs/phases/CI09-REFERENCE.md`; later `core/reference.py` | anchor oracle, chunker, verify, closed-world | proposed; grounding fetched 2026-10-06 |
| Finish CIxx renaming in doc bodies and cross-references | Proposal B | link scan, markdownlint, `git grep` for old names returns nothing | 135 stale names counted; not yet edited |
| Grounded detail per phase (algorithms, load/knowledge/memory) | Proposal B per-doc list | review | pending audit fold-in |
| Layout and no-dual-implementation reflected | path rule + Placement decision | `tools/render_manifest.py --check`; QA reuse review | consumers still stale (F3) |
| Merge gate | none exists | — | BLOCKED: D-GATE |
| Skill works the same way in every Codex scope; official layout with `agents/openai.yaml` | Proposal D | installed-package smoke per scope path; manifest byte-equality covers `openai.yaml` | proposed; scope table fetched 2026-10-06 |
| MR checker analogous to `gh-fix-ci`, reusing job and pipeline readers | Proposal E | mocked `glab` matrix; `gitlab_mr` receipt | proposed, not authorized |

## D-GATE: the three gate routes are roles, not alternatives (gates design pass, evidence cited)

- **G1, restore `.github/workflows/validate.yml` from `1108cca^`** as the fast, blocking, non-mutating GitHub
  preflight. The proposal described a fast GitHub preflight; its authority must be resolved before implementation.
  Needed edits to the old file: the shell list
  (`:80-89`, old `skills/ci-skills/...` paths), the bats glob (`:95`, now `tests/bash/*.bats`), the pytest paths
  (`:107`, `:111`, now `tests/python/...`), the acceptance call (`:118`, add `--expected tests/acceptance/expected.toml
  --receipts tests/acceptance/receipts --skill ci-skills`), and the `non_markdown_count` conditions, which the pinned
  `ci.md` forbids (zero skipped required tests; CI03 G6). Then re-enable protection with the `validate` context.
  Smallest change; also the precondition for any receipt (F1). Today a verbatim restore is red on six steps.
- **G2, a `.gitlab-ci.yml` on the operator's one home CI (`gitlab.rnd.embedings.ai`)** for the authoritative test
  run in Kubernetes, ending in one aggregator that posts one required context to GitHub for the exact SHA. The only
  route the binding rules call authoritative for tests; nothing exists yet, and the host answered HTTP 000 from this
  session, so every G2 fact (project, mirror, runner tag, token) is unverified and needs the operator's inventory.
- **G3, local `make`/`bless.sh`/`scripts/check.sh`** can only ever be the advisory pre-commit body (CI04-HOOKS), never
  the merge gate; today it is further from working than either CI route (16 missing library files).

Recommendation, stated as a recommendation: G1 first in its own PR (unblocks `gh pr checks` and receipt capture),
G2 as the CI03-GATES G8 route when the operator supplies the inventory, G3 only after CI05's libraries exist.

## Decisions recorded 2026-10-06 and the refuter's corrections

Operator correction C1 (2026-10-06, after approval): the agent interface is the black-box navigator
`reference.py next DOMAIN [TAG ...]` described in Proposal A; it is generic over every declared reference and the
skill's commands, answers with a small tag menu, and ends in one `get` or one command. The plain `get` stays as the
leaf. CI08-ROUTING's three hops are implemented by this one tool, and `SKILL.md` routes to it with one sentence:
"For anything GitLab or Kubernetes you cannot name exactly, run `bin/reference.py next gitlab <tag>` (or
`kubernetes`), follow the `next` command of the choice that fits, and run the leaf it hands you; never read
`index.json`, `tools.json` or the upstream page whole."

Operator answers: **D-GATE = no gate for now** (so every test and receipt claim stays UNVERIFIED by decision; the
docs and code PRs land on static checks only; CI03's new G0 records "decided 2026-10-06: no gate for now, revisit");
**D-DIGEST = exclude vendored text** (the lock, kept outside the excluded subtree, becomes the integrity root for
`index.json` and the chunk digests; the subtree digest is reported beside `skill.digest`); **D-HOME = extend
CI09-REFERENCE**; **D-LAYOUT: the operator asked for something better than the two options**, answered below.

### D-LAYOUT, improved: the tree is the index, with three derived tiers

Neither flat dotted files nor one big file is best. Mirror the keyword path as directories, so the filesystem itself
is the browsable index and an agent can `ls` its way to a keyword without loading anything:

```text
ci-skills/references/vendor/gitlab-ci-yaml/
├── LICENSE.md                 CC BY-SA 4.0 notice stating that changes (chunking) were made; MIT notice for ci.json fields
├── index.json                 indented JSON (grep-able), kind reference_index; the tool's and the lock's record
├── _preamble.md               upstream bytes before the first heading
├── _sections/job-keywords.md  the 7 prose headings
├── variables/_index.md        the H2 keyword; `_index.md` mirrors upstream's own naming
├── trigger/_index.md          the keyword's own text (keyword type, supported values, example, details)
├── trigger/forward.md         one H4 chunk, verbatim (2,587 B)
└── cache/key/files.md         an H5 keyword; the path is the keyword path
```

Three tiers, all derived at `structure` time or sliced at run time, never hand-written, so one declaration per fact:

| Tier | What `get` returns | Source | Size for `trigger:forward` |
| --- | --- | --- | --- |
| card (`--card`) | type, default, enum, one-line description per subkey | `ci.json` node(s), JSON Pointers listed | about 0.5 KB |
| part (`--part values|example|details|related`) | one labelled sub-section when the chunk has it (**Supported values** is present in 150 of 167 chunks, **Example** in 151, **Additional details** in 101, **Related topics** in 55; absence is reported, never an error) | line offsets recorded in the index | about 0.7 KB |
| chunk (default) | the whole verbatim chunk plus `links` resolved to published URLs | the chunk file | 2,587 B |

`list` and `--search` match full keyword paths (leaf names repeat 27 times across the 167 paths, so `load_when`
holds full paths only and `keyword_ambiguous` lists the candidates). The one-file-plus-outline shape stays available
as `--live` output only.

### Corrections from the refuter, applied to the design

- Chunks end with `---` and a blank line, which `git diff --check` flags as "new blank line at EOF": add
  `ci-skills/references/vendor/** -whitespace` to `.gitattributes`; add `ci-skills/references/vendor/**` to the
  markdownlint `ignores` (today's entries are placeholders).
- Upstream relative links: 377 `.md` links and 337 anchor links; resolving `x.md` against `link_base` yields a
  302 to an auth page, the published pages drop `.md` and `_index.md`. The resolver maps `path/x.md` -> `path/x/`,
  `path/_index.md` -> `path/`, `#anchor` -> the page's own anchor; `get` prints the resolved `links` and leaves bytes
  untouched.
- `schema_path` becomes `schema_paths`, RFC 6901 JSON Pointers, listing every match (`forward` sits under both
  `/definitions/job_template/properties/trigger/oneOf/0/properties/forward` and `.../oneOf/1/...`).
- The upstream sub-structure is usual, not fixed; `keyword_type` needs an explicit H2 mapping with `variables` as
  its own case; 96 Hugo shortcodes (`history` 76, `details` 16, `icon` 4) and `[!note]`, `[!warning]`, `[!flag]`
  alerts stay verbatim, and one sentence in the reference's `README` tells the agent what a `details` block carries
  (tier and offering).
- Heading scans skip fenced code (15 heading-like lines sit inside fences, 7 of them in `trigger:forward`'s
  example); Hugo's `-N` suffix for duplicate ids must be implemented or refused on refresh (none at this SHA).
- The Codex page documents `.agents/skills` paths, not `~/.codex/skills`: Proposal D's `--scope user`
  (`$HOME/.agents/skills`) is the documented destination; the installer's current default is undocumented.
- `SKILL.md` must name paths relative to the skill root once (Claude Code offers `${CLAUDE_SKILL_DIR}`, Codex does
  not), and the block-0 locator must fail with a machine-readable envelope, not a traceback.
- The transaction is extracted from the installer, not written again (already in the code design).

## Open decisions for the operator (asked at the end of planning)

- **D-GATE**: decided, no gate for now. Verification is static only; tests are written with each block and run
  when a route exists; receipts are not capturable until protection returns.
- **D-DIGEST**: decided, exclude vendored text (the `VENDOR_DIRECTORIES` change above, lock as integrity root).
- **D-HOME**: decided, extend CI09-REFERENCE.
- **D-LAYOUT**: proposed above (tree mirrors the keyword path; card, part and chunk tiers); approval of this plan
  approves it.
- Proceeding on stated assumptions unless the operator objects: Proposal D keeps today's install default and adds
  `--scope`; `allow_implicit_invocation: true`; icons and brand colour are supplied by the operator; Proposal E is
  written up but not built until authorized (D-SCOPE and the MR checker are not blocking questions).
- Reported, not asked: the binding pin (F2); the Codex install path (F4); the repo license (F5); the neutrality
  failure (F3a); the stale receipts (F1).
