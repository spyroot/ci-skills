"""One declaration of what each command is for and which options it accepts.

An agent choosing a command should not have to read five `--help` texts in prose
and infer the interface. Two things make that unnecessary, and both come from
here:

* **Consistency.** API options are declared as CAPABILITY TIERS, not per command.
  Every API command accepts the universal tier, so `--json`, `--target`,
  `--dry-run`, `--revision`, `--output-dir` and `--describe` mean the same thing
  everywhere. A command that filters records accepts `--search`; one scoped to a
  namespace accepts `--namespace`; one reading a time range accepts `--last`,
  `--from` and `--to`. Learn the tier, not the command.
* **Machine routing.** `tools.json` is rendered from this module and
  `--describe` prints one command's entry, so a caller can pick a command and
  build its arguments without parsing help output.

This is the single source. `tools.json`, every `--help`, every `--describe` and
the consistency test all read it, so a drift between the declared interface and
the real one is a test failure rather than a surprise at runtime.
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = "1.0"
SKILL_NAME = "k8s-admin-diagnostics"

# Every API command accepts these. Node-local commands have no API target.
UNIVERSAL_OPTIONS: dict[str, str] = {
    "--target": "nonsecret TOML naming the exact authorities; defaults to the per-host location",
    "--binding": "project-declared target and ordered kubeconfig source resolver",
    "--json": "versioned JSON document",
    "--yaml": "versioned YAML document",
    "--human": "human summary even when stdout is not a terminal",
    "--dry-run": "list planned probes without contacting any API; never access evidence",
    "--revision": "exact source commit of the installed copy, recorded as a claim",
    "--output-dir": "write paired JSON and text reports; nothing is persisted without it",
    "--describe": "this command's machine-readable contract, then exit",
}

# Capability tiers. A command declaring a capability accepts all of its options.
CAPABILITY_OPTIONS: dict[str, dict[str, str]] = {
    "filters_records": {
        "--search": "case-insensitive text filter over the returned records",
    },
    "namespaced": {
        "--namespace": "one namespace, or `all`; `auto` where the command discovers it",
    },
    "node_scoped": {
        "--node": "restrict to one node name",
    },
    "time_ranged": {
        "--last": "relative window ending now: 5m, 90s, 2h, 7d",
        "--from": "inclusive RFC3339 start",
        "--to": "inclusive RFC3339 end",
    },
}

AUTHORITIES = ("github", "gitlab", "kubernetes")

# The token variables each client honours, most preferred first. Declared here
# because three places need the same answer -- source binding, the unbound
# single-authority path, and the published protocol -- and they disagreed: the
# protocol named GH_TOKEN for an Enterprise host while the code read only
# GH_ENTERPRISE_TOKEN there, so a caller could set exactly the variable the
# manifest named and still be told the credential store was used.
GITHUB_DOTCOM_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")
GITHUB_ENTERPRISE_VARIABLES = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
GITLAB_VARIABLES = ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN")
GITHUB_DOTCOM_HOST = "github.com"


def github_variables(host: str) -> tuple[str, ...]:
    """Return the token variables that apply to one GitHub host."""
    return (
        GITHUB_DOTCOM_VARIABLES
        if host == GITHUB_DOTCOM_HOST
        else GITHUB_ENTERPRISE_VARIABLES
    )


# The access protocol, declared once. FIRST MATCH WINS, and the match is
# reported back in `credential_sources`, so a caller resolves credentials by
# making one call and reading the answer -- never by searching the host in
# several places and guessing which one applied.
ACCESS_PROTOCOL: dict[str, Any] = {
    "rule": "first match wins; the resolved source is reported in credential_sources",
    "no_search": (
        "Do not hunt for credentials. Run access_check.py once: if it passes, the "
        "report names the effective source for each authority and you are done. "
        "If it blocks, the surface reason names what to fix."
    ),
    "github": [
        {
            "source": "target:github.token_file",
            "when": "the target declares a token file",
        },
        {
            "source": "env:" + " or ".join(GITHUB_DOTCOM_VARIABLES),
            "when": f"set, and github.host is {GITHUB_DOTCOM_HOST}",
        },
        {
            "source": "env:" + " or ".join(GITHUB_ENTERPRISE_VARIABLES),
            "when": f"set, and github.host is anything other than {GITHUB_DOTCOM_HOST}",
        },
        {
            "source": "gh-credential-store:<host>",
            "when": "gh holds a credential for the exact host",
        },
    ],
    "gitlab": [
        {
            "source": "target:gitlab.token_file",
            "when": "the target declares a token file",
        },
        {
            "source": "env:"
            + ", ".join(GITLAB_VARIABLES[:-1])
            + f" or {GITLAB_VARIABLES[-1]}",
            "when": "set",
        },
        {
            "source": "glab-credential-store:<host>",
            "when": "glab holds a credential for the exact FQDN",
        },
    ],
    "kubernetes": [
        {
            "source": "target:kubernetes.kubeconfigs",
            "when": "the target declares kubectl's ordered, combined path",
        },
        {
            "source": "target:kubernetes.kubeconfig",
            "when": "the target declares one file",
        },
        {
            "source": "binding:kubernetes.sources",
            "when": "a project binding selects a declared file, environment, or command source",
        },
        {"source": "env:KUBECONFIG", "when": "set; its path order is honoured"},
        {"source": "kubectl-default:~/.kube/config", "when": "nothing above applies"},
    ],
    "warning": (
        "The default kubeconfig may name another cluster. The selected context "
        "and API server are always verified before collection."
    ),
}

# Where the target file comes from. Same shape as the credential chains: an
# ordered list, first match wins, and the winner is reported back as
# `target_file` and `target_source`. Declared here so a caller reads the
# protocol instead of guessing a location, and so provisioning stays the
# calling project's business rather than something this skill hardcodes.
TARGET_PROTOCOL: list[dict[str, str]] = [
    {
        "source": "argv:--target",
        "location": "the path given",
        "scope": "one command",
        "when": "passed; a missing file is an error, never a fallback",
    },
    {
        "source": "env:CI_SKILLS_TARGET",
        "location": "$CI_SKILLS_TARGET",
        "scope": "one environment",
        "when": "set and the file exists",
    },
    {
        "source": "project",
        "location": "./.ci-skills/target.toml",
        "scope": "one project",
        "when": "present in the working directory",
    },
    {
        "source": "user",
        "location": "~/.ci-skills/target.toml",
        "scope": "one user",
        "when": "present; the documented default",
    },
]

# Deliberately four places, in the shape agent tooling already uses -- a
# project `./.ci-skills/` beside a user `~/.ci-skills/`, exactly as `.claude`
# and `.codex` are laid out. Adding XDG and `~/.config` variants on top would
# recreate the problem this protocol exists to remove: a caller searching
# several locations and guessing which one applied.
TARGET_FILENAME = "target.toml"
PROJECT_DIR = ".ci-skills"

COMMANDS: dict[str, dict[str, Any]] = {
    "access_check.py": {
        "kind": "access_check",
        "purpose": "Prove access to every selected authority and emit one receipt.",
        "use_when": (
            "Before trusting any other command, and whenever you need evidence "
            "that a host really had the access it claims."
        ),
        "requires": ("github", "gitlab", "kubernetes"),
        "capabilities": (),
        "options": {
            "--publication": "also require repository administration and the declared required checks",
            "--job-url": "also prove access to one job, its pipeline, runner and trace",
            "--receipt-out": "write the committable receipt, with host paths digested",
        },
        "returns": "A receipt: credential sources, identities, targets, skill digest, and one entry per live check.",
    },
    "gitlab_job.py": {
        "kind": "gitlab_job",
        "purpose": "Read one CI job with its pipeline, runner and bounded trace.",
        "use_when": "A named job failed and you need its own facts, not the cluster's.",
        "requires": ("gitlab",),
        "capabilities": ("filters_records",),
        "options": {"--job-url": "full HTTPS URL of one job on the selected host"},
        "required_options": ("--job-url",),
        "returns": "One record: job, pipeline, runner, and the last 200 trace lines, sanitized.",
    },
    "storage_report.py": {
        "kind": "storage_report",
        "purpose": "Correlate claims, volumes, attachments, pods and controllers.",
        "use_when": "A workload will not start or a volume looks stuck, and you need the chain.",
        "requires": ("kubernetes",),
        "capabilities": ("filters_records", "namespaced", "node_scoped"),
        "options": {
            "--storage-class": "restrict to one StorageClass",
            "--phase": "claim phase: Pending, Bound, Lost, Released, Failed, or all",
        },
        "returns": "One record per claim or standalone volume, with its pods, attachments and controllers.",
    },
    "event_trace.py": {
        "kind": "event_trace",
        "purpose": "Return a time-ordered event trace from both event APIs.",
        "use_when": (
            "You need what the cluster said during an interval. Pass the "
            "interval; a zero count with zero errors means the events aged out, "
            "not that the read failed."
        ),
        "requires": ("kubernetes",),
        "capabilities": ("filters_records", "namespaced", "time_ranged"),
        "options": {
            "--kind": "involved object kind",
            "--object": "involved object name",
            "--reason": "case-insensitive event reason",
        },
        "returns": "One record per event, deduplicated across both APIs, oldest first.",
    },
    "cilium_status.py": {
        "kind": "cilium_status",
        "purpose": "Aggregate Cilium agent, operator and node health by real exec.",
        "use_when": "Connectivity is suspect, or a node's CNI may differ from its Ready condition.",
        "requires": ("kubernetes",),
        "capabilities": ("filters_records", "namespaced", "node_scoped"),
        "options": {},
        "returns": "One record per agent, with health from a non-TTY exec; UNKNOWN where it could not run.",
    },
    "ceph_cluster.py": {
        "kind": "ceph_cluster",
        "purpose": "Read Ceph health, OSDs, inactive PGs, and OSD/monitor Pods.",
        "use_when": "A selected Rook Ceph cluster needs API-backed diagnostics.",
        "requires": ("kubernetes",),
        "capabilities": ("namespaced", "node_scoped"),
        "options": {
            "--operator": "operator deployment name",
            "--conf": "Ceph config path inside the operator Pod",
            "--ready": "Pod Ready filter: all, true, or false",
            "--condition": "Pod condition filter: TYPE=STATUS",
        },
        "required_options": ("--namespace",),
        "returns": "Ceph status, OSD tree, inactive PGs, and filtered OSD/monitor Pod records.",
    },
}

# Node-local reads require a selected Linux execution host, not the API target
# or its credential chain. Keep their smaller interface explicit in the same
# manifest so agents can discover them without guessing from prose.
NODE_LOCAL_OPTIONS: dict[str, str] = {
    "--json": "versioned JSON document",
    "--yaml": "versioned YAML document",
    "--human": "human summary even when stdout is not a terminal",
    "--dry-run": "list local commands without executing them; never live evidence",
    "--describe": "this command's machine-readable contract, then exit",
}

NODE_LOCAL_COMMANDS: dict[str, dict[str, Any]] = {
    "cilium_node.py": {
        "kind": "cilium_node",
        "purpose": "Read the local Cilium agent container, daemon status and health.",
        "use_when": "A selected Linux node needs CRI-level Cilium diagnosis.",
        "required_tools": ("sudo", "crictl"),
        "returns": "One local agent record with daemon and health JSON, or a classified failure.",
    },
    "ceph_kernel.py": {
        "kind": "ceph_kernel",
        "purpose": "Classify recent local Ceph and RBD kernel journal messages.",
        "use_when": "A selected Linux node shows storage or RBD symptoms.",
        "required_tools": ("sudo", "journalctl"),
        "returns": "Bounded UTC kernel records and read-only recommended action codes.",
    },
}

STATUS_MEANING = {
    "PASS": "every selected authority and live check passed",
    "PARTIAL": "the read completed and a component is unhealthy; see access_proven",
    "BLOCKED": "an authority or a required read failed; see blocking_live_checks",
    "DRY_RUN": "a probe plan only, never access evidence",
    "UNKNOWN": "a per-item reading could not be taken, preserved rather than coerced",
}

EXIT_CODES = {
    "0": "PASS or an explicitly marked DRY_RUN",
    "2": "BLOCKED or PARTIAL, or an input that could not be used",
}


# One command's identity is its report kind, not the name it was invoked under.
COMMAND_BY_KIND: dict[str, str] = {
    entry["kind"]: script for script, entry in COMMANDS.items()
}


def missing_required_options(script: str, args: Any) -> list[str]:
    """Return the declared required options this invocation did not supply.

    `required_options` is published in `tools.json`, so it has to be the thing
    that is actually enforced. argparse cannot do it: `--describe` must answer
    with no other argument. Enforcing it from the declaration keeps one rule.
    """
    missing = []
    for option in COMMANDS[script].get("required_options", ()):
        destination = option.removeprefix("--").replace("-", "_")
        if not getattr(args, destination, None):
            missing.append(option)
    return missing


def options_for(script: str) -> dict[str, str]:
    """Return every option one command accepts, tier options included."""
    entry = COMMANDS[script]
    merged = dict(UNIVERSAL_OPTIONS)
    for capability in entry["capabilities"]:
        merged.update(CAPABILITY_OPTIONS[capability])
    merged.update(entry["options"])
    return merged


def describe(script: str) -> dict[str, Any]:
    """Return one command's contract, for `--describe`."""
    entry = COMMANDS[script]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "command_contract",
        "skill": SKILL_NAME,
        "command": script,
        "report_kind": entry["kind"],
        "purpose": entry["purpose"],
        "use_when": entry["use_when"],
        "requires_authorities": list(entry["requires"]),
        "access_protocol": {name: ACCESS_PROTOCOL[name] for name in entry["requires"]},
        "capabilities": list(entry["capabilities"]),
        "required_options": list(entry.get("required_options", ())),
        "options": options_for(script),
        "returns": entry["returns"],
        "status_values": STATUS_MEANING,
        "exit_codes": EXIT_CODES,
        "default_output": "json when stdout is not a terminal, human when it is",
        "target_protocol": TARGET_PROTOCOL,
    }


