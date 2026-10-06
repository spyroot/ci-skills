# CI09-REFERENCE: references, tool operations and upstream knowledge

Status: proposed. Depends on: CI01-CATALOG, CI07-SCHEMA and CI02-CLI; the
knowledge kind also depends on CI05-VENDOR (one lock, one transaction).
Followed by: CI08-ROUTING, whose three hops this phase implements as one
tool, and CI11-TOOLS, whose commands the same tool advertises.

A reference is something an agent consults before acting. This phase owns two
kinds, declared with the same `uses` pointers (CI07-SCHEMA):

- **operations** (section 1): what our code calls, one declared operation per
  tool invocation, checked against the installed binary;
- **knowledge** (section 2): what an agent reads, one upstream page vendored
  as verbatim chunks with a derived index, served on demand.

Section 3 is the navigator, the one command through which an agent (or
another agent on its behalf) asks "what can this skill do about X, and where
do I look next" and gets a bounded answer. Section 4 is the lifecycle every
knowledge reference follows. Everything here was measured on 2026-10-06
against the pinned upstream commit named below; nothing is estimated where a
number could be read.

## 1. Tool operations

### Context

Our skills and tools call many command-line tools. Two lists already name
them:

- **Our own tools:** the `ci-skills/bin/ci-*` commands (from PR #2, merged through #16), `ci-api`
  (read-only GitHub and GitLab API reads) and `ci-binary-build` (an
  exact-commit OpenShift build plan), plus `scripts/check.sh`.
- **The toolchain contract** from the shared standards
  (`bin/agent-tools.sh`): 61 tools observed on 2026-10-02, each named by
  full path, under the rule "call every tool by full path".

To choose a command and its flags, an agent today reads `--help` text or
upstream documentation. Two things are missing:

- a record of which operations our skills rely on;
- a check that fails when an upgrade renames or removes one.

CI05-VENDOR copies upstream trees, which are prose. Section 1 covers the tools
our code calls; section 2 covers prose an agent reads.

Measured on 2026-10-02 against the local binaries:

| Tool | Version | Top-level commands | Example |
| --- | --- | --- | --- |
| `glab` | 1.120.0 | 48 | `glab mr create`: 31 flags |
| `gh` | 2.98.0 | 35 | `gh help reference`: 94,244 bytes |
| `kubectl` | v1.36.4 | 45 | `kubectl get`: 53 flags |

All three are built with cobra, which answers a hidden `__complete` command
with machine-readable lines. The command exists for shell completion,
appears in no `--help` output, and is not a documented interface.

### What the skills call today

Taken from every call site, not just the obvious ones:

| Tool | Calls |
| --- | --- |
| `gh` | `auth status`; `api` reads, with no request body |
| `glab` | `auth status`; `api --method GET` |
| `kubectl` | `get`; `auth whoami`; `auth can-i`; `config view` |
| `kubectl` | `exec` with a fixed `cilium-health` command |
| `cilium` | node status on a selected node (PR #5) |
| `ceph` | `status`, `health`, `osd`, through `kubectl exec` (PR #7) |

A command name alone cannot say whether a call is read-only. `gh api` and
`glab api` can send a POST, and `kubectl exec` runs any command it is given.
What is read-only is the operation: the command together with its method,
its endpoint class and its fixed arguments.

`ci-skills/SKILL.md` (section 9) says to use `ci-api` instead of one-off `gh api` or
`glab api` reads. The k8s skill makes such reads itself. Whether it should
call `ci-api` instead is open, because an installed skill loads no code from
another skill.

Constraints:

- **Compute rather than copy.** What can be computed at runtime is
  computed, not copied.
- **One declaration** per fact.
- **CI is offline and deterministic.**
- **Upstream text keeps its license.**
- **Compact for agents.**
- **No implicit files.** Nothing is written without an explicit output path.

### Names: tool, operation, option

- **Tool:**
  - `name`: the binary.
  - `version`: read from the tool's own version command (`glab --version`,
    `gh --version`, `kubectl version --client -o json`).
  - `digest`: the SHA-256 of the resolved executable.
  - `source`: how the record was collected.
- **Operation:** one way our code calls a tool.
  - `id`, `<tool>:<operation>`: for example `glab:api.get` or
    `kubectl:exec.cilium-health`.
  - `argv`: the fixed argument prefix.
  - `method`: for API calls.
  - `flags`: the flags we pass.
  - `mutates`: true unless the method is a read and the argument list is
    fixed. An unbounded `api` or `exec` call counts as mutating.
- **Option:** a `flag` and its `summary`.

Schema: `tool-operations` (CI07-SCHEMA).

```json
{
  "tool": "glab",
  "version": "1.120.0",
  "digest": "...",
  "operations": [
    {
      "id": "glab:api.get",
      "argv": ["glab", "api", "--method", "GET"],
      "flags": ["--hostname"],
      "mutates": false
    }
  ]
}
```

### How references relate to tools

Skill commands and references point at operations through `uses`, declared
once on the user's side (CI07-SCHEMA, "Pointers"). For example,
`references/access.md` uses `gh:auth.status`, `glab:auth.status`,
`kubectl:auth.whoami` and `kubectl:auth.can-i`.

`bin/ci-skills tools NAME --describe ID` adds the reverse, `used_by`,
computed by discovery and never stored. Every `uses` id must resolve, and
every declared operation must be used.

### Options considered

#### Option A: copy help text and documentation into the repository

- **Pros:**
  - the fullest text, with examples;
  - needs no binary to read.
- **Cons:**
  - it is large: `gh help reference` alone is 94,244 bytes;
  - it goes stale on every upgrade;
  - options must be parsed out of prose;
  - copied text carries license duties.

#### Option B: a generated registry, committed

Crawl the tools, commit the normalized result, and check it is current, as
`tools.json` is.

- **Pros:**
  - structured;
  - upgrades show as diffs;
  - CI needs no binaries.
- **Cons:**
  - it commits what can be computed at runtime;
  - it matches only the host where it was regenerated.

#### Option C: runtime discovery through `__complete`, nothing stored

- **Pros:**
  - always matches the installed binary;
  - nothing to keep current.
- **Cons:**
  - a hidden protocol;
  - the binary must be present;
  - completion output mixes in argument choices, active-help lines and a
    final directive, and some completions run callbacks.

#### Option D: declared operations, checked against the installed tool

Declare each operation our code uses, as above, in one module of
`ci-skills/lib/core/` (Placement, CI10-PHASES). A contract test checks every declared command and flag
against the installed binary, the same pattern `tests/python/test_catalog.py` uses
for the k8s skill's parsers.

- **Pros:**
  - a small, exact list of what we depend on;
  - an upgrade that removes a used flag fails;
  - read-only claims are explicit.
- **Cons:**
  - hand-maintained;
  - not a full index;
  - the live check needs the binaries.

#### Option E: an external search index

- **Pros:**
  - fuzzy search at scale.
- **Cons:**
  - a service to run, with network and credentials;
  - far beyond our needs;
  - breaks offline CI.

### Decision

Proposed: **D, with C as optional navigation.**

- **Declare.** Every operation our code uses is declared, with its exact
  `argv`, method, flags and `mutates`, in one module of `ci-skills/lib/core/`. The
  list is derived from every current call site. A gate asserts that no
  unbounded `api` or `exec` operation is labelled read-only.
- **Check against the real tool.** On the installed binary, every declared
  command and flag must resolve. The check records the binary's path,
  version and digest, and runs:
  - in CI, against recorded fixtures, which test only the parser;
  - on an approved executor with the real binaries, which proves
    compatibility. Which executor is open; see the open decision below.
- **Navigate.** `bin/ci-skills tools [NAME]` lists tools and their declared
  operations. Its `--complete` option walks `__complete` for a wider view.
  Every query is bounded in time and output, argument values are never
  completed, directive and active-help lines are dropped, and the parser is
  tested against recorded and hostile output.
- **Our own tools.** Each `bin/ci-*` command gains `--describe` (CI02-CLI).
  Discovery reads that instead of help text; these tools are Bash, not
  cobra.
- **Contract.** The `tools` verb follows CI02-CLI: bounded JSON, the shared
  exit codes, and `tool_missing` with a safe next step when a binary is
  absent.

### Consequences

- **Easier:**
  - choosing an operation without reading whole help pages;
  - catching a removed flag at upgrade time;
  - proving that read-only skills stay read-only.
- **Harder:**
  - a second place to update when code adds a call;
  - the `uses` closed-world check catches a missed one.
- **Revisit:**
  - when a tool not built with cobra is added;
  - if upstream publishes a machine-readable reference.

### Steps

1. Derive the declared operations from every call site, including
   `kubectl auth can-i` and the fixed `exec` arguments.
2. Add the `tool-operations` schema (CI07-SCHEMA) and the declaration file.
3. Add the `tools` verb and the contract test, with recorded fixtures for
   `glab` 1.120.0, `gh` 2.98.0 and `kubectl` v1.36.4.
4. Add the `uses` pointers (CI08-ROUTING).
5. Open one pull request after CI01-CATALOG; the gate route D-GATE names
   must pass (CI03-GATES, G0).
6. Read back: `bin/ci-skills tools` lists every tool and operation, and each
   operation shows `used_by`.

### Open decision for tool operations

- Which approved executor runs the live compatibility check, since CI
  installs no `glab` (CI03-GATES, G5; D-GATE).

## 2. Knowledge references: upstream text read on demand

The first instance is the GitLab CI/CD YAML syntax reference
(<https://docs.gitlab.com/ci/yaml/>, for example `trigger:forward`). The
mechanism is generic: any upstream Markdown page with stable headings, from
any project, under any domain. It follows the Agent Skills layout
(`references/` beside `SKILL.md`, "loaded only when required"), the Claude
guidance (`SKILL.md` under 500 lines and used as navigation, reference files
one level deep) and the Codex guidance (name and description first,
`SKILL.md` on use, files after).

### Block

| Part | Value |
| --- | --- |
| Capability | vendor N upstream pages as verbatim chunks with a derived index; serve one chunk, part or card |
| Owner | `ci-skills/lib/core/reference.py` (chunk, anchor, index, verify, lookup) |
| Entrypoints | installed: `bin/reference.py next\|get\|list\|verify`; checkout: `tools/update_reference.py` |
| Result | `reference_next`, `reference_record`, `reference_index` (committed `index.json`), lock entries |
| Read-back | `verify` recomputes every digest and the index from the committed files; `update` runs it last |

### Upstream, measured 2026-10-06

- Source: `gitlab-org/gitlab`, `doc/ci/yaml/_index.md`, pinned at commit
  `9892f2e6cf006fa1acc3f4d744f707757111db58` (2026-10-02, the latest commit
  touching the page and its schema); the raw-by-ref URL answers 200 with
  `content-length: 254048`. 7,511 lines, 254,048 bytes.
- Headings: keyword headings carry backticks and run H2 to H5: 1 H2
  (`variables`), 40 H3, 111 H4, 15 H5 (`cache:key:files`, `spec:inputs:*`,
  `rules:changes:*`, `rules:exists:*`), 167 in all, no duplicates; 7 prose
  headings (the four section H2s, `Dynamic environments`, Job `variables`,
  Default `variables`). 15 heading-like lines sit inside fenced code and are
  not headings.
- Chunks (own-text rule, below): 167 keyword chunks, 7 section chunks and a
  1,635-byte preamble partition the file exactly. Keyword chunk sizes:
  smallest 93 bytes, median 1,236, p90 2,632, largest 5,000 (`after_script`);
  `trigger:forward` is 84 lines and 2,587 bytes (source lines 7031-7114).
- Sub-structure is usual, not fixed: **Keyword type** in 144 of 167 chunks,
  **Supported values** in 150, **Example** in 151, **Additional details** in
  101, **Related topics** in 55. 96 Hugo shortcodes (`history` 76, `details`
  16, `icon` 4) and `[!note]`, `[!warning]`, `[!flag]` alerts occur; example
  blocks hold literal `---` lines. A chunk keeps all of it byte for byte.
- Companion schema: `app/assets/javascripts/editor/schema/ci.json`, 128,126
  bytes, draft-07, 58 definitions, 120 `markdownDescription` nodes of which
  102 link to the page, 78 distinct anchors. `trigger.forward` is
  `{yaml_variables: boolean, default true; pipeline_variables: boolean,
  default false}` and appears under two `oneOf` branches.
- Anchor rule, verified against the rendered `<h4 id=triggerforward>` and the
  78 schema anchors: lowercase the heading text, drop every character outside
  `[a-z0-9_ -]` (backticks and `:` vanish, `_` stays), replace spaces with
  `-`. 77 of 78 reproduce; one dangles at this commit
  (`globally-defined-image-services-cache-before_script-after_script`) and is
  a recorded fixture, not a failure.
- License: everything under `doc/` is CC BY-SA 4.0; `ci.json` is MIT. The
  repository has no license file (D-LICENSE); each vendored tree carries its
  own notice stating that changes (chunking) were made.

### Layout

The tree mirrors the keyword path, so the filesystem is a browsable index
(`ls` shows the sub-keywords) and the worst case an agent can read is one
5,000-byte chunk. The `vendor` segment matches the documentation contract's
exclusion pattern, so doc gates skip upstream text; the same segment is added
to `.markdownlint-cli2.yaml` `ignores` and to `.gitattributes` as
`-whitespace` (chunks end with `---` and a blank line).

```text
ci-skills/references/vendor/gitlab-ci-yaml/
├── LICENSE.md            CC BY-SA 4.0 notice, attribution, pinned commit; MIT note for ci.json fields
├── index.json            generated by update; kind reference_index; indented, validated by the schemas gate
├── _preamble.md          upstream bytes before the first heading (1,635 B)
├── _sections/            the 7 prose headings as section chunks (job-keywords.md, ...)
├── variables/_index.md   the H2 keyword; `_index.md` mirrors upstream's own naming
├── trigger/_index.md     the keyword's own text (keyword type, values, example, details)
├── trigger/forward.md    one H4 chunk, verbatim (2,587 B)
└── cache/key/files.md    an H5 keyword; the path is the keyword path
```

Chunk bytes: the exact slice from a heading line to the line before the next
heading of any level (the own-text rule); the preamble is what precedes the
first heading. Concatenating `_preamble.md` and every chunk in document order
reproduces the upstream file byte for byte; `verify` checks this offline as a
byte-sum invariant. No local text inside a chunk; metadata lives only in
`index.json`, so a chunk's digest is reproducible from (commit, heading).

### Index record

The "memory" the operator defined: pointers, a machine-readable schema, where
is what, tags. Schema `reference-index` (CI07-SCHEMA), schema version `0.1`
while this phase is in review, `1.0` on merge.

```json
{
  "schema_version": "0.1",
  "kind": "reference_index",
  "reference": "gitlab-ci-yaml",
  "domain": "gitlab",
  "max_chunk_bytes": 65536,
  "upstream": {
    "project": "gitlab-org/gitlab", "path": "doc/ci/yaml/_index.md",
    "sha": "9892f2e6cf006fa1acc3f4d744f707757111db58", "sha256": "...", "bytes": 254048,
    "license": "CC-BY-SA-4.0", "link_base": "https://docs.gitlab.com/ci/yaml/",
    "schema": {"path": "app/assets/javascripts/editor/schema/ci.json", "sha256": "...", "license": "MIT"}
  },
  "entries": [
    {
      "keyword": "trigger:forward", "kind": "keyword", "level": 4, "parent": "trigger", "section": "job",
      "file": "trigger/forward.md", "sha256": "...", "bytes": 2587, "lines": 84,
      "anchor": "triggerforward", "url": "https://docs.gitlab.com/ci/yaml/#triggerforward",
      "description": "Specify what to forward to the downstream pipeline.",
      "schema_paths": ["/definitions/job_template/properties/trigger/oneOf/0/properties/forward",
                       "/definitions/job_template/properties/trigger/oneOf/1/properties/forward"],
      "subkeys": ["pipeline_variables", "yaml_variables"],
      "parts": {"values": [11, 22], "example": [23, 68], "details": [69, 83]},
      "tags": ["trigger", "forward", "job", "downstream-pipelines", "pipeline-variables"],
      "children": []
    }
  ]
}
```

Every field is derived, none is hand-written: `description`, `schema_paths`
(RFC 6901 pointers, every match listed) and `subkeys` from `ci.json`;
`section` from the enclosing H2 with `variables` as its own case; `parts`
from the labelled sub-sections when present; `tags` from the keyword path
tokens, the section and the slugs of the chunk's **Related topics** links;
`children` from the heading tree. Entries are sorted by keyword; the file is
written with sorted keys, two-space indentation and a trailing newline.

### Algorithms

1. **Fetch** (checkout only, network). GET the page and the schema at
   `https://gitlab.com/gitlab-org/gitlab/-/raw/<sha>/<path>` with a timeout
   and a 1 MiB byte bound, through the one bounded HTTP helper the skill
   library owns; `<sha>` is `--sha`, never a branch name. The default run
   prints the plan (URLs, commit, expected bytes) and writes nothing;
   `--confirm` fetches into staging.
2. **Chunk.** Scan lines, treating fenced code as opaque; a heading is
   `^(#{2,6}) (.+)$`; a keyword heading is one whose text is a backticked
   keyword path; anything else is a section heading. A chunk runs from its
   heading to the line before the next heading of any level. Reject a
   duplicate anchor or file name, a chunk over `max_chunk_bytes`, heading
   depth 6, and a byte sum that does not equal the upstream size.
3. **Anchor.** The rule above. Oracle: the 78 schema anchors (77 must
   reproduce, one recorded as dangling) and the rendered `triggerforward` id.
4. **Index.** One record per chunk; join `ci.json` structurally (split on
   `:`, root by section, descend `properties/<segment>` through `oneOf`,
   `anyOf` and `$ref`); derive `parts`, `tags`, `children`; validate against
   `reference-index` before writing.
5. **Lock.** Per-file `sha256` and `origin` (`upstream` for chunks, `local`
   for `LICENSE.md` and `index.json`) plus the subtree digest from
   `core.provenance.tree_digest`, as one `references` entry of the one lock
   CI05-VENDOR defines, at the repository root under `vendor/`.
6. **Transaction.** The journaled replace-tree transaction extracted from the
   installer (`tools/install_ci_skills.py`), not a second one: stage, swap,
   read back, recover; in-tree use keeps no backup and removes its lock so no
   residue enters the digest.
7. **Lookup** (installed, offline). `get <reference> <keyword>` reads
   `index.json`, resolves the keyword exactly (or by `--search` over full
   keyword paths, listing ties as `keyword_ambiguous`), checks the chunk's
   `sha256`, and prints the record with the chunk in `content`, the labelled
   `--part` or the schema-derived `--card`, and `links` resolved to the
   published URLs (`x.md` -> `x/`, `_index.md` -> `/`, anchors kept); bytes
   stay verbatim. Vendored text bypasses output redaction through a declared
   `verbatim` allowlist on the one emitter (`secrets:token` would otherwise be
   rewritten); `filters` and `errors` stay redacted.
8. **Reason tokens**, each defined once with its safe next step:
   `reference_unknown`, `keyword_unknown`, `keyword_ambiguous`,
   `index_unreadable`, `index_invalid`, `upstream_mismatch`,
   `digest_mismatch`, `file_missing`, `unexpected_file`,
   `symlink_unexpected`, `chunk_too_large`, `heading_too_deep`,
   `anchor_duplicate`, `file_duplicate`, `anchor_dangling`,
   `byte_sum_mismatch`, `fetch_failed`, `fetch_too_large`, `sha_required`,
   `confirmation_required`. Exit codes follow the catalog's table (0 and 2)
   until CI02-CLI lands the shared one.

### Loading, measured

| Step | The agent runs | Cost |
| --- | --- | --- |
| L0 | nothing; the skill `description` names the reference and the navigator | about 100 tokens |
| L1 | `bin/reference.py next gitlab <tag>`, then the chosen `next`, two or three times | under 1 KB each |
| L2 | the leaf: `get` (2,587 B for `trigger:forward`), `--part values` or `--card` | one chunk, never the page |

`index.json` (about 65-80 KB) and `tools.json` (26 KB) are inputs of the tool,
never reading material; `SKILL.md` says so in one sentence.

## 3. The navigator: one black box for knowledge and tools

The agent does not read an index and pick a file. It passes a domain it is
already anchored on and zero or more tags; the tool answers with a small set
of tags to choose from and, for each, where it leads next: another `next`,
a `get` of one chunk, or a command to run. The agent runs what it chose;
another agent can pass the tag instead. The same tool serves every declared
reference and every command the catalog declares, so it is also the skill's
programmatic self-description: `next` with no arguments is "this is what I
can do", `next harbor` is "this is what I can do about Harbor".

```text
reference.py next                        -> domains (derived: references' domain, commands' requires)
reference.py next gitlab                 -> areas: ci-yaml (reference), commands
reference.py next gitlab ci-yaml trigger -> children forward, include, inputs, project, strategy; leaf: get
reference.py next gitlab pipeline        -> ranked matches over derived tags: keywords and commands
```

- **Input**: `DOMAIN [TAG ...]`, `--json|--yaml|--human`, `--limit N` (12).
  Offline, no credentials, no target file.
- **Output**, bounded: `kind: reference_next`, `domain`, `path`, `node`
  (`id`, `kind` domain|area|section|keyword|command, `summary`), `choices`
  (one line each: `tag`, `kind`, `summary`, `bytes`, `next` as the exact
  command to run), `leaf` (`get`, `command`, `url`), and `cut` (how many
  choices the limit removed, and which tag narrows them). Never silent.
- **Tree sources, all derived**: reference indexes (`children`, `section`,
  `tags`) and `tools.json` (`routing` phrases, `commands[*].requires` for the
  domain, `use_when`, `subcommands`). A declared reference carries one
  `domain`; nothing else is typed by hand.
- **Matching**: an exact child tag wins; otherwise case-insensitive token
  match over the derived tags under the current path, ranked (exact tag, path
  token, section, related-topic slug, `use_when` word); ties are listed,
  never guessed; `tag_unknown` returns the nearest tags. Deterministic.
- **Measured** on 2026-10-06 with a prototype over the pinned page and the
  committed `tools.json`: `next gitlab ci-yaml trigger` is 904 bytes and
  lists five children with their sizes plus the `get` leaf; `next gitlab
  pipeline` is 622 bytes and lists `needs:pipeline`, `needs:pipeline:job`,
  `gitlab_job.py --describe` and `gitlab_pipeline.py --describe`.

## 4. Lifecycle of a reference

One state machine per declared reference, the same shape as CI05-VENDOR's
vendoring and the installer's plan, confirm and read-back. Checkout stages
may use the network; installed stages never do.

1. **declare**: hand edit of `vendor/vendor.toml` `[references.<name>]`; writes the
   declaration; evidence: the `schemas` gate; failure `declaration_invalid`.
2. **fetch**: `tools/update_reference.py <name> --sha S`, `--confirm` fetches; writes
   staging only; evidence: HTTP status, length, raw sha256; failures `fetch_failed`,
   `fetch_too_large`, `sha_required`.
3. **structure**: the same run chunks, anchors, joins and builds the index; writes
   staging only; evidence: counts, sizes, per-file sha256; failures `anchor_duplicate`,
   `file_duplicate`, `chunk_too_large`, `index_invalid`.
4. **verify**: `bin/reference.py verify <name>`; writes nothing; evidence: PASS or the
   first failing file and rule; failures `digest_mismatch`, `file_missing`,
   `unexpected_file`, `symlink_unexpected`.
5. **publish**: the transaction inside `update --confirm`, one pull request per refresh;
   writes the tree and the lock; evidence: per-keyword `git diff`, the subtree digest in
   receipts; `transaction_recovered` is reported, not an error.
6. **serve**: `bin/reference.py next|get|list`; writes nothing; evidence: the bounded
   record; failures `keyword_unknown`, `keyword_ambiguous`.
7. **check-upstream**: `update --dry-run`; writes nothing; evidence: `upstream_ahead` with
   the commits since the pin, or `current`, and `reference_stale` once `max_age_days` has
   passed; a report, never a failure by itself.
8. **refresh**: stages 2 to 5 at the new commit; the pull request diff shows exactly which
   keywords changed.
9. **retire**: remove the declaration; `update` plans the removals and `--confirm` applies
   them; the closed-world gate fails on any dangling `SKILL.md` or index pointer
   (`dangling_reference`).

Contract first, so a second reference adds no code path: a `ReferenceSource`
contract (one implementation today, `gitlab-raw`: a file at a commit from the
GitLab raw endpoint), a `Chunker` profile (`markdown-headings`) and an
optional `Joiner` (`gitlab-ci-schema`), selected by the declaration, never by
name checks inside the library.

## 5. Gates and tests

- Unit: the anchor oracle (78 anchors, 77 reproduced, one recorded as
  dangling, plus the rendered id); the chunker on a recorded skeleton fixture
  plus the real `trigger` section and a hostile fixture (duplicate heading, a
  heading inside a fence, H6, oversized chunk, shortcodes); the structural
  join for `trigger:forward` (two pointers, two subkeys); every reason token
  produced once; `verify` passes on the committed tree and fails on one
  flipped byte, a deleted notice, a stray file, a symlink; `update`'s default
  run writes nothing; an interrupted transaction recovers.
- Navigator: `next` lists exactly the derived domains; `next gitlab` lists
  `ci-yaml` and `commands`; `next gitlab ci-yaml trigger` lists its five
  children and the leaf; `next gitlab pipeline` returns a ranked, bounded
  menu containing `trigger`, `workflow`, `needs` and `gitlab_pipeline.py`;
  an unknown tag returns `tag_unknown` with nearest tags; every answer is
  under the declared byte bound; a second reference in the fixture appears
  under its domain with no code change; equal inputs give equal bytes.
- Contract: every index entry's file exists and nothing else is in the tree
  (closed world, CI07-SCHEMA); the vendored tree is excluded from the
  executed-code digest (D-DIGEST) and digested on its own in the lock.
- Lint: the `vendor` segment excludes the tree from markdownlint and the
  documentation gates; `.gitattributes` marks it `-whitespace`.
- Which surface runs these is D-GATE (CI03-GATES, G0); as of 2026-10-06 the
  operator chose no gate for now, so they are written and unexecuted.

## 6. Steps

1. Block 0 of CI10-PHASES (the locator and path repairs) lands first; the
   navigator and `get` depend on importable bins and a current `tools.json`.
2. Add `core/reference.py`, `core/navigate.py`, `core/paths.py`, the offline
   command path in `core/cli.py` and the `OFFLINE_COMMANDS` and `REFERENCES`
   tables in `core/catalog.py`; regenerate `tools.json`.
3. Add `tools/skillkit/{transaction,fetch,reference_update}.py` (the
   transaction extracted from the installer) and the thin main
   `tools/update_reference.py`.
4. Declare `gitlab-ci-yaml` in `vendor/vendor.toml`; run `update --sha
   9892f2e6cf006fa1acc3f4d744f707757111db58 --confirm`; commit the tree, the
   lock, `schemas/reference-index.schema.json` and the tests.
5. One `SKILL.md` sentence routes to `next`; the description gains the
   reference and the navigator.
6. Read back: `bin/reference.py get gitlab-ci-yaml trigger:forward --json`
   returns `bytes: 2587` and a `url` ending in `#triggerforward`;
   `bin/reference.py next gitlab pipeline --json` returns the measured menu;
   `verify` reports PASS; `tools/render_manifest.py --check` reports CURRENT.

## 7. Delivery, test and proof

1. *Delivery*: `ci-skills/lib/core/{reference,navigate,paths}.py`,
   `ci-skills/bin/reference.py`, `tools/skillkit/{transaction,fetch,reference_update}.py`,
   `tools/update_reference.py`, `schemas/reference-index.schema.json`,
   `ci-skills/references/vendor/gitlab-ci-yaml/` (176 files), `vendor/vendor.toml`,
   `vendor/vendor.lock.json`, one `SKILL.md` sentence, the regenerated
   `tools.json`.
2. *Tests*: section 5, by file and case.
3. *Smoke*, fixed arguments, on this laptop:
   - `bin/reference.py get gitlab-ci-yaml trigger:forward --json` reads back
     `bytes: 2587`, `sha256` equal to the index entry, `url` ending in
     `#triggerforward`, and `content` whose first line is
     ``#### `trigger:forward` ``;
   - `bin/reference.py next gitlab pipeline --json` reads back the measured
     menu (section 3);
   - `bin/reference.py verify gitlab-ci-yaml --json` reads back `PASS` with
     the file count 176;
   - `tools/update_reference.py gitlab-ci-yaml --sha 9892f2e6cf006fa1acc3f4d744f707757111db58 --dry-run --json`
     reads back, live from gitlab.com, HTTP 200, `content-length: 254048` and
     a raw `sha256` equal to `upstream.sha256` in the index, and writes nothing.
4. *Evidence*: `tests/acceptance/receipts/reference-get-trigger-forward.json`,
   `reference-next-gitlab-pipeline.json`, `reference-verify.json`,
   `reference-update-dry-run.json`; fields: `kind`, `status`, `records`,
   `readback`, `skill.digest`, `execution_host`, `captured_at`.
5. *Verification*: `tools/check_live_acceptance.py` with the four
   `[[smoke_cases]]` declared for this phase reports `PASS`.

## Open decisions for knowledge references

- D-LICENSE: a per-tree notice only (recommended), or a repository license.
- The executor for `update --confirm` (network) and for any live check:
  the operator or a named host (CI03-GATES, G5).
