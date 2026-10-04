# Field notes

What we observed while building this skill and running it against a real
cluster, and what each observation says to improve. Every observation is a
reading, not a theory: the evidence is named beside it.

Each observation records what we met **at the time**. Where the defect behind
one has since been fixed, the fix is named in the observation itself, so a
reader never starts work that is already done. Where it is still open, it says
so. Project names are deliberately absent: this repository is neutral by gate
(`tools/check_project_neutrality.py`), and none of the lessons depend on whose
cluster it was.

## What we observed

### 1. A declared location resolves free; an undeclared one cost six probes

The access gate resolved GitHub and GitLab on the first call, because the target
file named the host and repository. The kubeconfig took six separate probes
across one session, because nothing named it: environment, default path,
per-project guesses. Same gate, same host, same operator — the only difference
was whether the location was declared.

After declaring `kubernetes.kubeconfigs`, a zero-argument run from an unrelated
working directory returned `PASS` with 65 storage records and reported
`target:kubernetes.kubeconfigs` as the source it used. That resolution order is
locked by `../tests/python/test_catalog.py` and visible in every receipt as
`credential_sources.kubernetes`; the probe count is session recollection and is
not citable from this repository.

### 2. An instruction duplicating a declaration goes stale; the agent sees it first

A subagent was briefed to `export KUBECONFIG=<overlay>:<real>` before any
cluster command, because at the time the target declared no kubeconfig. After
the target declared one, that subagent ran the briefing anyway and then reported
back that the export was *not* what resolved the cluster —
`credential_sources.kubernetes` read `target:kubernetes.kubeconfigs`, which
precedes the environment in the chain (`core/credentials.py`, order locked by
`../tests/python/test_catalog.py`).

Only the report noticed the instruction had gone dead. A briefing that restates
a declaration cannot be kept correct; one that cites the declaration cannot go
stale. How long it had been dead is session recollection.

### 3. A cold caller meets a failure first, so failures obey the output contract

With no target file anywhere, a piped run wrote nothing to stdout and a single
`BLOCKED: no_target_file…` line to stderr. The caller's `json.loads(stdout)`
raised `JSONDecodeError`. The success path had been switched to reader-driven
output; the failure path still keyed on explicit flags. The one shape a program
could not parse was the one it met first, while the manifest advertised
`default_output: json when stdout is not a terminal` the whole time.

**Fixed** in `core/cli.py` — the failure envelope resolves its mode with
`output_mode(args)` like any success — with a regression test at
`tests/test_target_resolution.py::test_a_cold_run_with_no_target_still_answers_a_pipe_in_json`.

### 4. A gate that reads prose is not a gate

The drift test compared each command's catalog entry against its `--help` text.
`argparse` renders the epilog *after* the options block, and the epilog names
`--json`, `--yaml`, `--human`, `--describe` and `--target` as prose. Five of the
eight universal options were therefore reported as accepted whether `argparse`
defined them or not. There was no drift at the time — the catalog and the
parsers agreed. The gate asserting that guarantee was the thing that did not
hold.

**Fixed** in `../tests/python/test_catalog.py`: `_actual_options` reads
`build_parser()._actions` instead of rendered help, and each entrypoint exposes
`build_parser()` for it. A parser object carries no prose.

### 5. Writing the test found what reading the code did not

The project-tier candidate was built as `Path(PROJECT_DIR) / TARGET_FILENAME` —
relative. It survived its author, a review of the committed diff, and a second
reading, because at a glance it looks like the two constants it is composed
from. The first assertion that compared the resolved path with an absolute one
failed immediately. A relative candidate reports `target_file` as a string that
identifies no file and means a different place for every caller.

**Fixed** in `core/cli.py`, where the candidate is now
`Path.cwd() / PROJECT_DIR / TARGET_FILENAME`, with the tier tests in
`../tests/python/test_target_resolution.py`. Worth knowing that the same shape can
reappear in a module of its own: a `DEFAULT_TARGET = Path(".ci-skills/…")` has
been seen on an unmerged branch, where no tier test covers it.

### 6. A content digest makes the receipt the last step

Live acceptance pins a digest of the skill tree, so any byte under it
invalidates the committed receipt. Three recaptures were needed in one pass
before the order became deliberate: finish every edit, then capture.

The scope is exactly `skills/ci-skills` — 22 files, as the
receipt's own `file_count` records — so a typo in `SKILL.md` or under
`references/` costs a recapture, while this document does not: `docs/` is
outside the digest. Still open as a cost, not a defect.

