# CI-REFERENCE: collecting and indexing reference tools

**Status:** Proposed
**Date:** 2026-10-02
**Deciders:** repository maintainers
**Depends on:** CI01-CATALOG, CI-SCHEMA and CI02-CLI

## Context

Our skills and tools call many command-line tools. Two lists already name
them:

- **Our own tools:** the `bin/ci-*` commands that PR #2 adds, `ci-api`
  (read-only GitHub and GitLab API reads) and `ci-binary-build` (an
  exact-commit OpenShift build plan), plus `scripts/check.sh`.
- **The toolchain contract** from the shared standards
  (`bin/agent-tools.sh`): 61 tools observed on 2026-10-02, each named by
  full path, under the rule "call every tool by full path".

To choose a command and its flags, an agent today reads `--help` text or
upstream documentation. Two things are missing:

- a record of which operations our skills rely on;
- a check that fails when an upgrade renames or removes one.

CI-VENDOR copies upstream skills, which are prose about tools. This record
covers the tools themselves.

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

PR #2's `SKILL.md` says to use `ci-api` instead of one-off `gh api` or
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

## Names: tool, operation, option

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

Schema: `tool-operations` (CI-SCHEMA).

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

## How references relate to tools

Skill commands and references point at operations through `uses`, declared
once on the user's side (CI-SCHEMA, "Pointers"). For example,
`references/access.md` uses `gh:auth.status`, `glab:auth.status`,
`kubectl:auth.whoami` and `kubectl:auth.can-i`.

`bin/ci-skills tools NAME --describe ID` adds the reverse, `used_by`,
computed by discovery and never stored. Every `uses` id must resolve, and
every declared operation must be used.

## Options considered

### Option A: copy help text and documentation into the repository

- **Pros:**
  - the fullest text, with examples;
  - needs no binary to read.
- **Cons:**
  - it is large: `gh help reference` alone is 94,244 bytes;
  - it goes stale on every upgrade;
  - options must be parsed out of prose;
  - copied text carries license duties.

### Option B: a generated registry, committed

Crawl the tools, commit the normalized result, and check it is current, as
`tools.json` is.

- **Pros:**
  - structured;
  - upgrades show as diffs;
  - CI needs no binaries.
- **Cons:**
  - it commits what can be computed at runtime;
  - it matches only the host where it was regenerated.

### Option C: runtime discovery through `__complete`, nothing stored

- **Pros:**
  - always matches the installed binary;
  - nothing to keep current.
- **Cons:**
  - a hidden protocol;
  - the binary must be present;
  - completion output mixes in argument choices, active-help lines and a
    final directive, and some completions run callbacks.

### Option D: declared operations, checked against the installed tool

Declare each operation our code uses, as above, in one file in
`tools/skillkit/`. A contract test checks every declared command and flag
against the installed binary, the same pattern `../../tests/python/test_catalog.py` uses
for the k8s skill's parsers.

- **Pros:**
  - a small, exact list of what we depend on;
  - an upgrade that removes a used flag fails;
  - read-only claims are explicit.
- **Cons:**
  - hand-maintained;
  - not a full index;
  - the live check needs the binaries.

### Option E: an external search index

- **Pros:**
  - fuzzy search at scale.
- **Cons:**
  - a service to run, with network and credentials;
  - far beyond our needs;
  - breaks offline CI.

## Decision

Proposed: **D, with C as optional navigation.**

- **Declare.** Every operation our code uses is declared, with its exact
  `argv`, method, flags and `mutates`, in one file in `tools/skillkit/`. The
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

## Consequences

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

## Steps

1. Derive the declared operations from every call site, including
   `kubectl auth can-i` and the fixed `exec` arguments.
2. Add the `tool-operations` schema (CI-SCHEMA) and the declaration file.
3. Add the `tools` verb and the contract test, with recorded fixtures for
   `glab` 1.120.0, `gh` 2.98.0 and `kubectl` v1.36.4.
4. Add the `uses` pointers (CI-ROUTING).
5. Open one pull request after CI01-CATALOG; the `validate` workflow must
   pass.
6. Read back: `bin/ci-skills tools` lists every tool and operation, and each
   operation shows `used_by`.

## Open decision

- Which approved executor runs the live compatibility check, since CI
  installs no `glab`.
