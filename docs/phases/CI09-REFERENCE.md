# CI09-REFERENCE: references, tool operations and upstream knowledge

Status: proposed. Order and dependencies: CI10-PHASES, Phases. Followed by
CI08-ROUTING, whose routing points at this phase's navigator (section 3), and
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
| Owner | `ci-skills/lib/python/core/tool_operations.py` (the declarations, the `__complete` parser, the checks) |
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
`GlabAPIClient` call site under `ci-skills/lib/python/core/` (file:line beside each
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
  method, flags and `mutates`, in `ci-skills/lib/python/core/tool_operations.py`;
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
| Owner | `ci-skills/lib/python/core/reference.py` (chunk, anchor, index, verify, lookup) |
| Entrypoints | installed: `ci-skills/bin/reference.py next\|get\|verify`; checkout: `tools/update_reference.py` |
| Result | `reference_next` and `reference_section` (section 3), `reference_verify`, `reference_index`, lock entries |
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

Where a reference's sections appear in the graph is declared in `core/catalog.py` (section 3), not here.
`pin = "latest"` in place of `sha` makes `update` resolve the newest upstream version, record it as the pin
and serve only that copy; a source that is not a file at a commit, such as a specification site, needs the
second `ReferenceSource` implementation first.
`source`, `chunker` and `joiner` each have one implementation today; the
`ReferenceSource`, `Chunker` and `Joiner` seams are introduced with the second
source, not before (CI10-PHASES, Modularity: contract first only where two
implementations exist).

### Algorithms

1. **Fetch** (checkout only, network). GET the page and the schema at
   `https://gitlab.com/gitlab-org/gitlab/-/raw/<sha>/<path>` with a timeout
   and a 1 MiB byte bound through `ci-skills/lib/python/core/http.py`, the bounded
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
   heading to the line before the next heading of any level. A repeated
   anchor takes `-1`, `-2` in document order (section 3, Anchors). Reject an
   anchor collision left after that, a duplicate file name, a chunk over `max_chunk_bytes`, heading
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
7. **Lookup** (installed, offline). `get REF ANCHOR`, its `--card` and `--part`
   tiers and `--search` are specified in section 3. This step checks the
   chunk's `sha256` against `index.json` before serving it; the card and part
   texts come from the entry's schema join and labelled sub-sections. Vendored
   text bypasses output redaction through a declared `verbatim` allowlist on
   the one emitter (`secrets:token` would otherwise be rewritten); `filters`
   and `errors` stay redacted.

Reason tokens are defined once each, in the lifecycle stage that produces
them (section 4); `tests/python/test_reference.py` produces every one.

### Loading, measured

| Step | The agent runs | Cost |
| --- | --- | --- |
| L0 | nothing; the skill `description` names the reference and the navigator | about 100 tokens |
| L1 | `ci-skills/bin/reference.py next <path>` at any level, then one pointer (section 3) | under 4 KB each |
| L2 | `get REF ANCHOR`, or its `--card` or `--part NAME` tier (section 3) | one section, never the page |

`index.json` and `tools.json` are inputs of the tool, never reading
material; the `SKILL.md` sentence of section 3, Steps, says so, and CI08-ROUTING
owns the rest of the router.

## 3. The navigator: one entrypoint for knowledge and tools

An agent discovers what the skill can do through one entrypoint, `ci-skills/bin/reference.py`. Each call returns
one level of the resource graph: a short summary and typed pointers to the next level. The agent follows a pointer
only when it needs what is behind it. It never reads `tools.json`, a reference `index.json`, the documentation tree
or a command's full `--help`; those files are the navigator's inputs.

### Authority

This section is the only specification of the navigator, of `get` and `--search`, of the pointer every response
carries, and of how both render. Sections 2 and 4, CI08-ROUTING, CI11-TOOLS and `docs/README.md` point here and
restate none of it. Each combo's operations and completion evidence are specified once in CI11-TOOLS, Combos.
Versions, and how to add what we missed, follow CI07-SCHEMA, Versions. Acquiring and chunking a reference (source,
pin, license, chunker, tiers) follows section 2; where its sections appear in the graph is declared here. The
navigator needs CI10 block 0 (importable `bin/`, a working `tools/render_manifest.py`) and, for Renders 4 to 6, the
pipeline watch pull request (CI11-TOOLS, Pipeline watch, exact); the rest of this phase does not block it.

### Grammar

Every call has the same shape, so an agent repeats it one segment deeper:

```text
reference.py next                              the root
reference.py next PATH                         one level: subresources, plus one run pointer and one read pointer
reference.py next PATH run                     what you can run here
reference.py next PATH run ID                  how to run it: command, required inputs, an options pointer
reference.py next PATH run ID options          every optional input, only if needed
reference.py next PATH read                    the references with sections here
reference.py next PATH read REF                that reference's sections here, and how they relate
reference.py next PATH read REF --search TEXT  the sections of the whole pinned reference that contain TEXT
reference.py get REF ANCHOR                    one section's own text, plus pointers to its subsections
reference.py get REF ANCHOR --card             one tier of it, where section 2 declares tiers (also --part NAME)
                                               every call also takes --page N, and --json, --yaml or --human
```

- `run`, `read` and `options` are reserved segments; no node takes those names.
- A path whose first segment is not a root group is looked up by name anywhere in the graph: one match answers that
  level, several answer "which one?" with one `expand` pointer each (Render 13).
- A pointer is listed only when its target is non-empty, so every pointer resolves.
- A verb a plan names but the installed skill cannot run is listed with `available: false` and no pointer (Renders 4
  and 7).
- `--json` prints one line, and the limits below apply to that line. The renders show it wrapped for reading.

### The renders, in the order an agent meets them

Each render shows the call, what a person sees (`--human`, the default on a terminal) and what an agent parses
(`--json`, the default on a pipe). The human view is the JSON drawn by the rules that follow the renders; nothing
appears in one that is not in the other. Renders 1 to 7 and 14 come from the catalog once the pipeline watch pull
request and this task land. Renders 8 to 13 appear when section 2 vendors the references they read; their text is
as pasted on 2026-10-07, and their exact bytes come from the fetched copy. A combo result draws as CI11-TOOLS,
Combos shows for the pipeline watch.

#### Render 1. The root

Produced by: this task. One-line JSON: 851 bytes.

```text
$ reference.py next
Discover what ci-skills can inspect, run or read.

Available groups
  gitlab   GitLab pipelines, jobs, runners, issues, milestones and wikis.
           Expand: reference.py next gitlab

  k8s      Cluster, workload, storage and network operations.
           Expand: reference.py next k8s

Commands / combos
  run      1 capability to run.
           Expand: reference.py next run

References
  read     1 reference with sections here.
           Expand: reference.py next read
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": [],
  "summary": "Discover what ci-skills can inspect, run or read.",
  "choices": [
    {"id": "gitlab", "kind": "expand", "summary": "GitLab pipelines, jobs, runners, issues, milestones and wikis.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "--json"]}},
    {"id": "k8s", "kind": "expand", "summary": "Cluster, workload, storage and network operations.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "k8s", "--json"]}},
    {"id": "run", "kind": "expand", "summary": "1 capability to run.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "run", "--json"]}},
    {"id": "read", "kind": "expand", "summary": "1 reference with sections here.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "read", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 2. Expand a group

Produced by: this task. One-line JSON: 1650 bytes.

```text
$ reference.py next gitlab
GitLab pipelines, jobs, runners, issues, milestones and wikis.

Subresources
  access     Prove GitLab access and read back the selected target.
             Expand: reference.py next gitlab access

  api        Read one caller-selected GitLab or GitHub API resource.
             Expand: reference.py next gitlab api

  issue      Create or reuse exact bug issues.
             Expand: reference.py next gitlab issue

  job        Inspect job state, output and failure evidence.
             Expand: reference.py next gitlab job

  milestone  Create or update milestones.
             Expand: reference.py next gitlab milestone

  pipeline   Inspect, watch and diagnose pipelines.
             Expand: reference.py next gitlab pipeline

  runner     Inspect or manage runners.
             Expand: reference.py next gitlab runner

  wiki       Create or update wiki pages.
             Expand: reference.py next gitlab wiki
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab"],
  "summary": "GitLab pipelines, jobs, runners, issues, milestones and wikis.",
  "choices": [
    {"id": "access", "kind": "expand", "summary": "Prove GitLab access and read back the selected target.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "access", "--json"]}},
    {"id": "api", "kind": "expand", "summary": "Read one caller-selected GitLab or GitHub API resource.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "api", "--json"]}},
    {"id": "issue", "kind": "expand", "summary": "Create or reuse exact bug issues.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "issue", "--json"]}},
    {"id": "job", "kind": "expand", "summary": "Inspect job state, output and failure evidence.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "job", "--json"]}},
    {"id": "milestone", "kind": "expand", "summary": "Create or update milestones.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "milestone", "--json"]}},
    {"id": "pipeline", "kind": "expand", "summary": "Inspect, watch and diagnose pipelines.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "--json"]}},
    {"id": "runner", "kind": "expand", "summary": "Inspect or manage runners.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "runner", "--json"]}},
    {"id": "wiki", "kind": "expand", "summary": "Create or update wiki pages.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "wiki", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 3. Expand a subresource

Produced by: this task, after the pipeline watch pull request. One-line JSON: 345 bytes.

```text
$ reference.py next gitlab pipeline
Inspect, watch and diagnose pipelines.

Commands / combos
  run      2 capabilities to run; 6 planned.
           Expand: reference.py next gitlab pipeline run
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "pipeline"],
  "summary": "Inspect, watch and diagnose pipelines.",
  "choices": [
    {"id": "run", "kind": "expand", "summary": "2 capabilities to run; 6 planned.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "run", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 4. What you can run

Produced by: this task, after the pipeline watch pull request. One-line JSON: 1246 bytes.

```text
$ reference.py next gitlab pipeline run
2 capabilities to run; 6 planned.

Commands / combos
  get       Read one pipeline: status, stages and job counts.
            Describe: reference.py next gitlab pipeline run get

  watch     Watch a pipeline and its linked downstream runs.
            Describe: reference.py next gitlab pipeline run watch

  list      Unavailable: planned in CI11-TOOLS.
            Unavailable

  logs      Unavailable: planned in CI11-TOOLS.
            Unavailable

  children  Unavailable: planned in CI11-TOOLS.
            Unavailable

  start     Unavailable: planned in CI11-TOOLS.
            Unavailable

  retry     Unavailable: planned in CI11-TOOLS.
            Unavailable

  cancel    Unavailable: planned in CI11-TOOLS.
            Unavailable
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "pipeline", "run"],
  "summary": "2 capabilities to run; 6 planned.",
  "choices": [
    {"id": "get", "kind": "execute", "summary": "Read one pipeline: status, stages and job counts.",
     "next": {"action": "describe", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "run", "get", "--json"]}},
    {"id": "watch", "kind": "execute", "summary": "Watch a pipeline and its linked downstream runs.",
     "next": {"action": "describe", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "run", "watch", "--json"]}},
    {"id": "list", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null},
    {"id": "logs", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null},
    {"id": "children", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null},
    {"id": "start", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null},
    {"id": "retry", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null},
    {"id": "cancel", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null}
  ],
  "continuation": null
}
```

#### Render 5. Describe one capability

Produced by: this task, after the pipeline watch pull request. One-line JSON: 1089 bytes.

```text
$ reference.py next gitlab pipeline run watch
Watch a pipeline and its linked downstream runs.

  command       bin/gitlab_pipeline.py watch
  kind          gitlab_pipeline_watch
  requires      gitlab
  tools         glab
  mutates       no
  side_effects  none
  returns       One record per pipeline in scope; complete and success reported separately; retrieve pointers to failures.
  read_back     Read-only; every pipeline in scope is re-read until it settles.

Commands / combos
  watch    Watch a pipeline and its linked downstream runs.
           Run: gitlab_pipeline.py watch --pipeline-id <pipeline-id>
           Needs: --pipeline-id  numeric ID of the pipeline to read

  options  4 optional inputs; append each as --name value.
           Expand: reference.py next gitlab pipeline run watch options
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "pipeline", "run", "watch"],
  "summary": "Watch a pipeline and its linked downstream runs.",
  "capability": {"command": "bin/gitlab_pipeline.py", "verb": "watch", "kind": "gitlab_pipeline_watch", "requires": ["gitlab"], "tools": ["glab"], "mutates": false, "side_effects": "none", "returns": "One record per pipeline in scope; complete and success reported separately; retrieve pointers to failures.", "read_back": "Read-only; every pipeline in scope is re-read until it settles."},
  "choices": [
    {"id": "watch", "kind": "execute", "summary": "Watch a pipeline and its linked downstream runs.",
     "next": {"action": "execute", "entrypoint": "bin/gitlab_pipeline.py", "args": ["watch", "--pipeline-id", "<pipeline-id>", "--json"], "inputs": [
       {"name": "pipeline-id", "summary": "numeric ID of the pipeline to read", "required": true}
     ]}},
    {"id": "options", "kind": "expand", "summary": "4 optional inputs; append each as --name value.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "run", "watch", "options", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 6. Its optional inputs, only if needed

Produced by: this task, after the pipeline watch pull request. One-line JSON: 933 bytes.

```text
$ reference.py next gitlab pipeline run watch options
4 optional inputs; append each as --name value.

Commands / combos
  watch    Watch a pipeline and its linked downstream runs.
           Run: gitlab_pipeline.py watch --pipeline-id <pipeline-id>
           Needs: --pipeline-id  numeric ID of the pipeline to read
           Optional: --project  exact project path or numeric ID; otherwise use gitlab.project
           Optional: --related-name  also watch newer pipelines on the root's project and ref whose name matches this regex
           Optional: --interval  seconds between polls (default 10)
           Optional: --timeout  seconds for the whole watch (default 3600)
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "pipeline", "run", "watch", "options"],
  "summary": "4 optional inputs; append each as --name value.",
  "choices": [
    {"id": "watch", "kind": "execute", "summary": "Watch a pipeline and its linked downstream runs.",
     "next": {"action": "execute", "entrypoint": "bin/gitlab_pipeline.py", "args": ["watch", "--pipeline-id", "<pipeline-id>", "--json"], "inputs": [
       {"name": "pipeline-id", "summary": "numeric ID of the pipeline to read", "required": true},
       {"name": "project", "summary": "exact project path or numeric ID; otherwise use gitlab.project", "required": false},
       {"name": "related-name", "summary": "also watch newer pipelines on the root's project and ref whose name matches this regex", "required": false},
       {"name": "interval", "summary": "seconds between polls (default 10)", "required": false},
       {"name": "timeout", "summary": "seconds for the whole watch (default 3600)", "required": false}
     ]}}
  ],
  "continuation": null
}
```

#### Render 7. A planned verb is shown, never callable

Produced by: this task. One-line JSON: 996 bytes.

```text
$ reference.py next gitlab milestone run
3 capabilities to run; 1 planned.

Commands / combos
  create       Plan, then create, an exact milestone with independent read-back.
               Describe: reference.py next gitlab milestone run create

  update       Change an exact milestone's title, description, state or dates, with read-back.
               Describe: reference.py next gitlab milestone run update

  adjust-time  Change only an exact milestone's start or due date, with read-back.
               Describe: reference.py next gitlab milestone run adjust-time

  list         Unavailable: planned in CI11-TOOLS.
               Unavailable
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "milestone", "run"],
  "summary": "3 capabilities to run; 1 planned.",
  "choices": [
    {"id": "create", "kind": "execute", "summary": "Plan, then create, an exact milestone with independent read-back.",
     "next": {"action": "describe", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "milestone", "run", "create", "--json"]}},
    {"id": "update", "kind": "execute", "summary": "Change an exact milestone's title, description, state or dates, with read-back.",
     "next": {"action": "describe", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "milestone", "run", "update", "--json"]}},
    {"id": "adjust-time", "kind": "execute", "summary": "Change only an exact milestone's start or due date, with read-back.",
     "next": {"action": "describe", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "milestone", "run", "adjust-time", "--json"]}},
    {"id": "list", "kind": "execute", "summary": "Unavailable: planned in CI11-TOOLS.", "available": false, "next": null}
  ],
  "continuation": null
}
```

#### Render 8. What you can read

Produced by: once section 2 vendors `gitlab-labels`. One-line JSON: 375 bytes.

```text
$ reference.py next gitlab issue read
1 reference with sections here.

Subresources
  gitlab-labels  GitLab label families and how they relate.
                 Expand: reference.py next gitlab issue read gitlab-labels
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "issue", "read"],
  "summary": "1 reference with sections here.",
  "choices": [
    {"id": "gitlab-labels", "kind": "expand", "summary": "GitLab label families and how they relate.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "issue", "read", "gitlab-labels", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 9. One reference as a cluster

Produced by: once section 2 vendors `gitlab-labels`. One-line JSON: 2920 bytes.

```text
$ reference.py next gitlab issue read gitlab-labels
GitLab label families and how they relate.

References
  type-labels      Exactly one per issue; always lowercase; any color but blue.
                   Read: reference.py get gitlab-labels type-labels
                   Read when labeling any issue.

  priority-labels  priority::1 to priority::4; their meaning lives in the handbook triage page.
                   Read: reference.py get gitlab-labels priority-labels
                   Read when setting priority; choosing a level needs the handbook page.

  severity-labels  severity::1 to severity::4; their meaning lives in the handbook triage page.
                   Read: reference.py get gitlab-labels severity-labels
                   Read when setting severity; choosing a level needs the handbook page.

  workflow-labels  18 workflow:: values for the issue's current status.
                   Read: reference.py get gitlab-labels workflow-labels
                   Read when moving an issue between states.

  stage-labels     devops::<stage_key>; scoped, at most one per issue.
                   Read: reference.py get gitlab-labels stage-labels
                   Read when choosing the product stage.

  group-labels     group::<group_key>; scoped; automation infers the stage from it.
                   Read: reference.py get gitlab-labels group-labels
                   Read when choosing the owning group.

  category-labels  Category:<Category Name>; automation infers group and stage from it.
                   Read: reference.py get gitlab-labels category-labels
                   Read when the issue fits a product category.

  feature-labels   Lowercase feature labels when no category applies; they infer group and stage.
                   Read: reference.py get gitlab-labels feature-labels
                   Read when no category label fits.

Relations
  group-labels infers stage-labels  (group-labels)
  category-labels infers group-labels  (category-labels)
  category-labels infers stage-labels  (category-labels)
  feature-labels infers group-labels  (feature-labels)
  feature-labels infers stage-labels  (feature-labels)
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "issue", "read", "gitlab-labels"],
  "summary": "GitLab label families and how they relate.",
  "choices": [
    {"id": "type-labels", "kind": "read", "summary": "Exactly one per issue; always lowercase; any color but blue.", "when": "Read when labeling any issue.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "type-labels", "--json"]}},
    {"id": "priority-labels", "kind": "read", "summary": "priority::1 to priority::4; their meaning lives in the handbook triage page.", "when": "Read when setting priority; choosing a level needs the handbook page.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "priority-labels", "--json"]}},
    {"id": "severity-labels", "kind": "read", "summary": "severity::1 to severity::4; their meaning lives in the handbook triage page.", "when": "Read when setting severity; choosing a level needs the handbook page.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "severity-labels", "--json"]}},
    {"id": "workflow-labels", "kind": "read", "summary": "18 workflow:: values for the issue's current status.", "when": "Read when moving an issue between states.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "workflow-labels", "--json"]}},
    {"id": "stage-labels", "kind": "read", "summary": "devops::<stage_key>; scoped, at most one per issue.", "when": "Read when choosing the product stage.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "stage-labels", "--json"]}},
    {"id": "group-labels", "kind": "read", "summary": "group::<group_key>; scoped; automation infers the stage from it.", "when": "Read when choosing the owning group.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "group-labels", "--json"]}},
    {"id": "category-labels", "kind": "read", "summary": "Category:<Category Name>; automation infers group and stage from it.", "when": "Read when the issue fits a product category.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "category-labels", "--json"]}},
    {"id": "feature-labels", "kind": "read", "summary": "Lowercase feature labels when no category applies; they infer group and stage.", "when": "Read when no category label fits.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "feature-labels", "--json"]}}
  ],
  "relations": [
    {"from": "group-labels", "rel": "infers", "to": "stage-labels", "evidence": "group-labels"},
    {"from": "category-labels", "rel": "infers", "to": "group-labels", "evidence": "category-labels"},
    {"from": "category-labels", "rel": "infers", "to": "stage-labels", "evidence": "category-labels"},
    {"from": "feature-labels", "rel": "infers", "to": "group-labels", "evidence": "feature-labels"},
    {"from": "feature-labels", "rel": "infers", "to": "stage-labels", "evidence": "feature-labels"}
  ],
  "continuation": null
}
```

#### Render 10. Read one section

Produced by: once section 2 vendors `gitlab-labels`. One-line JSON: 537 bytes.

```text
$ reference.py get gitlab-labels priority-labels
gitlab-labels#priority-labels  pinned <commit recorded by the last refresh>

## Priority labels

We have the following priority labels:

* `~"priority::1"`
* `~"priority::2"`
* `~"priority::3"`
* `~"priority::4"`

Refer to the issue triage [priority label](https://handbook.gitlab.com/handbook/product-development/how-we-work/issue-triage/#priority) section in our handbook to see how it’s used.
```

```json
{
  "kind": "reference_section",
  "schema_version": "1.0",
  "reference": "gitlab-labels",
  "anchor": "priority-labels",
  "pinned": "<commit recorded by the last refresh>",
  "text": "## Priority labels\n\nWe have the following priority labels:\n\n* `~\"priority::1\"`\n* `~\"priority::2\"`\n* `~\"priority::3\"`\n* `~\"priority::4\"`\n\nRefer to the issue triage [priority label](https://handbook.gitlab.com/handbook/product-development/how-we-work/issue-triage/#priority) section in our handbook to see how it’s used.\n",
  "choices": [],
  "continuation": null
}
```

#### Render 11. Read deeper only through a pointer

Produced by: once section 2 vendors `gitlab-labels`. One-line JSON: 629 bytes.

```text
$ reference.py get gitlab-labels stage-labels
gitlab-labels#stage-labels  pinned <commit recorded by the last refresh>

## Stage labels

Stage labels specify which [stage](https://handbook.gitlab.com/handbook/product/categories/#hierarchy) the issue belongs to.

Subsections
  naming-and-color-convention  Stage labels: Naming and color convention.
                               Read: reference.py get gitlab-labels naming-and-color-convention
                               Read when you need Naming and color convention.
```

```json
{
  "kind": "reference_section",
  "schema_version": "1.0",
  "reference": "gitlab-labels",
  "anchor": "stage-labels",
  "pinned": "<commit recorded by the last refresh>",
  "text": "## Stage labels\n\nStage labels specify which [stage](https://handbook.gitlab.com/handbook/product/categories/#hierarchy) the issue belongs to.\n",
  "choices": [
    {"id": "naming-and-color-convention", "kind": "read", "summary": "Stage labels: Naming and color convention.", "when": "Read when you need Naming and color convention.",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "naming-and-color-convention", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 12. Search one pinned reference

Produced by: once section 2 vendors `gitlab-labels`. One-line JSON: 771 bytes.

```text
$ reference.py next gitlab issue read gitlab-labels --search scoped
2 sections contain "scoped".

References
  naming-and-color-convention    Stage labels: Naming and color convention.
                                 Read: reference.py get gitlab-labels naming-and-color-convention
                                 Read for the passage that contains "scoped".

  naming-and-color-convention-1  Group labels: Naming and color convention.
                                 Read: reference.py get gitlab-labels naming-and-color-convention-1
                                 Read for the passage that contains "scoped".
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab", "issue", "read", "gitlab-labels"],
  "summary": "2 sections contain \"scoped\".",
  "query": "scoped",
  "choices": [
    {"id": "naming-and-color-convention", "kind": "read", "summary": "Stage labels: Naming and color convention.", "when": "Read for the passage that contains \"scoped\".",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "naming-and-color-convention", "--json"]}},
    {"id": "naming-and-color-convention-1", "kind": "read", "summary": "Group labels: Naming and color convention.", "when": "Read for the passage that contains \"scoped\".",
     "next": {"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-labels", "naming-and-color-convention-1", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 13. Enter by name

Produced by: once the MCP references are vendored. One-line JSON: 501 bytes.

```text
$ reference.py next mcp
mcp: which one?

Subresources
  gitlab.mcp  Connect an MCP client to GitLab's MCP server.
              Expand: reference.py next gitlab mcp

  claude.mcp  Configure MCP servers in Claude Code.
              Expand: reference.py next claude mcp
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["mcp"],
  "summary": "mcp: which one?",
  "choices": [
    {"id": "gitlab.mcp", "kind": "expand", "summary": "Connect an MCP client to GitLab's MCP server.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "mcp", "--json"]}},
    {"id": "claude.mcp", "kind": "expand", "summary": "Configure MCP servers in Claude Code.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "claude", "mcp", "--json"]}}
  ],
  "continuation": null
}
```

#### Render 14. A wrong segment

Produced by: this task. One-line JSON: 1702 bytes.

```text
$ reference.py next gitlab pipelin
path_unknown: pipelin
GitLab pipelines, jobs, runners, issues, milestones and wikis.

Subresources
  access     Prove GitLab access and read back the selected target.
             Expand: reference.py next gitlab access

  api        Read one caller-selected GitLab or GitHub API resource.
             Expand: reference.py next gitlab api

  issue      Create or reuse exact bug issues.
             Expand: reference.py next gitlab issue

  job        Inspect job state, output and failure evidence.
             Expand: reference.py next gitlab job

  milestone  Create or update milestones.
             Expand: reference.py next gitlab milestone

  pipeline   Inspect, watch and diagnose pipelines.
             Expand: reference.py next gitlab pipeline

  runner     Inspect or manage runners.
             Expand: reference.py next gitlab runner

  wiki       Create or update wiki pages.
             Expand: reference.py next gitlab wiki
```

```json
{
  "kind": "reference_next",
  "schema_version": "1.0",
  "path": ["gitlab"],
  "summary": "GitLab pipelines, jobs, runners, issues, milestones and wikis.",
  "choices": [
    {"id": "access", "kind": "expand", "summary": "Prove GitLab access and read back the selected target.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "access", "--json"]}},
    {"id": "api", "kind": "expand", "summary": "Read one caller-selected GitLab or GitHub API resource.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "api", "--json"]}},
    {"id": "issue", "kind": "expand", "summary": "Create or reuse exact bug issues.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "issue", "--json"]}},
    {"id": "job", "kind": "expand", "summary": "Inspect job state, output and failure evidence.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "job", "--json"]}},
    {"id": "milestone", "kind": "expand", "summary": "Create or update milestones.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "milestone", "--json"]}},
    {"id": "pipeline", "kind": "expand", "summary": "Inspect, watch and diagnose pipelines.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "pipeline", "--json"]}},
    {"id": "runner", "kind": "expand", "summary": "Inspect or manage runners.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "runner", "--json"]}},
    {"id": "wiki", "kind": "expand", "summary": "Create or update wiki pages.",
     "next": {"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "wiki", "--json"]}}
  ],
  "continuation": null,
  "error": {"reason": "path_unknown", "input": "pipelin"}
}
```

### How a render is drawn

1. The first line is the summary; an `error` comes before it as `reason: input`.
2. A `capability` follows with one aligned line per field, in this order: `command` (the command, then its verb),
   `kind`, `requires`, `tools`, `mutates`, `side_effects`, `returns`, `read_back`. A list is comma-joined, an empty
   list is `none`, a boolean is `yes` or `no`.
3. Choices go into blocks in this order, each block omitted when empty: "Available groups" at the root and
   "Subresources" below it (`expand` choices other than `run`, `read` and `options`); "Commands / combos"
   (`execute` choices and the `run` and `options` pointers); "References" (`read` choices and the `read` pointer);
   "Results" (`retrieve` choices); then "Relations".
4. A choice is its id and summary on one line, then its pointer as `Expand:`, `Describe:`, `Run:`, `Read:` or
   `Retrieve:` followed by the entrypoint's file name and its arguments without `--json`. An `execute` pointer lists
   its inputs as `Needs:` or `Optional:` lines, a `read` choice adds its `when` line, and an unavailable choice shows
   `Unavailable`. The id column is as wide as the longest id in the answer, at least 7 characters, plus 2 spaces;
   blank lines separate choices.
5. A relation renders as `from rel to  (evidence)`; a `continuation` renders last, as `More:` and its command.
6. A section renders as `reference#anchor  pinned <pin>`, its text verbatim, then a "Subsections" block of its
   pointers, then `More:` when it continues.

### Contract values

- **Response** (`reference_next`): `kind`, `schema_version`, `path`, `summary`, `choices`, `continuation`; `query`
  on a search; `capability` on a `run ID` level; `relations` on a `read REF` level; `error` with exit 2.
- **Choice**: `id`, `kind` (`expand`, `execute`, `read` or `retrieve`), `summary`, `next`; `when` on every `read`
  choice; `available: false` with `next: null` for a planned verb. A read choice's anchor is `args[2]` of its pointer.
- **Pointer**: `action` (`expand`, `describe`, `execute`, `read` or `retrieve`), `entrypoint`, `args`. An `execute`
  pointer adds `inputs`, each with `name`, `required` and, when the catalog has one, `summary`. Its `args` are the
  verb, each required option with a `<name>` placeholder, and `--json` when the command accepts it. `inputs` lists the
  command's and the verb's own options and capability-tier options; the universal tier is never repeated (`SKILL.md`
  names it once). A mutating command's pointer prints its plan; the plan's own result carries the apply pointer with
  the real digest, so an agent never builds `--apply` itself.
- **Names**: a capability's id is its verb, or the command's file name without `.py` when it has no verbs; `entrypoint`
  and `capability.command` are `bin/` plus the file name. From the catalog, `kind`, `requires`, `tools`, `mutates`,
  `side_effects` and `returns` are `kind`, `requires`, `required_tools`, `mutates`, `side_effects` and `returns`
  (`report_kind`, `requires_authorities`, `required_tools`, not `read_only`, `side_effects` and `returns` for a Bash
  entry). A verb's fields live in its command's existing `subcommands` entry; its `purpose`, `kind`, `returns`,
  `read_back`, `mutates`, `side_effects`, `options` and `required_options` replace the command's.
- **Order**: subresources and sections in the order the Declarations list them; capabilities in catalog order, then
  planned verbs; inputs with required ones first, then catalog order. `run ID` lists the required inputs only;
  `options` lists every input of the command and verb except `--apply` and `--confirm-plan`, which only a plan's own
  result hands out.
- **Lookup by name**: when the first segment is not a root group, it is matched against node names only, not
  capability ids or reference names. One match answers that node with its full `path`; several answer "which one?"
  with ids that join each match's path with `.`; none exits 2 with `path_unknown` and the root's choices.
- **Read-back**: a declared `read_back`; else `read-only; the result is the read-back.` for a read-only command; else
  the command's `returns`.
- **Section** (`reference_section`, from `get`): `kind`, `schema_version`, `reference`, `anchor`, `pinned` (the
  upstream commit or version for a vendored reference, the skill revision for a local one), `text` (the lines from
  the anchor's heading line to the next heading of any level, verbatim), `choices` (its subsections), `continuation`;
  `tier` when `--card` or `--part` selected one.
- **Anchors**: section 2's anchor rule; a repeated heading takes the suffixes `-1`, `-2` and so on in document order,
  as Render 12 shows for the first two of the five "Naming and color convention" headings.
- **Relations**: `infers` means automation sets the `to` label from the `from` label, as the `evidence` section
  states. `to` is an anchor of the same reference, or `REF.ANCHOR` of another declared reference.
- **Search**: a case-insensitive literal substring over each section's own text, heading included, across the whole
  pinned reference, in document order; its continuation keeps `--search`.
- **Summaries**: a declared section uses its declared `summary` and `when`; a tagged section (`gitlab-ci-yaml`) uses
  its index `description`, or its heading path when the description is longer than 100 characters, and its
  placement's `when`; any other section uses its heading path, the headings joined by a colon and a space, with a
  final period. The fixed texts are exactly these:

  | Where | Text |
  | --- | --- |
  | `run` pointer and level | `N capability to run.` or `N capabilities to run.`, then `; M planned.` when M > 0 |
  | `read` pointer and level | `N reference with sections here.` or `N references with sections here.` |
  | `options` pointer and level | `N optional input; append it as --name value.` or `... inputs; append each ...` |
  | which one | `NAME: which one?` |
  | planned verb | `Unavailable: planned in PHASE.` |
  | search level | `N section contains "TEXT".` or `N sections contain "TEXT".` |
  | search hit `when` | `Read for the passage that contains "TEXT".` |
  | subsection `when` | `Read when you need HEADING.` |

- **Serialization**: every answer goes through the one emitter, `core/report.py:emit`, which keeps its redaction
  and its `verbatim` allowlist (section 2, Lookup) and gains a compact mode for the navigator:
  `json.dumps(answer, separators=(",", ":"), ensure_ascii=False)`, fields in the schema's property order, keys not
  sorted. Byte limits are measured on that line; reports keep the indented, sorted form.
- **Limits**: one line at most 4096 bytes, 12 choices, 12 relations, 8 path segments, 100-character summaries and
  queries, 120-character `when` and input summaries, 240-character `returns` and `read_back`; a `get` page at most
  8192 bytes of `text`, cut at a line end. Past a limit, `continuation` carries the next page, for example
  `{"action": "expand", "entrypoint": "bin/reference.py", "args": ["next", "gitlab", "--page", "2", "--json"]}` or
  `{"action": "read", "entrypoint": "bin/reference.py", "args": ["get", "gitlab-ci-yaml", "rules", "--page", "2",
  "--json"]}`. Nothing is dropped or truncated.
- **Exit codes**: 0 for an answer; 2 with `error.reason` `path_unknown`, `reference_unknown`, `anchor_unknown`,
  `tier_unavailable` or `page_out_of_range`.

### Schemas

`schemas/reference-next.schema.json` defines the answer, and in its `$defs` the choice and pointer every other
pointer-carrying record reuses. `schemas/reference-section.schema.json` defines `get`. Every render above validates
against them:

```text
check-jsonschema --base-uri "file://$PWD/schemas/reference-section.schema.json" \
  --schemafile schemas/reference-section.schema.json <record>
```

`schemas/reference-next.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/spyroot/ci-skills/schemas/reference-next.schema.json",
  "title": "reference_next",
  "description": "One answer of ci-skills/bin/reference.py next (CI09-REFERENCE section 3): one level of the resource graph, its summary and a bounded set of typed pointers. Its $defs choice and pointer are the one definition every pointer-carrying record reuses.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "kind",
    "schema_version",
    "path",
    "summary",
    "choices",
    "continuation"
  ],
  "properties": {
    "kind": {
      "const": "reference_next"
    },
    "schema_version": {
      "type": "string",
      "pattern": "^1\\.[0-9]+$"
    },
    "path": {
      "type": "array",
      "maxItems": 8,
      "items": {
        "type": "string",
        "pattern": "^[a-z0-9][a-z0-9_.-]*$"
      }
    },
    "summary": {
      "$ref": "#/$defs/summary"
    },
    "query": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100
    },
    "capability": {
      "$ref": "#/$defs/capability"
    },
    "choices": {
      "type": "array",
      "maxItems": 12,
      "items": {
        "$ref": "#/$defs/choice"
      }
    },
    "relations": {
      "type": "array",
      "maxItems": 12,
      "items": {
        "$ref": "#/$defs/relation"
      }
    },
    "continuation": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "$ref": "#/$defs/pointer"
        }
      ]
    },
    "error": {
      "$ref": "#/$defs/error"
    }
  },
  "$defs": {
    "summary": {
      "type": "string",
      "minLength": 1,
      "maxLength": 100
    },
    "choice": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "id",
        "kind",
        "summary",
        "next"
      ],
      "properties": {
        "id": {
          "type": "string",
          "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
        },
        "kind": {
          "enum": [
            "expand",
            "execute",
            "read",
            "retrieve"
          ]
        },
        "summary": {
          "$ref": "#/$defs/summary"
        },
        "when": {
          "type": "string",
          "minLength": 1,
          "maxLength": 120
        },
        "available": {
          "type": "boolean"
        },
        "next": {
          "oneOf": [
            {
              "type": "null"
            },
            {
              "$ref": "#/$defs/pointer"
            }
          ]
        }
      },
      "allOf": [
        {
          "if": {
            "properties": {
              "kind": {
                "const": "read"
              }
            },
            "required": [
              "kind"
            ]
          },
          "then": {
            "required": [
              "when"
            ]
          }
        },
        {
          "if": {
            "properties": {
              "available": {
                "const": false
              }
            },
            "required": [
              "available"
            ]
          },
          "then": {
            "properties": {
              "next": {
                "type": "null"
              }
            }
          },
          "else": {
            "properties": {
              "next": {
                "$ref": "#/$defs/pointer"
              }
            }
          }
        }
      ]
    },
    "pointer": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "action",
        "entrypoint",
        "args"
      ],
      "properties": {
        "action": {
          "enum": [
            "expand",
            "describe",
            "execute",
            "read",
            "retrieve"
          ]
        },
        "entrypoint": {
          "type": "string",
          "pattern": "^bin/[A-Za-z0-9_.-]+$"
        },
        "args": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        },
        "inputs": {
          "type": "array",
          "items": {
            "$ref": "#/$defs/input"
          }
        }
      }
    },
    "input": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "name",
        "required"
      ],
      "properties": {
        "name": {
          "type": "string",
          "pattern": "^[a-z0-9][a-z0-9-]*$"
        },
        "summary": {
          "type": "string",
          "minLength": 1,
          "maxLength": 120
        },
        "required": {
          "type": "boolean"
        }
      }
    },
    "capability": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "command",
        "kind",
        "requires",
        "tools",
        "mutates",
        "side_effects",
        "returns",
        "read_back"
      ],
      "properties": {
        "command": {
          "type": "string",
          "pattern": "^bin/[A-Za-z0-9_.-]+$"
        },
        "verb": {
          "type": "string",
          "pattern": "^[a-z0-9][a-z0-9_-]*$"
        },
        "kind": {
          "type": "string",
          "pattern": "^[a-z][a-z0-9_]*$"
        },
        "requires": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        },
        "tools": {
          "type": "array",
          "items": {
            "type": "string",
            "minLength": 1
          }
        },
        "mutates": {
          "type": "boolean"
        },
        "side_effects": {
          "type": "string",
          "minLength": 1
        },
        "returns": {
          "type": "string",
          "minLength": 1,
          "maxLength": 240
        },
        "read_back": {
          "type": "string",
          "minLength": 1,
          "maxLength": 240
        }
      }
    },
    "relation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "from",
        "rel",
        "to",
        "evidence"
      ],
      "properties": {
        "from": {
          "type": "string",
          "pattern": "^[a-z0-9][a-z0-9_.:-]*$"
        },
        "rel": {
          "enum": [
            "infers"
          ]
        },
        "to": {
          "type": "string",
          "pattern": "^[a-z0-9][a-z0-9_.:-]*$"
        },
        "evidence": {
          "type": "string",
          "pattern": "^[a-z0-9][a-z0-9_-]*$"
        }
      }
    },
    "error": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "reason",
        "input"
      ],
      "properties": {
        "reason": {
          "enum": [
            "path_unknown",
            "reference_unknown",
            "anchor_unknown",
            "tier_unavailable",
            "page_out_of_range"
          ]
        },
        "input": {
          "type": "string",
          "minLength": 1
        }
      }
    }
  }
}
```

`schemas/reference-section.schema.json`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://github.com/spyroot/ci-skills/schemas/reference-section.schema.json",
  "title": "reference_section",
  "description": "One answer of ci-skills/bin/reference.py get (CI09-REFERENCE section 3): one section's own text, verbatim and bounded, the pin it was read at, and pointers to its subsections.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "kind",
    "schema_version",
    "reference",
    "anchor",
    "pinned",
    "text",
    "choices",
    "continuation"
  ],
  "properties": {
    "kind": {
      "const": "reference_section"
    },
    "schema_version": {
      "type": "string",
      "pattern": "^1\\.[0-9]+$"
    },
    "reference": {
      "type": "string",
      "pattern": "^[a-z0-9][a-z0-9-]*$"
    },
    "anchor": {
      "type": "string",
      "pattern": "^[a-z0-9][a-z0-9_-]*$"
    },
    "tier": {
      "type": "string",
      "pattern": "^(card|part:[a-z0-9-]+)$"
    },
    "pinned": {
      "type": "string",
      "minLength": 1
    },
    "text": {
      "type": "string",
      "maxLength": 8192
    },
    "choices": {
      "type": "array",
      "maxItems": 12,
      "items": {
        "$ref": "reference-next.schema.json#/$defs/choice"
      }
    },
    "continuation": {
      "oneOf": [
        {
          "type": "null"
        },
        {
          "$ref": "reference-next.schema.json#/$defs/pointer"
        }
      ]
    },
    "error": {
      "$ref": "reference-next.schema.json#/$defs/error"
    }
  }
}
```

### Declarations: exactly what `core/catalog.py` gains

Nodes, each with its one summary:

| Node | Summary |
| --- | --- |
| root | Discover what ci-skills can inspect, run or read. |
| `gitlab` | GitLab pipelines, jobs, runners, issues, milestones and wikis. |
| `gitlab access` | Prove GitLab access and read back the selected target. |
| `gitlab api` | Read one caller-selected GitLab or GitHub API resource. |
| `gitlab issue` | Create or reuse exact bug issues. |
| `gitlab job` | Inspect job state, output and failure evidence. |
| `gitlab mcp` | Connect an MCP client to GitLab's MCP server. |
| `gitlab milestone` | Create or update milestones. |
| `gitlab pipeline` | Inspect, watch and diagnose pipelines. |
| `gitlab runner` | Inspect or manage runners. |
| `gitlab wiki` | Create or update wiki pages. |
| `k8s` | Cluster, workload, storage and network operations. |
| `k8s build` | Plan exact-commit OpenShift binary builds. |
| `k8s ceph` | Ceph health, OSDs, placement groups and host kernel messages. |
| `k8s cilium` | Cilium agents, operator and per-node health. |
| `k8s events` | What the cluster reported during an interval. |
| `k8s network` | Physical uplink MTU consistency across nodes. |
| `k8s storage` | Claims, volumes, attachments and the pods using them. |
| `claude` | Agent clients and their configuration. |
| `claude mcp` | Configure MCP servers in Claude Code. |
| `harbor` | Registry, images, charts and related operations. |

`claude`, `claude mcp`, `gitlab mcp` and `harbor` stay hidden until something is placed in them.

Placement of every command:

| Node | Command | Capability ids |
| --- | --- | --- |
| root | `access_check.py` | `access_check` |
| `gitlab access` | `gitlab_access.py` | `check` |
| `gitlab api` | `bin/ci-api` | `check`, `get` |
| `gitlab issue` | `gitlab_issue.py` | `open-bug`, `create-bug` |
| `gitlab job` | `gitlab_job.py` | `gitlab_job` |
| `gitlab milestone` | `gitlab_milestone.py` | `create`, `update`, `adjust-time` |
| `gitlab pipeline` | `gitlab_pipeline.py` | `get`, `watch` (CI11-TOOLS, Combos) |
| `gitlab runner` | `gitlab_runner.py` | `assign`, `create`, `tag` |
| `gitlab wiki` | `gitlab_wiki.py` | `create`, `update` |
| `k8s build` | `bin/ci-binary-build` | `ci-binary-build` |
| `k8s ceph` | `ceph_cluster.py`, `ceph_kernel.py` | `ceph_cluster`, `ceph_kernel` |
| `k8s cilium` | `cilium_status.py`, `cilium_node.py` | `cilium_status`, `cilium_node` |
| `k8s events` | `event_trace.py` | `event_trace` |
| `k8s network` | `k8s_verify_mtu_consistency.py` | `k8s_verify_mtu_consistency` |
| `k8s storage` | `storage_report.py` | `storage_report` |

One purpose per verb, distinct among siblings, read from what each verb's code does, stored in the command's
`subcommands` entry:

| Command | Verb | Purpose |
| --- | --- | --- |
| `gitlab_pipeline.py` | `get` | Read one pipeline: status, stages and job counts. |
| `gitlab_pipeline.py` | `watch` | Watch a pipeline and its linked downstream runs. |
| `gitlab_milestone.py` | `create` | Plan, then create, an exact milestone with independent read-back. |
| `gitlab_milestone.py` | `update` | Change an exact milestone's title, description, state or dates, with read-back. |
| `gitlab_milestone.py` | `adjust-time` | Change only an exact milestone's start or due date, with read-back. |
| `gitlab_issue.py` | `open-bug` | Reuse the open bug with this exact title, or create it, with read-back. |
| `gitlab_issue.py` | `create-bug` | Alias of open-bug. |
| `gitlab_wiki.py` | `create` | Create a wiki page from a content file, with read-back. |
| `gitlab_wiki.py` | `update` | Replace an existing page's content by its exact slug, with read-back. |
| `gitlab_runner.py` | `assign` | Assign an existing runner to the selected project or group. |
| `gitlab_runner.py` | `create` | Create a runner record; its one-time token goes to --token-out. |
| `gitlab_runner.py` | `tag` | Add tags to an existing runner record, with before/after read-back. |
| `bin/ci-api` | `check` | Check the credential for one caller-selected GitHub or GitLab host. |
| `bin/ci-api` | `get` | Read one caller-selected API endpoint with bounded output. |

`gitlab_access.py check` keeps its declared purpose. `bin/ci-api` declares its required options per verb: `check`
takes `--provider`; `get` takes `--provider` and `--endpoint`. The GitLab commands declare
`required_tools: ("glab",)`, the client their transport runs. `subcommands["watch"]` also declares
`kind: gitlab_pipeline_watch`, `returns` and `read_back` as Render 5 shows them, and its own `options`
`--related-name`, `--interval` and `--timeout` with the help texts of Render 6; `options_for` and
`tests/python/test_catalog.py` compare options per verb. `schemas/skill-manifest.schema.json` and
`schemas/command-contract.schema.json` accept these optional `subcommands` fields. `reference.py` itself is an
`OFFLINE_COMMANDS` entry (section 6, step 2) with verbs `next` and `get` and options `--page`, `--search`, `--card`,
`--part`, `--json`, `--yaml`, `--human` and `--describe`.

Planned verbs, each shown unavailable until it ships (the verbs CI11-TOOLS names for commands that exist today):

| Command | Planned verbs |
| --- | --- |
| `gitlab_job.py` | `get`, `list`, `watch`, `logs` |
| `gitlab_pipeline.py` | `list`, `logs`, `children`, `start`, `retry`, `cancel` |
| `gitlab_runner.py` | `list`, `get`, `delete`, `reset-token` |
| `gitlab_milestone.py` | `list` |

References, each with its one summary:

| Reference | `source`, then `path` or `index` under `references/` | Summary |
| --- | --- | --- |
| `access` | local, `access.md` | Where targets and credentials come from, and the live receipt. |
| `project-binding` | local, `project-binding.md` | How a project binds its target and kubeconfig sources. |
| `gitlab-labels` | vendored, `vendor/gitlab-labels/index.json` | GitLab label families and how they relate. |
| `gitlab-ci-yaml` | vendored, `vendor/gitlab-ci-yaml/index.json` | The GitLab CI/CD YAML syntax, by keyword. |
| `gitlab-mcp-server`, `claude-code-mcp`, `mcp-spec` | vendored | declared when each is vendored |

These are the `REFERENCES` entries CI07-SCHEMA (Pointers) and CI08-ROUTING extend; `source` is new here and is not
CI07's `kind` (`operations` or `knowledge`), which those phases add.

Reference sections and where they appear; `when` renders as written:

- `access`, node root: `where-the-target-file-comes-from` "Where a command finds its target file." when "Read when a
  command reports no target file."; `effective-credential-sources` "Which credential each authority uses." when
  "Read when a command is BLOCKED on a credential."; `full-three-authority-live-receipt` "What the full live receipt
  proves." when "Read before capturing a receipt."
- `project-binding`, node `k8s`: `project-specific-kubeconfig-resolver` "How a binding selects kubeconfigs." when
  "Read when a project declares its own kubeconfig sources."
- `gitlab-labels`: node `gitlab issue`, the eight sections and five relations of Render 9; node `gitlab milestone`,
  `release-scoping-labels` "Deliverable, Stretch, Next Patch Release: what a milestone's issues carry." when "Read
  when scheduling issues into a milestone." and `workflow-labels` as in Render 9.
- `gitlab-ci-yaml`: node `gitlab pipeline`, the keywords section 2 tags with `pipeline`, each with its index
  `description` and the placement's `when` "Read when writing or reading this pipeline's CI YAML."
- `gitlab-mcp-server`, `claude-code-mcp` and `mcp-spec`: nodes `gitlab mcp` and `claude mcp`; their anchors,
  summaries and `when` are declared from the first fetch.

The same data in `core/catalog.py`, one entry per row above:

```python
NAV_NODES = {
    (): "Discover what ci-skills can inspect, run or read.",
    ("gitlab", "pipeline"): "Inspect, watch and diagnose pipelines.",
}
NAV_PLACEMENT = {"access_check.py": (), "gitlab_pipeline.py": ("gitlab", "pipeline")}
COMMANDS["gitlab_milestone.py"]["subcommands"]["adjust-time"] = {
    "required_options": ["--milestone-id"],
    "purpose": "Change only an exact milestone's start or due date, with read-back.",
}
PLANNED_VERBS = {"gitlab_milestone.py": {"list": "CI11-TOOLS"}}
REFERENCES = {"gitlab-labels": {"source": "vendored", "index": "references/vendor/gitlab-labels/index.json",
                                 "summary": "GitLab label families and how they relate."}}
REFERENCE_SECTIONS = (
    {"reference": "gitlab-labels", "anchor": "priority-labels", "nodes": (("gitlab", "issue"),),
     "summary": "priority::1 to priority::4; their meaning lives in the handbook triage page.",
     "when": "Read when setting priority; choosing a level needs the handbook page."},
)
RELATIONS = (
    {"reference": "gitlab-labels", "from": "group-labels", "rel": "infers", "to": "stage-labels",
     "evidence": "group-labels"},
)
```

Acquisition stays in `vendor/vendor.toml` (section 2, Declaration), including `pin = "latest"` for a reference that
follows its newest upstream version:

```toml
[references.mcp-spec]
source = "<MCP specification>"
pin = "latest"
license = "<checked at declaration>"
chunker = "markdown-headings"
```

A new command, verb, reference or section adds rows here in the same pull request; nothing else changes.

### Steps

Who adds what, in order:

1. The pipeline watch pull request (CI11-TOOLS, Pipeline watch, exact) lands first, so `get` and `watch` exist.
2. The agent that claims the queue record `CI09-POINTER-OUTPUT` then delivers, in one pull request after block 0:
   - `ci-skills/lib/python/core/catalog.py`: the tables above as data, with the verb purposes, `required_tools` for the
     GitLab commands and the `watch` fields;
   - `ci-skills/lib/python/core/navigate.py`: builds every answer and section from that data, the commands' options and
     the reference files, and draws the human view by the rules above; it types no other text than the fixed texts;
   - `ci-skills/bin/reference.py`: a thin main with the grammar above and `--describe`;
   - `ci-skills/tools.json` re-rendered, and one `SKILL.md` sentence: "Start at `bin/reference.py next` and follow
     the pointers; never read `tools.json`, an `index.json` or a whole reference."
3. Acceptance: Renders 1 to 7 and 14 come out of the real tool exactly as shown and validate against the schemas
   above, and the five acceptance points below hold. Renders 8 to 13 follow when section 2 vendors their references.

### Compact, versioned, pointer-based output

Discovery, capability descriptions, and combo results must use versioned,
machine-readable output that is compact by default.

A response contains only the current resource's summary, essential fields,
and a bounded set of typed pointers. It must not recursively embed child
resources, full command contracts, schemas, reference bodies, logs, or
component reports.

Every pointer identifies its target, explains its purpose briefly, and
provides the exact machine-callable next invocation. Distinguish:
expand, describe, execute, read, and retrieve-result.

READ pointers include the specific reference anchor and when to read it.
EXECUTE pointers expose the callable operation and required inputs, or
provide a pointer to its compact description. Missing inputs are explicit.

Expansion is optional. Support direct addressing of known resources.
Do not force root-to-leaf navigation when the requested operation or
reference is already known.

New capabilities, references, and result sections extend the resource
graph through declarations and pointers. They must not enlarge the
default response by adding their full contents inline.

Set concrete limits for serialized response bytes, entries per response,
and summary length. When entries exceed a limit, return an explicit
continuation or narrowing call. Never silently omit entries or truncate
JSON. Explicit detail reads must also have defined bounds.

Version the pointer and response contracts. Preserve the meaning of
existing fields and actions. Do not use an unrestricted extensions object
to bypass the compactness limits or introduce undefined behavior.

Reuse the existing catalogue, navigator, commands, and schema owners.
A pointer may invoke an existing command; it does not require another
registry, workflow engine, or background service.

Acceptance must demonstrate that:

- a larger catalogue still produces bounded default responses;
- advertised resources remain reachable through returned pointers;
- following one pointer retrieves only the selected resource;
- combo results do not inline all component reports;
- every advertised invocation and reference anchor resolves correctly.

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
6. **serve**: evidence the bounded record; failures as section 3, Contract
   values, Exit codes, defines them.
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
2. Add `ci-skills/lib/python/core/{reference,navigate,http,tool_operations}.py`, the
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
6. The `SKILL.md` sentence of section 3, Steps; the description gains the
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
   - `ci-skills/bin/reference.py get gitlab-ci-yaml triggerforward --json`
     reads back `kind: reference_section`, `anchor: triggerforward`, `pinned`
     equal to the declared sha, and `text` whose first line is
     ``#### `trigger:forward` ``;
   - `ci-skills/bin/reference.py next gitlab pipeline run --json` reads back
     Render 4 (section 3) and validates against `schemas/reference-next.schema.json`;
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
