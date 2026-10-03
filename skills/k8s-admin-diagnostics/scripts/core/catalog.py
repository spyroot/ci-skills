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

# Every API command accepts these. Node diagnostics share the target protocol.
UNIVERSAL_OPTIONS: dict[str, str] = {
    "--target": "nonsecret TOML naming the exact authorities; defaults to the per-host location",
    "--binding": "project-declared target and ordered kubeconfig source resolver",
    "--json": "versioned JSON document",
    "--yaml": "versioned YAML document",
    "--human": "human summary even when stdout is not a terminal",
    "--dry-run": "list planned probes without contacting any API; never access evidence",
    "--revision": "exact source commit of the installed copy, recorded as a claim",
    "--output-dir": "write paired JSON and text reports to this explicit path",
    "--describe": "this command's machine-readable contract, then exit",
    "--log-format": "diagnostic log format: text or JSON Lines (default: text)",
    "--log-level": "minimum diagnostic level: debug, info, warning, or error",
    "--log-file": "also append sanitized diagnostics to this exact path",
    "--run-id": "caller-selected identifier included in diagnostic logs",
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
GITHUB_CLOUD_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")
GITHUB_ENTERPRISE_VARIABLES = ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
GITHUB_TOKEN_VARIABLES = GITHUB_CLOUD_VARIABLES + GITHUB_ENTERPRISE_VARIABLES
GITLAB_VARIABLES = ("GITLAB_TOKEN", "GITLAB_ACCESS_TOKEN", "OAUTH_TOKEN")
GITHUB_DOTCOM_HOST = "github.com"
GITHUB_CLOUD_SUFFIX = ".ghe.com"


def github_variables(host: str) -> tuple[str, ...]:
    """Return the variables gh uses for GitHub Cloud or Enterprise Server."""
    return (
        GITHUB_CLOUD_VARIABLES
        if host == GITHUB_DOTCOM_HOST or host.endswith(GITHUB_CLOUD_SUFFIX)
        else GITHUB_ENTERPRISE_VARIABLES
    )


# The access protocol, declared once. FIRST MATCH WINS, and the match is
# reported back in `credential_sources`, so a caller resolves credentials by
# making one call and reading the answer -- never by searching the host in
# several places and guessing which one applied.
ACCESS_PROTOCOL: dict[str, Any] = {
    "rule": "first match wins; the resolved source is reported in credential_sources",
    "no_search": (
        "Do not hunt for credentials. Run the diagnostic you need: its access "
        "receipt names the effective source for each required authority. "
        "Use access_check.py when you need the full three-authority receipt."
    ),
    "github": [
        {
            "source": "target:github.token_file",
            "when": "the target declares a token file",
        },
        {
            "source": "env:" + " or ".join(GITHUB_CLOUD_VARIABLES),
            "when": f"set, and github.host is {GITHUB_DOTCOM_HOST} or a subdomain of ghe.com",
        },
        {
            "source": "env:" + " or ".join(GITHUB_ENTERPRISE_VARIABLES),
            "when": "set, and github.host is a GitHub Enterprise Server host",
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
        "when": "set; a missing file is an error, never a fallback",
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
            "When you need one live receipt proving GitHub, GitLab, and "
            "Kubernetes access on the execution host."
        ),
        "requires": ("github", "gitlab", "kubernetes"),
        "capabilities": (),
        "options": {
            "--publication": "also require repository administration and the declared required checks",
            "--job-url": "also prove access to one job, its pipeline, runner and trace",
            "--ceph-namespace": "also prove Ceph health, OSD, PG and Pod reads in the selected namespace",
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
    "gitlab_pipeline.py": {
        "kind": "gitlab_pipeline",
        "purpose": "Read one CI pipeline and bounded job progress by stage.",
        "use_when": "You need the status and progress of an exact GitLab pipeline.",
        "requires": ("gitlab",),
        "capabilities": (),
        "options": {
            "--project": "exact project path or numeric ID; otherwise use gitlab.project",
            "--pipeline-id": "numeric ID of the pipeline to read",
        },
        "required_options": ("--pipeline-id",),
        "returns": (
            "One validated pipeline with stage and job status counts, up to 500 "
            "job records, and a PARTIAL result when the job cap is exceeded."
        ),
    },
    "gitlab_access.py": {
        "kind": "gitlab_access",
        "purpose": "Resolve the effective GitLab credential and read back identity and exact target.",
        "use_when": "Before a GitLab operation; this route needs GitLab access only.",
        "requires": ("gitlab",),
        "capabilities": (),
        "options": {
            "--project": "select the exact project path or numeric ID",
            "--group": "select the exact group path or numeric ID",
            "--receipt-out": "write a sanitized, shareable access receipt",
        },
        "subcommands": {
            "check": {"purpose": "read back GitLab identity and selected target"},
        },
        "returns": "The effective source, identity, origin, and numeric project or group ID.",
    },
    "gitlab_milestone.py": {
        "kind": "gitlab_milestone",
        "purpose": "Create or update an exact GitLab milestone with independent read-back.",
        "use_when": "A selected project or group milestone needs a requested change.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--project": "exact project path or numeric ID",
            "--group": "exact group path or numeric ID",
            "--title": "milestone title for create or update",
            "--milestone-id": "numeric milestone ID for update or adjust-time",
            "--description-file": "file containing the requested description",
            "--start-date": "requested start date in YYYY-MM-DD form",
            "--due-date": "requested due date in YYYY-MM-DD form",
            "--state": "requested active or closed state",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            "--receipt-out": "write a sanitized, shareable operation receipt",
        },
        "subcommands": {
            "create": {"required_options": ["--title"]},
            "update": {"required_options": ["--milestone-id"]},
            "adjust-time": {"required_options": ["--milestone-id"]},
        },
        "returns": "A sanitized plan or verified milestone record and independent GET evidence.",
    },
    "gitlab_issue.py": {
        "kind": "gitlab_issue",
        "purpose": "Create or reuse an exact GitLab bug issue with independent read-back.",
        "use_when": "A selected project needs a requested bug issue.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--project": "exact project path or numeric ID",
            "--title": "bug title",
            "--description-file": "file containing the requested description",
            "--label": "label; repeat for multiple labels",
            "--milestone-id": "optional numeric milestone ID",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            "--receipt-out": "write a sanitized, shareable operation receipt",
        },
        "subcommands": {
            "open-bug": {"required_options": ["--title"]},
            "create-bug": {"required_options": ["--title"]},
        },
        "returns": "A sanitized plan or verified issue IID and independent GET evidence.",
    },
    "gitlab_wiki.py": {
        "kind": "gitlab_wiki",
        "purpose": "Create or update an exact GitLab wiki page with independent read-back.",
        "use_when": "A selected project wiki page needs requested content.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--project": "exact project path or numeric ID",
            "--title": "page title for create",
            "--slug": "exact existing page slug for update",
            "--content-file": "file containing the requested page content",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            "--receipt-out": "write a sanitized, shareable operation receipt",
        },
        "subcommands": {
            "create": {"required_options": ["--title", "--content-file"]},
            "update": {"required_options": ["--slug", "--content-file"]},
        },
        "returns": "A sanitized plan or verified wiki slug and independent GET evidence.",
    },
    "gitlab_runner.py": {
        "kind": "gitlab_runner",
        "purpose": "Assign an existing runner or create a runner record and verify it.",
        "use_when": "A selected project or group needs runner assignment or creation.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--project": "exact project path or numeric ID",
            "--group": "exact group path or numeric ID",
            "--runner-id": "numeric existing runner ID for assignment",
            "--runner-type": "project or group scope for creation",
            "--description": "runner description and server-visible recovery key",
            "--tag": "runner tag; repeat for multiple tags",
            "--token-out": "caller-selected 0600 file for the one-time runner token",
            "--live-plan": "read exact group project IDs and print an apply-ready plan without writes",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the live plan for group assignment, otherwise the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            "--receipt-out": "write a sanitized, shareable operation receipt",
        },
        "subcommands": {
            "assign": {"required_options": ["--runner-id"]},
            "create": {
                "required_options": ["--runner-type", "--description", "--token-out"]
            },
        },
        "returns": "A sanitized plan or verified runner record and assignment evidence.",
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
        "returns": "One record per agent with non-TTY health and actionable peer findings; UNKNOWN where exec could not run.",
    },
    "ceph_cluster.py": {
        "kind": "ceph_cluster",
        "purpose": "Read Ceph health, root/rack/host/OSD hierarchy, inactive PGs, and OSD/monitor Pods.",
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
        "returns": "Ceph status, nested OSD tree, inactive PGs, and filtered OSD/monitor Pod records.",
    },
    "k8s_verify_mtu_consistency.py": {
        "kind": "k8s_verify_mtu_consistency",
        "purpose": "Compare PCI Ethernet IPv4 uplink MTUs across selected Kubernetes nodes.",
        "use_when": (
            "An OpenShift Ceph or Cilium symptom may involve physical NIC MTUs; "
            "plan the selected nodes, then run the exact confirmed plan."
        ),
        "execution_surface": "OpenShift API and temporary oc debug Pods",
        "required_tools": ("kubectl", "oc", "ip on the selected node"),
        "requires": ("kubernetes",),
        "capabilities": ("node_scoped",),
        "mutates": True,
        "options": {
            "--dry-run": "read exact node inventory and return a plan without creating Pods",
            "--apply": "create temporary oc debug Pods for the confirmed plan",
            "--confirm-plan": "SHA256 digest returned by the dry-run plan",
            "--timeout": "per-node oc debug timeout in seconds",
            "--log-format": "text or JSON Lines diagnostics on stderr",
            "--log-level": "minimum diagnostic level",
            "--log-file": "optional private diagnostic log path",
            "--run-id": "correlation ID for logs and the temporary Pod marker",
        },
        "returns": (
            "A versioned table of node, PCI NIC, IPv4 address and MTU, "
            "consistency findings, and temporary-Pod cleanup read-back."
        ),
        "side_effects": "oc debug creates temporary Pods; apply verifies their cleanup",
    },
}