def describe_node(script: str) -> dict[str, Any]:
    """Return the contract for one command executed on a selected Linux node."""
    entry = NODE_LOCAL_COMMANDS[script]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "command_contract",
        "skill": SKILL_NAME,
        "command": script,
        "report_kind": entry["kind"],
        "purpose": entry["purpose"],
        "use_when": entry["use_when"],
        "requires_authorities": [],
        "execution_surface": "selected Linux node",
        "required_tools": list(entry["required_tools"]),
        "access_protocol": {},
        "capabilities": [],
        "required_options": [],
        "options": NODE_LOCAL_OPTIONS,
        "returns": entry["returns"],
        "status_values": STATUS_MEANING,
        "exit_codes": EXIT_CODES,
        "default_output": "json when stdout is not a terminal, human when it is",
        "target_protocol": None,
    }


def manifest() -> dict[str, Any]:
    """Return the whole-skill manifest rendered into `tools.json`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "skill_manifest",
        "skill": SKILL_NAME,
        "read_only": True,
        "default_output": "json when stdout is not a terminal, human when it is",
        "universal_options": UNIVERSAL_OPTIONS,
        "node_local_options": NODE_LOCAL_OPTIONS,
        "capability_options": CAPABILITY_OPTIONS,
        "authorities": list(AUTHORITIES),
        "access_protocol": ACCESS_PROTOCOL,
        "target_protocol": TARGET_PROTOCOL,
        "status_values": STATUS_MEANING,
        "exit_codes": EXIT_CODES,
        "commands": {
            **{
                script: {
                    "report_kind": entry["kind"],
                    "purpose": entry["purpose"],
                    "use_when": entry["use_when"],
                    "requires_authorities": list(entry["requires"]),
                    "capabilities": list(entry["capabilities"]),
                    "required_options": list(entry.get("required_options", ())),
                    "options": sorted(options_for(script)),
                    "returns": entry["returns"],
                }
                for script, entry in COMMANDS.items()
            },
            **{
                script: {
                    "report_kind": entry["kind"],
                    "purpose": entry["purpose"],
                    "use_when": entry["use_when"],
                    "requires_authorities": [],
                    "execution_surface": "selected Linux node",
                    "required_tools": list(entry["required_tools"]),
                    "capabilities": [],
                    "required_options": [],
                    "options": sorted(NODE_LOCAL_OPTIONS),
                    "returns": entry["returns"],
                }
                for script, entry in NODE_LOCAL_COMMANDS.items()
            },
        },
        "routing": {
            "prove access first": "access_check.py",
            "a named CI job failed": "gitlab_job.py",
            "a volume or claim is stuck": "storage_report.py",
            "what the cluster said during an interval": "event_trace.py",
            "connectivity or CNI health": "cilium_status.py",
            "local Cilium CRI health on a selected node": "cilium_node.py",
            "local Ceph or RBD kernel messages": "ceph_kernel.py",
            "Ceph cluster health and OSD/monitor Pods": "ceph_cluster.py",
        },
    }
