# CI09-REFERENCE: references, tool operations and upstream knowledge

Status: proposed. Order and dependencies: CI10-PHASES, Phases. Followed by
CI08-ROUTING, whose three hops this phase implements as one tool, and
CI11-TOOLS, whose commands the same tool advertises.

A reference is something an agent consults before acting. This phase owns two
kinds, declared with the same `uses` pointers (CI07-SCHEMA):

- **operations** (section 1): what our code calls, one declared operation per
  tool invocation, checked against the installed binary;
- **knowledge** (section 2): what an agent reads, one upstream page vendored
  as verbatim chunks with a derived index, served on demand.

## 1. Tool operations

### Block

| Part | Value |
| --- | --- |
| Capability | declare every external operation our code runs and check it against the installed binary |
| Owner | `ci-skills/lib/core/tool_operations.py` (the declarations, the `__complete` parser, the checks) |
| Entrypoint | `bin/ci-skills tools [NAME] [--describe ID] [--complete]` (the root maintenance command, CI05) |
| Result | `tool_operations` (`schemas/tool-operations.schema.json`, CI07-SCHEMA) |
| Read-back | `tests/python/test_tool_operations.py`, against fixtures and, on the D-SMOKE executor, the binaries |

The read-back test resolves every declared command and flag against the
recorded fixtures `tests/python/fixtures/complete/<tool>-<version>.txt` and,
on the D-SMOKE executor, against the installed binary; its `mutates`
assertion is the gate that no unbounded `api` or `exec` call is labelled
read-only.

### Context

Our code calls `gh`, `glab`, `kubectl` and `oc`; the toolchain contract from
the shared standards (`bin/agent-tools.sh`) names 61 tools by full path. To
choose a command and its flags an agent today reads `--help` text or upstream
documentation. Two things are missing: a record of which operations our code
relies on, and a check that fails when an upgrade renames or removes one.

Measured on 2026-10-02 against the local binaries:

| Tool | Version | Top-level commands | Example |
| --- | --- | --- | --- |
| `glab` | 1.120.0 | 48 | `glab mr create`: 31 flags |
| `gh` | 2.98.0 | 35 | `gh help reference`: 94,244 bytes |
| `kubectl` | v1.36.4 | 45 | `kubectl get`: 53 flags |

### What the code calls today

Derived on 2026-10-06 from every `run_command`, `kubectl_argv`, `oc_argv` and
`GlabAPIClient` call site under `ci-skills/lib/core/` (file:line beside each
row). A command name alone cannot say whether a call is read-only; the
operation (command, method, endpoint class, fixed arguments) can.

- `gh:auth.status`: `gh auth status --hostname H` (`access.py:168`); read.
- `gh:api.get`: `gh api --hostname H user` and `... repos/R` (`access.py:181,184`);
  read.
- `gh:api.protection`: `gh api ... branches/main/protection` and
  `repos/R --jq .permissions.admin` (`access.py:221,231`, publication only); read.
- `glab:auth.status`: `glab auth status --hostname H` (`access.py:344`); read.
- `glab:api.get`: `glab api user`, `runners/all?per_page=1` (`access.py:356,367`)
  and `glab api E --hostname H --method GET --output json --include`
  (`gitlab_api.py:303-313`); read.
- `glab:api.write`: the same argv with `--method POST`, `PUT` or `DELETE` and a
  0600 body file (`gitlab_api.py:287-294`, used by `gitlab_actions.py`); mutates.
- `kubectl:get`: `get daemonsets -A -o json`, `-n N get pods -o json`
  (`access.py:512,609`); `get R [-A] -o json`, `get events[.events.k8s.io] -A -o json`
  (`collect.py:96,390,407`); `get nodes -o json` (`mtu_consistency.py:165`);
  `-n N get pods -l S --field-selector spec.nodeName=X -o json`, `-n N get pod P`
  (`node_pod.py:195,29`); read.