# Node diagnostics use existing selected Pods through the same API target.
NODE_LOCAL_OPTIONS: dict[str, str] = {
    "--target": "nonsecret TOML naming the exact authorities and node Pod routes",
    "--binding": "project binding for target and ordered kubeconfig sources",
    "--revision": "exact source commit of the installed copy, recorded as a claim",
    "--json": "versioned JSON document",
    "--yaml": "versioned YAML document",
    "--human": "human summary even when stdout is not a terminal",
    "--dry-run": "list API and existing-Pod probes without executing them",
    "--search": "case-insensitive text filter over returned records",
    "--describe": "this command's machine-readable contract, then exit",
}

NODE_LOCAL_COMMANDS: dict[str, dict[str, Any]] = {
    "cilium_node.py": {
        "kind": "cilium_node",
        "requires": ("kubernetes",),
        "purpose": "Read Cilium daemon status and health from an existing agent Pod on one node.",
        "use_when": "A selected node needs Cilium daemon and peer diagnosis.",
        "required_tools": ("kubectl",),
        "returns": "One Pod record with bounded daemon and health summaries plus findings, or a classified failure.",
    },
    "ceph_kernel.py": {
        "kind": "ceph_kernel",
        "requires": ("kubernetes",),
        "purpose": "Classify host Ceph/RBD kernel journal lines through an existing Pod.",
        "use_when": "A selected node has a Pod with a verified host journal mount.",
        "required_tools": ("kubectl", "journalctl in the selected Pod"),
        "options": {
            "--classification": "return only records with the selected Ceph kernel classification"
        },
        "returns": "Bounded UTC kernel records and read-only recommended action codes.",
    },
}