### 7. `PARTIAL` with `access_proven: true` is the finding

Two committed receipts, three hours apart, both returned `PARTIAL` from the
Cilium check with `access_proven: true` while the gate itself passed:

| captured | agent health | errors |
| --- | --- | --- |
| `19:15:44Z` | 8 of 9 | one `agent_not_ready` |
| `22:35:26Z` | 7 of 9 | two `agent_not_ready` |

The cluster degraded further between them, and both runs reported *which*
component was unhealthy rather than a failed read. That distinction is what
lets this skill run on the degraded cluster it exists to diagnose — and the
pair also shows the status tracking reality rather than being pinned to a
single observation.

### 8. A correlator only in JSON cannot be cited from a human run

Asked for the receipt correlator after running `--human`, a subagent found
`receipt_sha256` absent, read `core/access.py`, quoted the docstring explaining
that the field digests the in-memory gate rather than the written receipt, and
declined to invent a value. Correct behaviour, and a gap: the human summary
drops the identifier that ties several reports into one audit trail. Still open.

### 9. A shell turns `--last 15m` into one token when the value is unquoted

An unquoted variable expansion delivered `--last` one argument instead of two,
and the window parser refused it. The tool was right and the invocation was
wrong, but the error described the accepted format rather than the likely cause.
Still open.

### 10. A collector report needs the gate's own provenance

Collector reports named the authorities they used but not which declared
location selected them, so a per-project target and the user default were
indistinguishable in the evidence — while the documentation promised every
report names its own source.

**Fixed** in `core/access.py`: `access_evidence` carries `target_file` and
`target_source`, and `core/portable.py` digests the path in the committable
form. Verified live — a collector report reads `target_source: "user"`.

## What to improve

### Traced to an observation above

**From 9 — say what the shell did.** Name the quoting trap in `--last` help and
in the manifest entry, and have the parse error suggest it when the value
contains whitespace.

**From 8 — do not drop the correlator.** Carry the gate correlator into the
human summary, or state in `SKILL.md` that correlating several runs requires
`--json`.

**From 6 — separate the code digest from the prose digest.** A typo in
`SKILL.md` or under `references/` should not invalidate live evidence. Weigh it
carefully rather than assuming: `SKILL.md` changes agent behaviour, so
documentation is not obviously outside the executed surface.

**From 1 and 2 — collapse the two setup steps.** One command that copies the
template to the user tier and opens it would remove the gap between "install
the skill" and "create your target". Copying a *template* is not provisioning a
credential, so this stays inside the resolve-never-provision rule that
[references/access.md](../skills/ci-skills/references/access.md)
owns.

**From 3 — assert the cold-run failure per command.** One command's failure
shape is covered. A row per command would catch an adapter that bypasses the
shared failure path.

**From 5 — one fixture can hide a tier test.** `../tests/python/conftest.py` sets `HOME`
to the working directory, so under `run_script` the project and user tiers
resolve to the same path. A tier test written against that fixture would be
degenerate without failing.

### From review, with no field reading behind them

These came out of auditing the code, not from meeting them on a live run. They
are listed because they are open, not because anything has demonstrated them.

**The neutrality gate matches one project marker.** It would not catch a home
directory, a company domain or another project's hostname committed into the
skill tree. The workflow runs `yamllint` on `standards-binding.yaml`, but has
no semantic gate for its schema, pinned revision or required contracts.

**Five deferred defects.** `_auth_mechanism` rejecting a kubeconfig user that
carries both `token` and `tokenFile`; `collect_storage` keying controllers
without kind, so a Deployment and a StatefulSet of one name in one namespace
collide; `collect_events` reading `series.lastObservedTime` but dropping
`series.count`; credential containment applied to declared kubeconfig paths but
not to the environment and default branches; `emit` writing `<kind>.json`, so a
second run into one `--output-dir` overwrites the first.

**One host is not a fleet.** A receipt is per host and not transferable, and
`acceptance/expected.toml` declares exactly one executor. A second execution
host should record its own.

## The one rule the rest of this follows

Declare, resolve, and report — in that order. The chain itself is an ordered
search with four declared tiers, and
[references/access.md](../skills/ci-skills/references/access.md)
owns it; what this adds is only that a *caller* should not run its own search
alongside it. One call, then read the answer, and no stale instruction,
forgotten export or quietly winning lower tier can aim the run at the wrong
cluster. Every observation above is either that rule working or the cost of a
place it had not reached yet.