- `kubectl:raw`: `get --raw=/version` (`access.py:701`);
  `get --raw=/apis/config.openshift.io/v1` (`mtu_consistency.py:202`); read.
- `kubectl:auth`: `auth whoami -o json`, `auth can-i ...` one per declared check,
  `config view --raw -o json` (`access.py:708,764,656`);
  `config view --minify -o jsonpath={..namespace}` (`mtu_consistency.py:226`); read.
- `kubectl:exec.cilium-health`: `-n N exec POD -- cilium-health status -o json`
  (`access.py:620`, `cilium.py:20`, `collect.py:686`); read, fixed argv.
- `kubectl:exec.node-local`: `-n N exec pod/P -c C --` followed by
  `cilium-dbg status --verbose --output json`, `cilium-health status --verbose
  --output json`, `journalctl --directory=D --list-boots --no-pager` or
  `journalctl -k --directory=D --utc --since ...` (`node_pod.py:56`,
  `node_local.py:105-106,177,186`); read, fixed argv.
- `oc:exec.ceph`: `oc -n N exec deploy/OP -- ceph --conf=F` with `-s --format=json`,
  `osd tree --format=json` or `pg dump_stuck inactive --format=json`, and
  `oc -n N get pods -l S -o json` (`ceph_cluster.py:236-242`; `oc_argv`,
  `access.py:75`); read, fixed argv.
- `oc:debug.node`: `oc debug node/X MARKER --quiet --to-namespace=N -- chroot /host
  ip -d -j addr show` (`mtu_consistency.py:373-387`); mutates (creates a debug Pod).

### Names: tool, operation, option

- **Tool:** `name` (the binary); `version`, read from the tool's own version
  command (`glab --version`, `gh --version`, `kubectl version --client -o json`);
  `digest`, the SHA-256 of the resolved executable; `source`, how the record
  was collected.
- **Operation:** one way our code calls a tool: `id` as `<tool>:<operation>`
  (`glab:api.get`, `kubectl:exec.cilium-health`), `argv` (the fixed prefix),
  `method` (API calls), `flags` (the flags we pass), `mutates` (true unless the
  method is a read and the argument list is fixed; an unbounded `api` or `exec`
  call counts as mutating).
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

Commands and references name the operations they use in `uses`; `used_by`,
the closed-world rule and the example are CI07-SCHEMA, "Pointers between
references and tools".

### Options considered

| Option | Rejected because |
| --- | --- |
| A, copy help text into the repository | 94 KB of prose per tool, stale on every upgrade, license duties |
| B, a generated registry, committed | commits what runtime can compute; matches one host only |
| C, runtime discovery through `__complete` | a hidden completion protocol; needs the binary; kept for `--complete` |
| E, an external search index | a service with network and credentials; breaks offline CI |

### Decision

Proposed: **D, declared operations checked against the installed tool, with
C as optional navigation.**

- **Declare.** Every operation in the table above, with its exact `argv`,
  method, flags and `mutates`, in `ci-skills/lib/core/tool_operations.py`;
  the contract test asserts that no unbounded `api` or `exec` operation is
  labelled read-only.
- **Check against the real tool.** Every declared command and flag must
  resolve on the installed binary; the check records the binary's path,
  version and digest. Against recorded fixtures it tests only the parser;
  on the D-SMOKE executor, against the real binaries, it proves
  compatibility.
- **Navigate.** `bin/ci-skills tools [NAME]` lists tools and their declared
  operations; `--complete` walks `__complete` for a wider view: every query
  bounded in time and output, argument values never completed, directive and
  active-help lines dropped, the parser tested against recorded and hostile
  output.
- **Our own tools.** `ci-api` and `ci-binary-build` become Python mains
  (CI10-PHASES, Refactor); `--describe` arrives with the port (CI02-CLI).
- **Contract.** The `tools` verb follows CI02-CLI: bounded JSON, the shared
  exit codes, and `tool_missing` with a safe next step when a binary is
  absent.
- **Revisit** when a tool not built with cobra is added, or when upstream
  publishes a machine-readable reference.