STATUS_MEANING = {
    "PASS": "every selected authority and live check passed",
    "PARTIAL": "the read completed and a component is unhealthy; see access_proven",
    "BLOCKED": "an authority or a required read failed; see blocking_live_checks",
    "DRY_RUN": "a probe plan only, never access evidence",
    "PLANNED": "a live read-only operation plan, with no resource mutation",
    "UNKNOWN": "a per-item reading could not be taken, preserved rather than coerced",
}

EXIT_CODES = {
    "0": "PASS, DRY_RUN, or a live read-only PLANNED result",
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
        "mutates": entry.get("mutates", False),
        "subcommands": entry.get("subcommands", {}),
        "side_effects": entry.get("side_effects", "none"),
        "execution_surface": entry.get("execution_surface", "selected authority API"),
        "required_tools": list(entry.get("required_tools", ())),
        "status_values": STATUS_MEANING,
        "exit_codes": EXIT_CODES,
        "default_output": "json when stdout is not a terminal, human when it is",
        "target_protocol": TARGET_PROTOCOL,
    }


def describe_node(script: str) -> dict[str, Any]:
    """Return the contract for a node read through an existing selected Pod."""
    entry = NODE_LOCAL_COMMANDS[script]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "command_contract",
        "skill": SKILL_NAME,
        "command": script,
        "report_kind": entry["kind"],
        "purpose": entry["purpose"],
        "use_when": entry["use_when"],
        "requires_authorities": list(entry["requires"]),
        "execution_surface": "Kubernetes API and one existing Pod on the selected node",
        "required_tools": list(entry["required_tools"]),
        "access_protocol": {name: ACCESS_PROTOCOL[name] for name in entry["requires"]},
        "capabilities": [],
        "required_options": [],
        "options": {**NODE_LOCAL_OPTIONS, **entry.get("options", {})},
        "returns": entry["returns"],
        "status_values": STATUS_MEANING,
        "exit_codes": EXIT_CODES,
        "default_output": "json when stdout is not a terminal, human when it is",
        "target_protocol": TARGET_PROTOCOL,
    }


