# GAL-REFERENCE: collecting and indexing reference tools

**Status:** Proposed
**Date:** 2026-10-02
**Deciders:** repository maintainers

## Context

Our skills and tools call many command-line tools. Two lists already name
them:

- **Our own tools**, the `bin/ci-*` commands that PR #2 adds: `ci-api`
  (read-only GitHub and GitLab API reads) and `ci-binary-build` (an
  exact-commit OpenShift build plan), plus `scripts/check.sh`.
- **The toolchain contract** from the shared standards
  (`bin/agent-tools.sh`): 61 tools observed on 2026-10-02, each named by
  full path, under the rule "call every tool by full path".

To choose a command and its flags, an agent today reads `--help` text or
upstream documentation. Two things are missing:

- a record of which capabilities and options our skills rely on;
- a check that fails when an upgrade renames or removes one.

GAL-VENDOR copies upstream skills, which are prose about tools. This record
covers the tools themselves: their commands (capabilities) and their flags
(options).

Measured on 2026-10-02 against the local binaries:

| Tool | Version | Top-level commands | Example |
| --- | --- | --- | --- |
| `glab` | 1.120.0 | 48 | `glab mr create`: 31 flags |
| `gh` | 2.98.0 | 35 | `gh help reference`: 94,244 bytes |
| `kubectl` | v1.36.4 | 45 | `kubectl get`: 53 flags |

All three are built with cobra, which answers a hidden `__complete` command
with machine-readable lines, one `name<TAB>description` per subcommand or
flag. The command exists for shell completion, appears in no `--help`
output, and is not a documented interface.

What our skill uses today, from its code:

| Tool | Capabilities used |
| --- | --- |
| `gh` | `auth status`, `api` (reads) |
| `glab` | `auth status`, `api` (reads) |
| `kubectl` | `get`, `auth whoami`, `config view`, `exec` |
| `cilium` | node status on a selected node (PR #5) |
| `ceph` | `status`, `health`, `osd`, through `kubectl exec` (PR #7) |

PR #2's `SKILL.md` says to use `ci-api` instead of one-off `gh api` or
`glab api` reads. The k8s skill makes such reads itself; whether it should
call `ci-api` instead is open, because an installed skill loads no code
from another skill.

Constraints:

- **Compute rather than copy.** What can be computed at runtime is
  computed, not copied.
- **One declaration** per fact.
- **CI is offline and deterministic.**
- **Upstream text keeps its license.**
- **Compact for agents.** Lazy loading, as in GAL-ROUTING.
- **No implicit files.** Nothing is written without an explicit output path.

## Names: tool, capability, option

These definitions hold whichever option below is chosen.

- **Tool:** `name` (the binary), `version` (as the tool reports it),
  `source` (how the record was collected), `digest` (of the binary or the
  source text).
- **Capability:**
  - `id`: the command path joined by dots, for example `mr.create` or
    `auth.whoami`.
  - `command`: for example `glab mr create`.
  - `summary`: the upstream one-line description.
  - `mutates`: declared, never guessed.
  - `tags`
- **Option:** `flag` (for example `--title`) and `summary`. Whether a flag
  takes a value, or is required, is declared only where we use the flag;
  the completion protocol does not report it.

```json
{
  "tool": "glab",
  "version": "1.120.0",
  "source": "complete",
  "capabilities": [
    {
      "id": "mr.create",
      "command": "glab mr create",
      "summary": "Create a new merge request.",
      "mutates": true,
      "options": [{"flag": "--title", "summary": "..."}]
    }
  ]
}
```

## Options considered

### Option A: copy help text and documentation into the repository

| Dimension | Assessment |
| --- | --- |
| Complexity | Low to collect, high to use |
| Cost | Large: `gh help reference` alone is 94,244 bytes |
| Freshness | Stale on every upgrade |
| CI | Offline and deterministic |

**Pros:**

- The fullest text, with examples and notes.
- Needs no binary to read.

**Cons:**

- Options must be parsed out of prose.
- It duplicates upstream documentation.
- Copied text carries license and attribution duties.
- It adds Markdown to lint and keep current.

### Option B: a generated registry, committed

Crawl `__complete`, normalize the result into the record shape, commit it,
and have CI check that it is current, as it does for `tools.json`.

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium |
| Cost | One generated file per tool, plus a freshness check |
| Freshness | A snapshot of one host's versions |
| CI | Offline and deterministic |

**Pros:**

- Structured and compact.
- An upgrade shows up as a reviewable diff.
- CI reads it without the binaries.

**Cons:**

- It commits what can be computed at runtime.
- It only matches the versions installed where it was regenerated.
- It rests on a hidden protocol.

### Option C: runtime discovery, nothing stored

An adapter crawls `__complete` of the installed binary on demand, only for
the subtree asked for, and returns the record shape.

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium |
| Cost | One process call per command node walked |
| Freshness | Always matches the binary actually used |
| CI | Needs recorded fixtures; CI does not install `glab` |

**Pros:**

- Nothing to keep current.
- Answers come from the exact binary that will run.
- Only the requested subtree is loaded.
- No upstream text is copied.

**Cons:**

- The binary must be present at call time.
- A full crawl is slow without a cache.
- It rests on a hidden protocol.

### Option D: declared capabilities, checked against the tool

Declare, in one file, the capabilities and options our skills use, each
with `mutates`. A contract test compares each declaration with discovery
from the installed tool, the same pattern `tests/test_catalog.py` uses to
compare the catalog with each script's real parser.

| Dimension | Assessment |
| --- | --- |
| Complexity | Low |
| Cost | A short hand-kept list; about nine capabilities today |
| Freshness | Upgrades that break a declaration fail the test |
| CI | Fixtures in CI; the live check runs where tools exist |

**Pros:**

- A small, reviewed list of exactly what we depend on.
- Breakage on upgrade is caught.
- The read-only skills can prove they call no mutating capability.

**Cons:**

- Hand-maintained.
- Not a full index.
- The live check needs the binaries.

### Option E: an external search index

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Cost | A service to run, with network and credentials |
| Freshness | Depends on an ingestion job |
| CI | Not offline |

**Pros:**

- Fuzzy search at scale.

**Cons:**

- Far beyond three tools.
- State outside the repository.
- Breaks offline CI.

## Trade-off analysis

- **Freshness against determinism.** Runtime discovery (C) is always
  current but needs the binary. Committed copies (A, B) are deterministic
  but go stale.
- **The rules favour two options.**
  - "Compute what can be computed at runtime" favours C for the full index.
  - "One declaration" and "check beforehand" favour D for what our skills
    depend on.
- **Structure against stability.** The completion protocol gives
  structure that prose (A) does not, but it is undocumented. One adapter
  should own it, with tests on recorded output, so that a change breaks in
  one place.
- **Fit.** E solves a problem three tools do not have.

## Decision

Proposed: **C and D together.**

- **Collect:** runtime discovery through one adapter in `tools/skillkit/`,
  using `__complete`. Help-text parsing is kept only as a fallback for a
  tool that is not built with cobra; none is today.
- **Store:** nothing generated in the repository. With no explicit
  `--cache-dir`, nothing is written. A given cache is keyed by binary
  digest and is never authoritative.
- **Declare:** the capabilities our skills use, with `mutates` and the
  options we pass, in one file in `tools/skillkit/`, contract-tested:
  - **In CI:** against recorded completion output for the versions above.
  - **Where the tools are installed:** against the live binary.
- **Our own tools:** each `bin/ci-*` command gains `--describe`, printing
  its capabilities and options in the record shape, as the k8s skill's
  commands already do. Discovery reads that, not help text; these tools
  are Bash, not cobra.
- **Index:** `bin/ci-skills tools [NAME]` lists tools and capability
  ids. `bin/ci-skills tools NAME --describe ID` prints one capability
  with its options. These are the same hops as GAL-ROUTING.

## Consequences

- **Easier:**
  - choosing a command and its flags without reading whole help pages;
  - catching a renamed or removed flag at upgrade time;
  - proving that a read-only skill calls no mutating capability.
- **Harder:**
  - the hidden protocol may change;
  - CI depends on recorded fixtures;
  - `bin/ci-skills` gains one more verb.
- **Revisit:**
  - when a tool not built with cobra is added;
  - when the tool count passes about ten, where a cache by default may pay
    off;
  - if upstream publishes a machine-readable reference.

## Action items

1. [ ] Agree the names: tool, capability (`id` as the dotted command path)
   and option.
2. [ ] Choose the option; C with D is proposed.
3. [ ] Build the discovery adapter, with fixtures recorded from `glab`
   1.120.0, `gh` 2.98.0 and `kubectl` v1.36.4.
4. [ ] Declare the capabilities used today (the table above) and add the
   contract test.
5. [ ] Add the `tools` verb to `bin/ci-skills` (after GAL-CATALOG).
6. [ ] Decide where the live contract check runs, since CI installs no
   `glab` (as with `bats`, see GAL-TESTS).
