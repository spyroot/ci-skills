# Field notes

What we observed while building this skill and running it against a real
cluster, and what each observation says to improve. Every observation is a
reading, not a theory: the evidence is named beside it. Project names are
deliberately absent: this repository is neutral by gate
(`tools/check_project_neutrality.py`), and none of the lessons depend on
whose cluster it was.

## What we observed

### 1. A declared location resolves free; an undeclared one cost six probes

The access gate resolved GitHub and GitLab on the first call, because the target
file named the host and repository. The kubeconfig took six separate probes
across one session, because nothing named it: environment, default path,
per-project guesses. Same gate, same host, same operator — the only difference
was whether the location was declared.

After declaring `kubernetes.kubeconfigs`, a zero-argument run from an unrelated
working directory returned `PASS` with 65 storage records and reported
`target:kubernetes.kubeconfigs` as the source it used.

### 2. An instruction duplicating a declaration goes stale; the agent sees it first

A subagent was briefed to `export KUBECONFIG=<overlay>:<real>` before any
cluster command, because at the time the target declared no kubeconfig. After
the target declared one, that subagent ran the briefing anyway and then reported
back that the export was *not* what resolved the cluster —
`credential_sources.kubernetes` read `target:kubernetes.kubeconfigs`, which
precedes the environment in the chain.

The instruction had been dead for an hour and only the report said so. A
briefing that restates a declaration cannot be kept correct; one that cites the
declaration cannot go stale.

### 3. A cold caller meets a failure first, so failures obey the output contract

With no target file anywhere, a piped run wrote nothing to stdout and a single
`BLOCKED: no_target_file…` line to stderr. The caller's `json.loads(stdout)`
raised `JSONDecodeError`. The success path had been switched to reader-driven
output; the failure path still keyed on explicit flags.

The one shape a program could not parse was the one it met first, and the
manifest advertised `default_output: json when stdout is not a terminal` the
whole time.

### 4. A gate that reads prose is not a gate

The drift test compared each command's catalog entry against its `--help` text.
`argparse` renders the epilog *after* the options block, and the epilog names
`--json`, `--yaml`, `--human`, `--describe` and `--target` as prose. Five of the
eight universal options were therefore reported as accepted whether `argparse`
defined them or not.

There was no drift at the time — the catalog and the parsers agreed. The gate
asserting that guarantee was the thing that did not hold.

### 5. Writing the test found what reading the code did not

The project-tier candidate was `Path(".ci-skills/target.toml")` — relative. It
survived its author, a review of the committed diff, and a second reading. The
first assertion that compared the resolved path with an absolute one failed
immediately. A relative path reports `target_file` as a string that identifies
no file and means a different place for every caller.

### 6. A content digest makes the receipt the last step

Live acceptance pins a digest of the whole skill tree, so any byte under it —
including a typo in this file's neighbours — invalidates the committed receipt.
Three recaptures were needed in one pass before the order became deliberate:
finish every edit, then capture.

### 7. `PARTIAL` with `access_proven: true` is the finding, twice over

Two independent live runs returned `PARTIAL` because 2 of 9 CNI agents could not
answer a health exec, while every read the gate needed succeeded. Both runs
reported the two unhealthy components rather than a failed read. The distinction
is what lets this skill run on the degraded cluster it exists to diagnose.

### 8. A correlator only in JSON cannot be cited from a human run

Asked for the receipt correlator after running `--human`, a subagent found
`receipt_sha256` absent, read `core/access.py`, quoted the docstring explaining
that the field digests the in-memory gate rather than the written receipt, and
declined to invent a value. Correct behaviour, and a gap: the human summary
drops the identifier that ties several reports into one audit trail.

### 9. A shell turns `--last 15m` into one token when the value is unquoted

An unquoted variable expansion delivered `--last` one argument instead of two,
and the window parser refused it. The tool was right and the invocation was
wrong, but the error described the format rather than the likely cause.

### 10. A collector report needs the gate's own provenance

Collector reports named the authorities they used but not which declared
location selected them, so a per-project target and the user default were
indistinguishable in the evidence — while the documentation promised every
report names its own source.

## What to improve

Each item names the observation it comes from. Nothing here is scheduled; this
is the list a reader should start from.

**From 9 — say what the shell did.** Name the quoting trap in `--last` help and
in the manifest entry, and have the parse error suggest it when the value
contains whitespace.

**From 8 — do not drop the correlator.** Carry the gate correlator into the
human summary, or state in `SKILL.md` that correlating several runs requires
`--json`.

**From 6 — separate the code digest from the prose digest.** A typo in a
reference file should not invalidate live evidence. Weigh this carefully rather
than assuming it: `SKILL.md` changes agent behaviour, so documentation is not
obviously outside the executed surface.

**From 4 — the neutrality gate matches one project marker.** It would not catch
a home directory, a company domain or another project's hostname committed into
the skill tree. That surface is checked by hand today.

**From 1 and 2 — collapse the two setup steps.** One command that copies the
template to the user tier and opens it would remove the gap between "install
the skill" and "create your target". Copying a *template* is not provisioning a
credential, so this stays inside the resolve-never-provision rule.

**From 3 — assert the cold-run failure per command.** One command's failure
shape is covered. A row per command would catch an adapter that bypasses the
shared failure path.

**From 5 — one fixture can hide a tier test.** `tests/conftest.py` sets `HOME`
to the working directory, so under `run_script` the project and user tiers
resolve to the same path. A tier test written that way would be degenerate
without failing.

**From 7 — five deferred defects remain open.** `_auth_mechanism` rejecting
`token` with `tokenFile`; `collect_storage` keying controllers without kind;
`collect_events` ignoring `series.count`; credential containment not applied to
environment and default kubeconfig paths; `emit` filename uniqueness.

**From 6 — one host is not a fleet.** A receipt is per host and not
transferable, and only one execution host has produced one. A second host
should record its own.

## The one rule the rest of this follows

Declare, resolve, and report — in that order, and never search. A caller that
makes one call and reads the answer cannot be aimed at the wrong cluster by a
stale instruction, a forgotten export, or a lower tier quietly winning. Every
observation above is either that rule working or the cost of a place it had not
reached yet.