def manifest() -> dict[str, Any]:
    """Return the whole-skill manifest rendered into `tools.json`."""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "skill_manifest",
        "skill": SKILL_NAME,
        "read_only": not any(
            entry.get("mutates", False) for entry in COMMANDS.values()
        ),
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
                    "read_only": not entry.get("mutates", False),
                    "subcommands": entry.get("subcommands", {}),
                    "side_effects": entry.get("side_effects", "none"),
                    "execution_surface": entry.get(
                        "execution_surface", "selected authority API"
                    ),
                    "required_tools": list(entry.get("required_tools", ())),
                }
                for script, entry in COMMANDS.items()
            },
            **{
                script: {
                    "report_kind": entry["kind"],
                    "purpose": entry["purpose"],
                    "use_when": entry["use_when"],
                    "requires_authorities": list(entry["requires"]),
                    "execution_surface": "Kubernetes API and one existing Pod on the selected node",
                    "required_tools": list(entry["required_tools"]),
                    "capabilities": [],
                    "required_options": [],
                    "options": sorted(
                        {**NODE_LOCAL_OPTIONS, **entry.get("options", {})}
                    ),
                    "returns": entry["returns"],
                }
                for script, entry in NODE_LOCAL_COMMANDS.items()
            },
        },
        "routing": {
            "prove all three authorities": "access_check.py",
            "a named CI job failed": "gitlab_job.py",
            "check GitLab pipeline progress": "gitlab_pipeline.py",
            "GitLab operation access": "gitlab_access.py",
            "create or change a GitLab milestone": "gitlab_milestone.py",
            "open a GitLab bug": "gitlab_issue.py",
            "create or change a GitLab wiki page": "gitlab_wiki.py",
            "assign or create a GitLab runner": "gitlab_runner.py",
            "a volume or claim is stuck": "storage_report.py",
            "what the cluster said during an interval": "event_trace.py",
            "connectivity or CNI health": "cilium_status.py",
            "Cilium daemon and health on a selected node": "cilium_node.py",
            "host Ceph or RBD kernel messages through an existing Pod": "ceph_kernel.py",
            "Ceph cluster health and OSD/monitor Pods": "ceph_cluster.py",
            "physical uplink MTU consistency across nodes": "k8s_verify_mtu_consistency.py",
        },
    }