## 2. Knowledge references: upstream text read on demand

The first instance is the GitLab CI/CD YAML syntax reference
(<https://docs.gitlab.com/ci/yaml/>, for example `trigger:forward`). The
mechanism is generic: any upstream Markdown page with stable headings, from
any project, under any domain. It follows the Agent Skills layout:
`references/` beside `SKILL.md`, loaded only when required.

### Block, knowledge kind

| Part | Value |
| --- | --- |
| Capability | vendor N upstream pages as verbatim chunks with a derived index; serve one chunk, part or card |
| Owner | `ci-skills/lib/core/reference.py` (chunk, anchor, index, verify, lookup) |
| Entrypoints | installed: `ci-skills/bin/reference.py next\|get\|list\|verify`; checkout: `tools/update_reference.py` |
| Result | `reference_next`, `reference_record`, `reference_list`, `reference_verify`, `reference_index`, lock entries |
| Read-back | `verify` recomputes every digest and the index from the committed files; `update` runs it last |

### Upstream, measured 2026-10-06

- Source: `gitlab-org/gitlab`, `doc/ci/yaml/_index.md`, pinned at commit
  `9892f2e6cf006fa1acc3f4d744f707757111db58` (2026-10-02, the latest commit
  touching the page and its schema); the raw-by-ref URL answers 200; 7,511
  lines, 254,048 bytes.
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
5,000-byte chunk. The `vendor` segment matches the exclusion pattern of the
documentation contract at the pinned standards revision `56a579c`
(`standards-binding.yaml`), so doc gates skip upstream text; the same segment
is added to `.markdownlint-cli2.yaml`
`ignores` and to `.gitattributes` as `-whitespace` (chunks end with `---` and
a blank line).

```text
ci-skills/references/vendor/gitlab-ci-yaml/
├── LICENSE.md            CC BY-SA 4.0 notice, attribution, pinned commit; MIT note for ci.json fields
├── index.json            generated by update; kind reference_index; indented, validated by the schemas gate
├── _preamble.md          upstream bytes before the first heading (1,635 B)
├── _sections/            the 7 prose headings as section chunks (job-keywords.md, ...)
├── variables/_index.md   the H2 keyword; `_index.md` mirrors upstream's own naming
├── trigger/_index.md     the keyword's own text (keyword type, values, example, details)
├── trigger/forward.md    one H4 chunk, verbatim
└── cache/key/files.md    an H5 keyword; the path is the keyword path
```

Chunk bytes: the exact slice from a heading line to the line before the next
heading of any level (the own-text rule); the preamble is what precedes the
first heading. Concatenating `_preamble.md` and every chunk in document order
reproduces the upstream file byte for byte; `verify` checks this offline as a
byte-sum invariant. No local text inside a chunk; metadata lives only in
`index.json`, so a chunk's digest is reproducible from (commit, heading). The
lock's `file_count` for this tree is 177: 167 keyword chunks, 7 section
chunks, `_preamble.md`, `LICENSE.md` and `index.json`.

### Index record

Schema `reference-index` (CI07-SCHEMA; version per its Versions section:
`0.1` while this phase is in review, `1.0` on merge).

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

### Declaration

One table per reference in `vendor/vendor.toml` (CI05-VENDOR owns the file;
this phase adds the `[references.<name>]` fields with a MINOR bump of the
`vendor-declarations` schema):

```toml
[references.gitlab-ci-yaml]
domain = "gitlab"
source = "gitlab-raw"                                  # a file at a commit from the GitLab raw endpoint
project = "gitlab-org/gitlab"
path = "doc/ci/yaml/_index.md"
sha = "9892f2e6cf006fa1acc3f4d744f707757111db58"
license = "CC-BY-SA-4.0"
link_base = "https://docs.gitlab.com/ci/yaml/"
schema_path = "app/assets/javascripts/editor/schema/ci.json"   # optional joiner input, MIT
chunker = "markdown-headings"                          # levels 2-5, backticked keyword paths
joiner = "gitlab-ci-schema"                            # optional
max_chunk_bytes = 65536
max_age_days = 90
```

`source`, `chunker` and `joiner` each have one implementation today; the
`ReferenceSource`, `Chunker` and `Joiner` seams are introduced with the second
source, not before (CI10-PHASES, Modularity: contract first only where two
implementations exist).

### Algorithms

1. **Fetch** (checkout only, network). GET the page and the schema at
   `https://gitlab.com/gitlab-org/gitlab/-/raw/<sha>/<path>` with a timeout
   and a 1 MiB byte bound through `ci-skills/lib/core/http.py`, the bounded
   standard-library HTTP helper this phase adds (CI11-TOOLS reuses it);
   `<sha>` is `--sha`, never a branch name. Modes of `tools/update_reference.py`,
   defined once: the default run prints the plan (URLs, commit, expected
   bytes) offline and writes nothing; `--dry-run` performs the network reads
   and reports HTTP status, length and raw `sha256`, writing nothing;
   `--apply --confirm-plan DIGEST` (the digest the default run printed;
   CI02-CLI, item 6) fetches into staging and publishes.
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
   CI05-VENDOR defines, `vendor/vendor.lock.json`.
6. **Transaction.** The journaled replace-tree transaction extracted from the
   installer into `tools/skillkit/transaction.py` (CI05-VENDOR), not a second
   one: stage, swap, read back, recover; in-tree use keeps no backup and
   removes its lock so no residue enters the digest.
7. **Lookup** (installed, offline). `get <reference> <keyword>` reads
   `index.json`, resolves the keyword exactly (or by `--search` over full
   keyword paths, listing ties as `keyword_ambiguous`), checks the chunk's
   `sha256`, and prints the record with the chunk in `content`, the labelled
   `--part` or the schema-derived `--card`, and `links` resolved to the
   published URLs (`x.md` to `x/`, `_index.md` to `/`, anchors kept); bytes
   stay verbatim. Vendored text bypasses output redaction through a declared
   `verbatim` allowlist on the one emitter (`secrets:token` would otherwise be
   rewritten); `filters` and `errors` stay redacted. Exit codes follow the
   catalog's table (0 and 2) until CI02-CLI lands the shared one.

Reason tokens are defined once each, in the lifecycle stage that produces
them (section 4); `tests/python/test_reference.py` produces every one.

### Loading, measured

| Step | The agent runs | Cost |
| --- | --- | --- |
| L0 | nothing; the skill `description` names the reference and the navigator | about 100 tokens |
| L1 | `ci-skills/bin/reference.py next gitlab <tag>`, then the chosen `next`, two or three times | under 1 KB each |
| L2 | the leaf: `get` (one chunk), `--part values` or `--card` | one chunk, never the page |

`index.json` and `tools.json` are inputs of the tool, never reading
material; the one `SKILL.md` sentence of step 6 says so, and CI08-ROUTING
owns the rest of the router.

## 3. The navigator: one black box for knowledge and tools

An agent never reads `tools.json` or a whole reference. It walks: it names a domain, gets a short menu, picks one
entry, and repeats until it reaches something to run or something to read. Every menu entry carries one action:

| Action | Meaning | The entry's `next` is |
| --- | --- | --- |
| `next` | a group; walk into it | the next `reference.py next` call |
| `run` | a command the agent can execute | the exact command line |
| `read` | something the agent can read | the exact call that prints it, one chunk or one contract |

### Worked example: gitlab, then pipeline, then watch

Step 1 names the domain. The groups are the commands that require it, grouped by the noun in their name; the
`ci-yaml` reference is walked like a group.

```text
$ reference.py next gitlab
gitlab
  next  access     prove access; read back identity and target    reference.py next gitlab access
  next  issue      create or reuse an exact bug issue             reference.py next gitlab issue
  next  job        one job with its pipeline, runner and log tail reference.py next gitlab job
  next  milestone  create or update a milestone                   reference.py next gitlab milestone
  next  pipeline   one pipeline and its job progress by stage     reference.py next gitlab pipeline
  next  runner     assign or create a runner                      reference.py next gitlab runner
  next  wiki       create or update a wiki page                   reference.py next gitlab wiki
  next  ci-yaml    the CI/CD YAML syntax, one keyword at a time   reference.py next gitlab ci-yaml
```

Step 2 opens one group. Its verbs are things to run; the CI YAML keywords tagged with the same noun are things
to read.

```text
$ reference.py next gitlab pipeline
gitlab > pipeline
  run   get                 one pipeline: stages and job counts           gitlab_pipeline.py get --pipeline-id <id> --json
  run   track               watch one pipeline until it finishes          gitlab_pipeline.py track --pipeline-id <id> --json
  run   logs                the failed jobs' log tails                    gitlab_pipeline.py logs --pipeline-id <id> --json
  run   list                recent pipelines by status, time or job name  gitlab_pipeline.py list --json
  read  needs:pipeline      CI YAML: mirror an upstream pipeline's status reference.py get gitlab-ci-yaml needs:pipeline
  read  needs:pipeline:job  CI YAML: artifacts from another pipeline      reference.py get gitlab-ci-yaml needs:pipeline:job
```

Step 3 names the intent, not the verb. `watch` is no tag; it matches a word in the summary of `track`, so the
answer is that leaf: one command to run and its contract to read.

```text
$ reference.py next gitlab pipeline watch
gitlab > pipeline > track   ("watch" matched the summary of track)
  run   track     watch one pipeline until it finishes             gitlab_pipeline.py track --pipeline-id <id> --json
  read  contract  every option, the result fields, the exit codes  gitlab_pipeline.py track --describe
```

The same answer with `--json`, which an agent parses:

```json
{
  "schema_version": "1.0",
  "kind": "reference_next",
  "path": ["gitlab", "pipeline", "track"],
  "matched": {"input": "watch", "by": "summary"},
  "choices": [
    {"tag": "track", "action": "run", "summary": "watch one pipeline until it finishes",
     "next": "gitlab_pipeline.py track --pipeline-id <id> --json"},
    {"tag": "contract", "action": "read", "summary": "every option, the result fields, the exit codes",
     "next": "gitlab_pipeline.py track --describe"}
  ]
}
```

The four pipeline verbs are the ones CI11-TOOLS adds; until they land, step 2 holds one `run` entry,
`gitlab_pipeline.py --pipeline-id <id> --json`.

### Rules

- **Input**: `[DOMAIN [TAG ...]]`, `--json|--yaml|--human`, `--limit N` (default 12). Offline, no credentials, no
  target file.
- **Output**: `kind: reference_next`, `path`, `matched` when the input was not an exact tag, and `choices`, one line
  each: `tag`, `action` (`next`, `run` or `read`), `summary`, `next`. Every answer stays under 1 KB; when the
  limit drops choices, `cut` says how many and which tag narrows them. Never silent.
- **Tree sources, all derived**: groups from the commands that require the domain (`requires_authorities`),
  grouped by the noun in their name; verbs from each command's `subcommands`; reads from the reference indexes,
  the keywords whose tags include the group's noun. Nothing is typed by hand.
- **What the catalog must provide**: `subcommands` as a map for every command, each verb with a one-line
  `purpose`. Today only `gitlab_access.py check` declares one, and the two Bash entries list their verbs as an
  array; CI02-CLI makes both the rule.
- **Matching**: an exact tag wins; otherwise a case-insensitive token match over the tags, then over the summary
  words, as `watch` shows; ties are listed, never guessed; `tag_unknown` returns the nearest tags. The same input
  always gives the same answer.
- **Measured** on 2026-10-06 with a prototype over the pinned page: `next gitlab ci-yaml trigger` is 904 bytes and
  lists the five children of `trigger` with their sizes plus the `get` leaf.

## 4. Lifecycle of a reference

One state machine per declared reference, the same shape as CI05-VENDOR's
vendoring and the installer's plan, confirm and read-back. Checkout stages
may use the network; installed stages never do. Commands and what each stage
writes are Algorithms 1 and 5 to 7; each stage below carries its evidence and
its reason tokens, defined here once.

1. **declare**: the `[references.<name>]` table above; evidence: the
   `schemas` gate; failure `declaration_invalid`.
2. **fetch**: evidence HTTP status, length, raw `sha256`; failures
   `fetch_failed`, `fetch_too_large`, `sha_required`, `confirmation_required`.
3. **structure**: evidence counts, sizes, per-file `sha256`, the dangling
   anchor fixture; failures `anchor_duplicate`, `file_duplicate`,
   `chunk_too_large`, `heading_too_deep`, `anchor_dangling` (beyond the
   recorded fixture), `byte_sum_mismatch`, `index_invalid`.
4. **verify**: evidence PASS or the first failing file and rule; failures
   `digest_mismatch`, `file_missing`, `unexpected_file`, `symlink_unexpected`,
   `index_unreadable`, `index_invalid`.
5. **publish**: evidence per-keyword `git diff`, the subtree digest in
   receipts; `upstream_mismatch` when the bytes moved between plan and apply;
   `transaction_recovered` is reported, not an error.
6. **serve**: evidence the bounded record; failures `reference_unknown`,
   `keyword_unknown`, `keyword_ambiguous`, `tag_unknown`.
7. **check-upstream** (`update --dry-run`): evidence `upstream_ahead` with the
   commits since the pin, or `current`, and `reference_stale` once
   `max_age_days` has passed; a report, never a failure by itself.
8. **refresh**: stages 2 to 5 at the new commit; the pull request diff shows
   exactly which keywords changed.
9. **retire**: remove the declaration; `update` plans the removals and
   `--apply --confirm-plan DIGEST` applies them; the closed-world gate fails
   on any dangling
   `SKILL.md` or index pointer (`dangling_reference`).

## 5. Gates and tests

- `tests/python/test_reference.py`: the anchor oracle (Upstream, measured);
  the chunker on a recorded skeleton fixture plus the real `trigger` section
  and a hostile fixture (duplicate heading, a heading inside a fence, H6,
  oversized chunk, shortcodes); the structural join for `trigger:forward`
  (two pointers, two subkeys); every reason token of section 4 produced
  once; `verify` passes on the committed tree and fails on one flipped byte,
  a deleted notice, a stray file, a symlink; `update`'s default run writes
  nothing; an interrupted transaction recovers. Fixtures under
  `tests/python/fixtures/reference/`.
- `tests/python/test_navigate.py`: `next` lists exactly the derived domains;
  `next gitlab` lists `ci-yaml` and `commands`; `next gitlab ci-yaml trigger`
  lists its five children and the leaf; `next gitlab pipeline` returns the
  worked example's menu (section 3), byte for byte; an unknown tag returns
  `tag_unknown` with nearest tags; every answer is under the declared byte
  bound; a second reference in the fixture appears under its domain with no
  code change; equal inputs give equal bytes.
- `tests/python/test_reference_contract.py`: every index entry's file exists
  and nothing else is in the tree (closed world, CI07-SCHEMA); the vendored
  tree is excluded from the executed-code digest (D-DIGEST) and digested on
  its own in the lock.
- `tests/python/test_tool_operations.py`: section 1's read-back.
- Gate `reference`, a profile of `scripts/check.sh` (CI03-GATES, G1): it
  runs `ci-skills/bin/reference.py verify --json` over every vendored tree
  and fails on anything but `PASS`.
- Run on the gate route (D-GATE; CI03-GATES, G0).

## 6. Steps

1. Block 0 of CI10-PHASES (the locator and path repairs) lands first; the
   navigator and `get` depend on importable bins and a current `tools.json`.
2. Add `ci-skills/lib/core/{reference,navigate,http,tool_operations}.py`, the
   offline command path in `core/cli.py`, and the `OFFLINE_COMMANDS` and
   `REFERENCES` tables in `core/catalog.py` (this phase owns `REFERENCES`;
   CI08-ROUTING adds the tags); regenerate `tools.json`.
3. Add `tools/skillkit/reference_update.py` over `tools/skillkit/transaction.py`
   (CI05-VENDOR) and the thin main `tools/update_reference.py`.
4. Declare `gitlab-ci-yaml` in `vendor/vendor.toml`; run `update --sha
   9892f2e6cf006fa1acc3f4d744f707757111db58`, then the same command with
   `--apply --confirm-plan DIGEST`; commit the tree, the
   lock, `schemas/reference-index.schema.json`, `schemas/tool-operations.schema.json`
   and the tests of section 5.
5. Add the `tools` verb to `bin/ci-skills` with the recorded fixtures for
   `glab` 1.120.0, `gh` 2.98.0 and `kubectl` v1.36.4; the `uses` pointers
   (CI07-SCHEMA) name the operations of section 1.
6. One `SKILL.md` sentence routes to `next`; the description gains the
   reference and the navigator.
7. Read back: the smoke in section 7.

## 7. Delivery, test and proof

1. *Delivery*: the files of steps 2 to 6, plus `ci-skills/references/vendor/gitlab-ci-yaml/`
   (177 files), `vendor/vendor.toml`, `vendor/vendor.lock.json`, the
   regenerated `tools.json`, the `ignores` entry in `.markdownlint-cli2.yaml`
   and the `-whitespace` line in `.gitattributes` (Layout), and the five
   `[[smoke_cases]]` entries in `tests/acceptance/expected.toml` (part 3).
2. *Tests*: section 5, by file; written with the block, run status
   UNVERIFIED (CI03-GATES, G0).
3. *Smoke*, fixed arguments, on the D-SMOKE executor, each run with
   `--receipt-out` and its receipt path of part 4 (CI02-CLI, item 7):
   - `ci-skills/bin/reference.py get gitlab-ci-yaml trigger:forward --json`
     reads back `bytes: 2587`, `sha256` equal to the index entry, `url`
     ending in `#triggerforward`, and `content` whose first line is
     ``#### `trigger:forward` ``;
   - `ci-skills/bin/reference.py next gitlab pipeline --json` reads back the
     worked example's menu (section 3);
   - `ci-skills/bin/reference.py verify gitlab-ci-yaml --json` reads back
     `PASS` with `file_count: 177`;
   - `tools/update_reference.py gitlab-ci-yaml --sha 9892f2e6cf006fa1acc3f4d744f707757111db58 --dry-run --json`
     reads back, live from gitlab.com, HTTP 200, length 254048 and a raw
     `sha256` equal to `upstream.sha256` in the index, and writes nothing;
   - `bin/ci-skills tools glab --json` reads back the installed binary's
     version and digest and every declared operation resolved.
4. *Evidence*: `tests/acceptance/receipts/reference-get-trigger-forward.json`,
   `reference-next-gitlab-pipeline.json`, `reference-verify.json`,
   `reference-update-dry-run.json`, `tool-operations-glab.json`; fields:
   `kind`, `status`, `records`, `readback`, `skill.digest`, `execution_host`,
   `captured_at`.
5. *Verification*: `tools/check_live_acceptance.py --root . --expected
   tests/acceptance/expected.toml --receipts tests/acceptance/receipts
   --skill ci-skills --json`, with the five `[[smoke_cases]]` of this phase
   declared, prints `"status": "PASS"` and exits 0.

## Open decisions

- D-LICENSE: a per-tree notice only (recommended), or a repository license.
- The one home of the hash walk that `bin/ci-skills verify` (CI05-VENDOR)
  and `ci-skills/bin/reference.py verify` share, and whether
  `bin/ci-skills verify` also walks the lock's `references` entries
  (CI05-VENDOR, Interface).
