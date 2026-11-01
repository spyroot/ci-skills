"""One declaration of what each command is for and which options it accepts.

An agent choosing a command should not have to read five `--help` texts in prose
and infer the interface. Two things make that unnecessary, and both come from
here:

* **Consistency.** API options are declared as CAPABILITY TIERS, not per command.
  Every API command accepts the universal tier, so `--json`,
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

import re
from argparse import Namespace
from collections.abc import Mapping, MutableMapping, Sequence
from types import MappingProxyType
from typing import Final, Optional, Union

from lib.python.core.action_modes import (
    GITLAB_MODE_POLICY,
    GITLAB_RUNNER_READ_OPERATIONS,
    TOOL_POD_MODE_POLICY,
    ActionMode,
    ActionModePolicy,
)
from lib.python.core.catalog_types import (
    CommandDefinition,
    DiscoveryDefinition,
    NodeDefinition,
    ReadDefinition,
)
from lib.python.core.endpoints import TARGET_CONTRACT
from lib.python.core.json_types import (
    JSONValue,
    copy_json_object,
    json_object,
    json_strings,
)

SCHEMA_VERSION = "1.0"
SKILL_NAME = "ci-skills"
GITLAB_ACTION_SCHEMA: Final = "schemas/results/gitlab-action.schema.json"
GITLAB_PROFILE_OPTION: Final = "--gitlab-profile"
GITLAB_PROFILE_HELP: Final = (
    "select one complete [gitlab.NAME] profile; omission uses [gitlab]"
)

# Every API command accepts these. Node diagnostics share the target protocol.
UNIVERSAL_OPTIONS: dict[str, str] = {
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


def persistence_options(
    policy: ActionModePolicy,
    receipt: str,
    *,
    read_operations: Sequence[str] = (),
    live_plan: bool = False,
) -> dict[str, str]:
    """Describe existing persistence rules for a command's reachable modes.

    :param policy: The same policy used by the command's mode validator.
    :param receipt: Existing description of the command's portable receipt.
    :param read_operations: Operations that run in read mode without mode flags.
    :param live_plan: Whether the command exposes a live-plan option.
    :returns: Receipt, report and log descriptions for help and discovery.
    """
    modes = {
        ActionMode.OFFLINE: "offline (--dry-run)",
        ActionMode.APPLY: "confirmed apply (--apply --confirm-plan SHA256)",
    }
    if live_plan:
        modes[ActionMode.LIVE_PLAN] = "live-plan (--live-plan)"
    if read_operations:
        modes[ActionMode.READ] = (
            f"read ({'/'.join(read_operations)} without mode flags)"
        )
    receipts = [label for mode, label in modes.items() if mode in policy.receipt_modes]
    forbidden = [
        mode.value
        for mode in (ActionMode.OFFLINE, ActionMode.LIVE_PLAN)
        if mode not in policy.receipt_modes
    ]
    receipt_rule = f"Allowed in {'; '.join(receipts)}."
    if forbidden:
        receipt_rule += f" Not allowed in {' or '.join(forbidden)} mode."
    outputs = [
        label
        for mode, label in modes.items()
        if mode != ActionMode.OFFLINE or policy.allow_offline_output
    ]
    output_rule = f"Allowed in {'; '.join(outputs)}."
    if not policy.allow_offline_output:
        default = (
            " (the default for changes)"
            if policy.default_mode == ActionMode.OFFLINE
            else ""
        )
        output_rule += f" Not allowed in offline mode{default}."
    return {
        "--receipt-out": f"{receipt}. {receipt_rule}",
        "--output-dir": f"{UNIVERSAL_OPTIONS['--output-dir']}. {output_rule}",
        "--log-file": f"{UNIVERSAL_OPTIONS['--log-file']}. {output_rule}",
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

# The authorities a target file declares; `core/endpoints.py` owns the list.
AUTHORITIES = TARGET_CONTRACT.authorities
DEFAULT_AUTHORITIES: Final = ("github", "gitlab", "kubernetes")

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
    """Select credential variable names for the GitHub host.

    :param host: GitHub Cloud or Enterprise Server hostname.
    :returns: Credential variable names checked for that host.
    """
    return (
        GITHUB_CLOUD_VARIABLES
        if host == GITHUB_DOTCOM_HOST or host.endswith(GITHUB_CLOUD_SUFFIX)
        else GITHUB_ENTERPRISE_VARIABLES
    )


# The access protocol, declared once. FIRST MATCH WINS, and the match is
# reported back in `credential_sources`, so a caller resolves credentials by
# making one call and reading the answer -- never by searching the host in
# several places and guessing which one applied.
ACCESS_PROTOCOL: Mapping[str, Union[str, Sequence[Mapping[str, str]]]] = {
    "rule": "first match wins; the resolved source is reported in credential_sources",
    "no_search": (
        "Do not hunt for credentials. Run the diagnostic you need: its access "
        "receipt names the effective source for each required authority. "
        "Use access_check.py to check the configured GitHub, GitLab and Kubernetes authorities."
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
    "harbor": [
        {
            "source": "target:harbor.credential_file",
            "when": "combined native credential file is selected",
        },
        {
            "source": "target:harbor.username_file + harbor.password_file",
            "when": "paired existing files are selected",
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
        "source": "env:CI_SKILLS_TARGET",
        "location": "$CI_SKILLS_TARGET",
        "scope": "one environment",
        "when": "set; a missing file is an error, never a fallback",
    },
    {
        "source": "project",
        "location": "./.ci-skills/target.toml",
        "scope": "one project",
        "when": "present in the working directory; ci-skills-smoke selects ci-skills-smoke.toml instead",
    },
    {
        "source": "user",
        "location": "~/.ci-skills/target.toml",
        "scope": "one user",
        "when": "present; ci-skills-smoke selects ci-skills-smoke.toml instead, without normal-target fallback",
    },
]

# Deliberately three places, in the shape agent tooling already uses -- a
# project `./.ci-skills/` beside a user `~/.ci-skills/`, exactly as `.claude`
# and `.codex` are laid out. Adding XDG and `~/.config` variants on top would
# recreate the problem this protocol exists to remove: a caller searching
# several locations and guessing which one applied.
TARGET_FILENAME = "target.toml"
PROJECT_DIR = ".ci-skills"

COMMANDS: Mapping[str, CommandDefinition] = {
    "create_selfhosted_runner.py": {
        "kind": "create_selfhosted_runner",
        "purpose": "Build, publish, deploy and register a toolbox-backed GitHub runner.",
        "use_when": "Create, update or delete a GitHub runner using the configured cluster and "
        "Harbor.",
        "requires": ("github", "kubernetes", "harbor"),
        "capabilities": ("namespaced",),
        "mutates": True,
        "options": {
            "--spec": "toolbox JSON following schemas/toolbox.schema.json",
            "--name": "exact runner name",
            "--runner-root": "runner runtime directory inside the toolbox image",
            "--label": "GitHub runner label; repeatable",
            "--namespace": "runner namespace; defaults to the selected toolbox build "
            "namespace",
            "--apply": "perform the confirmed operation",
            "--confirm-plan": "exact fingerprint from the preceding plan",
            "--timeout": "operation timeout in seconds",
            "--receipt-out": "write a sanitized operation receipt",
        },
        "repeatable_options": ("--label",),
        "subcommands": {
            "create": {
                "required_options": ["--spec", "--name", "--runner-root", "--label"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
            "update": {
                "required_options": ["--spec", "--name", "--runner-root", "--label"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
            "delete": {
                "required_options": ["--namespace", "--name"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
        },
        "related": ("toolbox.py", "harbor_push.py", "harbor_pull.py"),
        "required_tools": ("gh", "kubectl", "oc", "skopeo"),
        "returns": "Versioned operation plan or independently verified callback results, with "
        "cleanup evidence.",
        "execution_surface": "installed skill and configured provider APIs; GitHub Actions runs "
        "tests and lint only",
        "side_effects": "Only confirmed operations mutate selected resources; temporary helpers "
        "are cleaned on exit.",
    },
    "ocp_node_onboard.py": {
        "kind": "ocp_node_onboard",
        "purpose": "Build an OpenShift node ISO in-cluster, join its node, and verify selected storage.",
        "use_when": "Prepare a node boot image from the caller's native MAC or YAML input; "
        "join its booted node, inspect it or release a previous run. "
        "See references/ocp-node-image.md.",
        "requires": ("kubernetes",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--spec": "native nodes-config.yaml passed unchanged; alternative to --mac-address",
            "--mac-address": "joining host's actual NIC MAC; alternative to --spec",
            "--network-config-path": "native network YAML used with the MAC route, passed unchanged",
            "--destination": "five-field cluster storage JSON; schemas/artifact-destination.schema.json",
            "--tool-pod-spec": "optional CI12.0 tool override; schemas/tool-pod.schema.json",
            "--node-name": "exact Kubernetes Node name selected for join",
            "--ip-address": "joining node address for native monitor; repeatable",
            "--pull-secret-namespace": "existing registry Secret namespace; default openshift-config",
            "--pull-secret-name": "existing registry Secret name; default pull-secret",
            "--pull-secret-key": "existing registry Secret data key; default .dockerconfigjson",
            "--run-id": "returned build run identifier for join, verification or removal",
            "--timeout": "positive operation timeout in seconds (default: 1800)",
            "--live-plan": "resolve live prerequisites and produce the confirmation digest",
            "--apply": "perform the exact confirmed build, join or removal",
            "--confirm-plan": "plan digest binding target, operation inputs and functional code",
            "--receipt-out": "write the sanitized result to this exact path",
        },
        "subcommands": {
            "build": {
                "purpose": "Build from either a MAC or native YAML and verify the exact cluster destination.",
                "required_options": ["--destination"],
                "mutates": True,
                "side_effects": "Confirmed build stages a tool Pod and native OpenShift temporary resources, "
                "writes the requested ISO, and retains its run record; temporary final storage retains its Pod.",
                "read_back": "Native completion, embedded Ignition, actual ISO size/SHA-256 and destination.",
                "result_schema": "schemas/results/ocp-node-onboard.schema.json",
            },
            "verify": {
                "purpose": "Read and inspect the existing cluster-held ISO identified by its run.",
                "required_options": ["--run-id"],
                "mutates": True,
                "side_effects": "Persistent storage can require a temporary reader Pod, cleaned after inspection.",
                "read_back": "Exact stored artifact identity, size/SHA-256 and embedded Ignition.",
                "result_schema": "schemas/results/ocp-node-onboard.schema.json",
            },
            "join": {
                "purpose": "Approve the booted node's selected CSRs and verify its Ready identity.",
                "required_options": ["--run-id", "--node-name", "--ip-address"],
                "mutates": True,
                "side_effects": "Confirmed join approves only the exact hostname's client and serving "
                "signers, then removes the owned build scope; it never boots or deletes a node.",
                "read_back": "Client bootstrap-completion evidence, issued serving request state and exact Ready "
                "Node hostname, roles and observed IPs.",
                "result_schema": "schemas/results/ocp-node-onboard.schema.json",
            },
            "remove": {
                "purpose": "Remove owned run resources while preserving persistent destination storage and ISO.",
                "required_options": ["--run-id"],
                "mutates": True,
                "side_effects": "Confirmed removal deletes only run-owned resources; temporary output ends "
                "with its Pod. Persistent ISO and caller storage remain.",
                "read_back": "Owned resource absence and, for persistent output, unchanged ISO before and after removal.",
                "result_schema": "schemas/results/ocp-node-onboard.schema.json",
            },
        },
        "related": ("prepare_tool_pod.py", "storage_report.py"),
        "references": ("Native OCP YAML example", "Native OCP command flags"),
        "required_tools": ("kubectl", "oc", "curl (default tool discovery)"),
        "returns": "Shared workflow report with native completion, join read-back, final cluster artifact "
        "identity/lifetime, plan, provenance and cleanup evidence.",
        "execution_surface": "installed skill controls tools, ISO generation and node join in the selected cluster",
        "side_effects": "Build/join/remove use plan confirmation. Native oc creates temporary build resources; "
        "join approves only the selected node's two signers. ISO bytes stay in the cluster.",
        "description": "Build requires --destination and exactly one of --mac-address or --spec. "
        "The caller selects real NIC/network/disk values; ci-skills passes native YAML unchanged to OpenShift "
        "and returns native errors. Tools are prepared inside the Pod through CI12.0. "
        "The destination wrapper selects storage and an exact final path; it does not define an OCP schema. "
        "Native input and destination examples: references/ocp-node-image.md.",
        "examples": (
            {
                "description": "Plan a MAC-based build at the selected cluster destination",
                "command": "ocp_node_onboard.py build --mac-address 02:00:00:10:00:01 "
                "--destination destination.json --live-plan --json",
            },
            {
                "description": "Plan a build from the caller's unchanged native YAML",
                "command": "ocp_node_onboard.py build --spec nodes-config.yaml "
                "--destination destination.json --live-plan --json",
            },
            {
                "description": "Build from the same inputs after inspecting the plan",
                "command": "ocp_node_onboard.py build --spec nodes-config.yaml "
                "--destination destination.json --apply --confirm-plan DIGEST --json",
            },
            {
                "description": "Inspect a previous run's cluster-held artifact",
                "command": "ocp_node_onboard.py verify --run-id RUN_ID --json",
            },
            {
                "description": "Plan the selected booted node's exact join sequence",
                "command": "ocp_node_onboard.py join --run-id RUN_ID --node-name worker-01 "
                "--ip-address 192.0.2.10 --live-plan --json",
            },
            {
                "description": "Plan removal while preserving persistent output",
                "command": "ocp_node_onboard.py remove --run-id RUN_ID --live-plan --json",
            },
        ),
    },
    "prepare_tool_pod.py": {
        "kind": "tool_pod",
        "purpose": "Prepare, reuse or remove a cluster Pod with declared tools.",
        "use_when": "Run tooling in a retained cluster Pod for another callback or command.",
        "requires": ("kubernetes",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--spec": "JSON with image and dependencies; schemas/tool-pod.schema.json",
            "--run-id": "returned run identifier for reuse/removal, also used in logs",
            "--live-plan": "verify Kubernetes access without creating resources",
            "--apply": "perform the exact confirmed operation",
            "--confirm-plan": "plan digest binding target, inputs, run ID and functional code",
            "--timeout": "positive operation timeout in seconds (default: 300)",
            **persistence_options(
                TOOL_POD_MODE_POLICY,
                "write a sanitized operation receipt",
                live_plan=True,
            ),
        },
        "subcommands": {
            "prepare": {
                "required_options": ["--spec"],
                "result_schema": "schemas/results/tool-pod.schema.json",
            },
            "remove": {
                "required_options": ["--run-id"],
                "result_schema": "schemas/results/tool-pod.schema.json",
            },
        },
        "related": ("toolbox.py",),
        "references": ("Tool Pod preparation",),
        "required_tools": ("kubectl",),
        "returns": "Plan or actual Pod identity, tool observations, per-callback results and cleanup.",
        "execution_surface": "installed skill controls a Pod in the selected Kubernetes cluster",
        "side_effects": "Confirmed prepare creates and retains run-owned resources; remove deletes "
        "only those resources. Failure removes resources created by the failed run.",
        "description": "Supply image and dependencies in the input JSON. Each dependency uses the "
        "existing toolchain schema (name, commands, optional probe and installation route). "
        "Tools are installed inside the Pod, never on this computer. See references/tool-pod.md.",
        "examples": (
            {
                "description": "Plan without provider calls",
                "command": "prepare_tool_pod.py prepare --spec tools.json --dry-run --json",
            },
            {
                "description": "Prepare the confirmed tools",
                "command": "prepare_tool_pod.py prepare --spec tools.json --apply --confirm-plan DIGEST --json",
            },
            {
                "description": "Reuse and re-probe the same Pod",
                "command": "prepare_tool_pod.py prepare --spec tools.json --run-id RUN_ID --dry-run --json",
            },
            {
                "description": "Plan removal of owned resources",
                "command": "prepare_tool_pod.py remove --run-id RUN_ID --dry-run --json",
            },
        ),
    },
    "toolbox.py": {
        "kind": "toolbox",
        "purpose": "Build or delete a toolbox using the configured OpenShift and Harbor.",
        "use_when": "Build one validated toolbox specification independently of a runner.",
        "requires": ("kubernetes", "harbor"),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--spec": "toolbox JSON following schemas/toolbox.schema.json",
            "--apply": "perform the confirmed operation",
            "--confirm-plan": "exact fingerprint from the preceding plan",
            "--timeout": "operation timeout in seconds",
            "--receipt-out": "write a sanitized operation receipt",
        },
        "subcommands": {
            "create": {
                "required_options": ["--spec"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
            "update": {
                "required_options": ["--spec"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
            "delete": {
                "required_options": ["--spec"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            },
        },
        "related": ("harbor_push.py", "harbor_pull.py", "create_selfhosted_runner.py"),
        "references": (
            "Harbor API shapes",
            "Podman inside a Pod",
            "Harbor authentication",
            "Harbor Helm OCI",
        ),
        "required_tools": ("oc", "kubectl", "skopeo"),
        "returns": "Versioned operation plan or independently verified callback results, with "
        "cleanup evidence.",
        "execution_surface": "installed skill and configured provider APIs; GitHub Actions runs "
        "tests and lint only",
        "side_effects": "Only confirmed operations mutate selected resources; temporary helpers "
        "are cleaned on exit.",
    },
    "harbor_push.py": {
        "kind": "harbor_push",
        "purpose": "Copy a selected image to Harbor and verify the immutable digest.",
        "use_when": "Publish an existing image without building or creating a runner.",
        "requires": ("harbor",),
        "capabilities": (),
        "mutates": True,
        "options": {
            "--source-image": "exact source registry image",
            "--repository": "destination repository within the configured Harbor "
            "project",
            "--tag": "destination tag",
            "--apply": "perform the confirmed operation",
            "--confirm-plan": "exact fingerprint from the preceding plan",
            "--timeout": "operation timeout in seconds",
            "--receipt-out": "write a sanitized operation receipt",
        },
        "subcommands": {
            "push": {
                "required_options": ["--source-image", "--repository", "--tag"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            }
        },
        "related": ("toolbox.py", "harbor_pull.py"),
        "references": (
            "Harbor API shapes",
            "Harbor authentication",
            "Harbor Helm OCI",
        ),
        "required_tools": ("skopeo",),
        "returns": "Versioned operation plan or independently verified callback results, with "
        "cleanup evidence.",
        "execution_surface": "installed skill and configured provider APIs; GitHub Actions runs "
        "tests and lint only",
        "side_effects": "Only confirmed operations mutate selected resources; temporary helpers "
        "are cleaned on exit.",
    },
    "harbor_pull.py": {
        "kind": "harbor_pull",
        "purpose": "Pull a Harbor toolbox in Kubernetes and execute its doctor.",
        "use_when": "Prove a published toolbox image works, with temporary Job cleanup.",
        "requires": ("kubernetes", "harbor"),
        "capabilities": ("namespaced",),
        "mutates": True,
        "options": {
            "--image": "digest-pinned image at the configured Harbor",
            "--namespace": "namespace for the temporary pull verification Job",
            "--name": "operation name for temporary verification resources",
            "--apply": "perform the confirmed operation",
            "--confirm-plan": "exact fingerprint from the preceding plan",
            "--timeout": "operation timeout in seconds",
            "--receipt-out": "write a sanitized operation receipt",
        },
        "subcommands": {
            "pull": {
                "required_options": ["--image", "--namespace", "--name"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
            }
        },
        "related": ("toolbox.py", "harbor_push.py"),
        "references": (
            "Harbor API shapes",
            "Harbor authentication",
            "Harbor Helm OCI",
        ),
        "required_tools": ("kubectl",),
        "returns": "Versioned operation plan or independently verified callback results, with "
        "cleanup evidence.",
        "execution_surface": "installed skill and configured provider APIs; GitHub Actions runs "
        "tests and lint only",
        "side_effects": "Only confirmed operations mutate selected resources; temporary helpers "
        "are cleaned on exit.",
    },
    "image_build_readiness.py": {
        "kind": "image_build_readiness",
        "purpose": "Probe nested Podman or build and publish a toolbox for a running GitLab runner.",
        "use_when": "Prove nested execution alone, or configure a GitLab runner with a published toolbox.",
        "requires": ("kubernetes",),
        "capabilities": ("node_scoped", "namespaced"),
        "mutates": True,
        "options": {
            "--namespace": "existing namespace; default creates a run namespace retained by a successful chain",
            "--probe-image": "digest-pinned image providing Podman; default is Linux amd64",
            "--inner-image": "digest-pinned inner test image; default is Linux amd64",
            "--timeout": "maximum probe duration in seconds",
            "--cpu-request": "probe container CPU request",
            "--cpu-limit": "probe container CPU limit",
            "--memory-request": "probe container memory request",
            "--memory-limit": "probe container memory limit",
            "--storage-size": "maximum temporary container-storage volume size",
            "--live-plan": "read target identity and select a Ready worker without mutation",
            "--apply": "run the confirmed operation and read back its result",
            "--confirm-plan": "exact fingerprint returned by the live plan",
            "--receipt-out": "write a sanitized operation receipt to this exact path",
            "--toolbox-spec": "chain: existing toolbox specification with gitlab-runner, git and podman",
            "--project": "chain: GitLab project for a project runner",
            "--group": "chain: GitLab group for a group runner",
            "--runner-description": "chain: unique description for the runner record",
            "--runner-label": "chain: runner tag; repeatable",
        },
        "subcommands": {
            "probe": {
                "purpose": "Run a temporary Podman Job on one selected worker.",
                "requires_authorities": ("kubernetes",),
                "result_schema": "schemas/results/image-build-readiness.schema.json",
                "mutates": True,
                "side_effects": "Creates and removes a temporary namespace, conditional "
                "run-owned SCC RoleBinding, script ConfigMap and privileged Job.",
                "read_back": "Exact permission source, optional RoleBinding UID, "
                "Job-owned Pod exit, probe rows and resource absence.",
            },
            "chain": {
                "purpose": "Build and publish the toolbox, then configure a runner using that image.",
                "requires_authorities": ("kubernetes", "gitlab", "harbor"),
                "required_options": (
                    "--toolbox-spec",
                    "--runner-description",
                    "--runner-label",
                ),
                "result_schema": "schemas/results/image-build-readiness.schema.json",
                "mutates": True,
                "side_effects": "Publishes the toolbox image and retains the runner record, manager and "
                "required cluster resources for caller use.",
                "read_back": "Published toolbox digest and the configured runner deployment using that image.",
            },
        },
        "related": (
            "toolbox.py",
            "harbor_pull.py",
            "gitlab_runner_manager.py",
        ),
        "references": (
            "Nested Podman readiness",
            "Image build chain",
            "OpenShift SCC access",
            "Podman inside a Pod",
            "Harbor API shapes",
        ),
        "required_tools": (
            "kubectl",
            "oc on OpenShift; chain requires oc, glab and skopeo",
        ),
        "returns": "A plan or operation evidence: probe marker rows and cleanup, or the published toolbox "
        "digest and retained runner manager workload.",
        "execution_surface": "configured Kubernetes API; chain also uses the selected GitLab project and Harbor registry",
        "side_effects": "Probe creates and removes temporary cluster resources. Chain publishes the toolbox "
        "and leaves its configured runner and manager available.",
    },
    "access_check.py": {
        "kind": "access_check",
        "purpose": "Prove access to every selected authority and emit one receipt.",
        "use_when": (
            "When you need one live receipt for the configured GitHub, GitLab "
            "or Kubernetes authorities on the execution host."
        ),
        "requires": (),
        "optional_requires": DEFAULT_AUTHORITIES,
        "capabilities": (),
        "options": {
            "--publication": "require GitHub, repository administration and the declared required checks",
            "--job-url": "require GitLab and prove access to one job, its pipeline, runner and trace",
            "--ceph-namespace": "require Kubernetes and prove Ceph health, OSD, PG and Pod reads",
            "--receipt-out": "write the committable receipt, with host paths digested",
        },
        "returns": "A receipt: credential sources, identities, targets, skill digest, and one entry per live check.",
    },
    "gitlab_job.py": {
        "references": (
            "GitLab runner",
            "GitLab runner configuration",
            "GitLab Runner feature flags",
            "GitLab Runner cache tuning",
        ),
        "related": ("gitlab_pipeline.py", "gitlab_runner.py"),
        "kind": "gitlab_job",
        "purpose": "Get one CI job or list bounded jobs from one GitLab project.",
        "use_when": "You need exact job facts or a filtered view of recent jobs.",
        "requires": ("gitlab",),
        "capabilities": ("filters_records", "time_ranged"),
        "options": {
            "--job-url": "full HTTPS URL of one job for get",
            "--project": "exact project path or numeric ID for list; otherwise use gitlab.project",
            "--status": "list state; repeatable; includes derived stuck",
            "--pipeline-id": "list jobs of one numeric pipeline ID",
            "--ref": "list jobs of one exact branch or tag",
            "--name-glob": "shell-style job-name pattern for list",
            "--limit": "maximum matching list records, 1–500 (default 50)",
            "--stuck-after": "minimum pending or created age in seconds (default 600)",
        },
        "required_options": ("--job-url",),
        "required_tools": ("glab",),
        "subcommands": {
            "get": {
                "purpose": "read one exact job, pipeline, runner, and bounded trace",
                "required_options": ["--job-url"],
            },
            "list": {
                "purpose": "filter newest jobs of one verified project",
                "required_options": [],
                "read_back": "GitLab project jobs keyset API; at most 20 pages of 100",
                "result_schema": "schemas/results/gitlab-job-list.schema.json",
            },
        },
        "returns": (
            "Get: one job, pipeline, runner, and bounded trace. "
            "List: up to 500 validated job records, record_count and truncated; "
            "PARTIAL if the page scan cannot prove completeness."
        ),
    },
    "gitlab_pipeline.py": {
        "related": (
            "gitlab_job.py",
            "gitlab_milestone.py",
            "gitlab_runner.py",
        ),
        "kind": "gitlab_pipeline",
        "purpose": "Read, start, or watch an exact GitLab CI pipeline.",
        "use_when": (
            "You need exact pipeline state, a confirmed start, or bounded "
            "completion evidence."
        ),
        "requires": ("gitlab",),
        "capabilities": (),
        "required_tools": ("glab",),
        "mutates": True,
        "side_effects": "Confirmed start may create one pipeline on the selected ref.",
        "references": (
            "GitLab pipeline execution",
            "GitLab runner",
            "GitLab REST inputs",
        ),
        "options": {
            "--project": (
                "exact project path or numeric ID; otherwise use gitlab.project"
            ),
            "--run-id": (
                "diagnostic identifier; start reuses it for a retry and changes it for a new identical build"
            ),
            "--pipeline-id": "numeric pipeline ID for get or watch",
            "--ref": "start ref; otherwise the live project default branch",
            "--variable": (
                "pipeline variable as KEY=VALUE; CI_SKILLS_PIPELINE_PLAN_DIGEST is managed by start"
            ),
            "--require-protected-ref": (
                "require the resolved start branch to be protected"
            ),
            "--expected-sha": (
                "require the ref to resolve to this full lowercase commit SHA"
            ),
            "--related-name": (
                "watch newer same-ref pipelines whose names match this regex"
            ),
            "--interval": "positive watch polling interval in seconds (default 10)",
            "--timeout": "positive watch deadline or start request timeout in seconds",
            "--live-plan": (
                "resolve the exact start ref and SHA without creating a pipeline"
            ),
            "--apply": "create the pipeline selected by the confirmed live plan",
            "--confirm-plan": "exact SHA-256 digest printed by --live-plan",
            "--receipt-out": "write one portable start or watch result",
        },
        "subcommands": {
            "get": {
                "purpose": "Read one exact pipeline and its bounded jobs.",
                "required_options": ["--pipeline-id"],
                "mutates": False,
                "read_back": "GitLab pipeline and job APIs for the selected numeric ID.",
            },
            "start": {
                "purpose": (
                    "Resolve an exact ref and commit, then start one confirmed pipeline."
                ),
                "required_options": [],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "mutates": True,
                "side_effects": (
                    "Confirmed apply creates one pipeline or returns NO_OP for its verified plan marker."
                ),
                "read_back": (
                    "GET the exact pipeline and variables; require project, ref, SHA and plan marker."
                ),
            },
            "watch": {
                "purpose": "Watch one bounded pipeline graph to settlement or timeout.",
                "required_options": ["--pipeline-id"],
                "result_schema": "schemas/results/gitlab-pipeline-watch.schema.json",
                "mutates": False,
                "read_back": (
                    "Poll root, child, related and job state through bounded GitLab reads."
                ),
            },
        },
        "returns": (
            "Get returns one validated pipeline and bounded jobs. Start returns an "
            "offline or live plan, or a verified new or existing plan identity. Watch returns "
            "bounded graph completion, success, changes and retrieve choices."
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
        "references": (
            "GitLab REST inputs",
            "GitLab milestone dates",
        ),
        "kind": "gitlab_milestone",
        "purpose": "Create or update an exact GitLab milestone with independent read-back.",
        "use_when": "A selected project or group milestone needs a requested change.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "side_effects": "Confirmed apply may create or update the selected milestone.",
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
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write a sanitized, shareable operation receipt",
            ),
        },
        "subcommands": {
            "create": {
                "required_options": ["--title"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
            "update": {
                "required_options": ["--milestone-id"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
            "adjust-time": {
                "required_options": ["--milestone-id"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
        },
        "examples": [
            {
                "description": "Offline plan without persistent files; retain plan_digest from stdout",
                "command": 'gitlab_milestone.py create --project "$PROJECT" --title "Release review" --dry-run --json',
            },
            {
                "description": "Apply the same inputs with plan_digest as PLAN_DIGEST and caller-selected paths",
                "command": 'gitlab_milestone.py create --project "$PROJECT" --title "Release review" --apply --confirm-plan "$PLAN_DIGEST" --receipt-out receipt.json --output-dir reports --log-file action.log --json',
            },
        ],
        "returns": "A sanitized plan or verified milestone record and independent GET evidence.",
    },
    "gitlab_issue.py": {
        "references": ("GitLab milestone dates",),
        "kind": "gitlab_issue",
        "purpose": "Create or reuse an exact GitLab bug issue with independent read-back.",
        "use_when": "A selected project needs a requested bug issue.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "side_effects": "Confirmed apply may create the selected bug issue.",
        "options": {
            "--project": "exact project path or numeric ID",
            "--title": "bug title",
            "--description-file": "file containing the requested description",
            "--label": "label; repeat for multiple labels",
            "--milestone-id": "optional numeric milestone ID",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write a sanitized, shareable operation receipt",
            ),
        },
        "subcommands": {
            "open-bug": {
                "required_options": ["--title"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
            "create-bug": {
                "required_options": ["--title"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
        },
        "returns": "A sanitized plan or verified issue IID and independent GET evidence.",
    },
    "gitlab_repository.py": {
        "kind": "gitlab_repository",
        "purpose": "Commit caller-supplied files to a GitLab branch and verify their bytes.",
        "use_when": "Create or update project source, a Dockerfile or pipeline configuration.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "related": ("gitlab_pipeline.py", "gitlab_mr.py"),
        "references": ("GitLab repository commits",),
        "required_tools": ("glab",),
        "side_effects": "Confirmed apply creates one commit and optionally its requested branch; GitLab may trigger the project's configured pipeline.",
        "options": {
            "--project": "exact project path or numeric ID",
            "--branch": "destination branch",
            "--start-branch": "existing branch or commit to start a new branch from",
            "--commit-message": "message for this commit",
            "--actions-file": "JSON array of native GitLab create/update file actions",
            "--apply": "perform the confirmed commit",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write the verified branch, commit and file digests",
            ),
        },
        "subcommands": {
            "commit": {
                "required_options": ["--branch", "--commit-message", "--actions-file"],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "mutates": True,
                "read_back": "Read the commit, exact file bytes and final branch head.",
            },
        },
        "examples": [
            {
                "description": "Offline plan without persistent files; retain plan_digest from stdout",
                "command": 'gitlab_repository.py commit --project "$PROJECT" --branch "$BRANCH" --start-branch "$BASE" --commit-message "Add consumer example" --actions-file actions.json --dry-run --json',
            },
            {
                "description": "Apply the same inputs with plan_digest as PLAN_DIGEST and caller-selected paths",
                "command": 'gitlab_repository.py commit --project "$PROJECT" --branch "$BRANCH" --start-branch "$BASE" --commit-message "Add consumer example" --actions-file actions.json --apply --confirm-plan "$PLAN_DIGEST" --receipt-out receipt.json --output-dir reports --log-file action.log --json',
            },
        ],
        "returns": "A plan or verified branch, commit SHA and per-file content digests.",
    },
    "gitlab_mr.py": {
        "kind": "gitlab_merge_request",
        "purpose": "Create a GitLab merge request or attach it to a milestone.",
        "use_when": "Open an MR for selected branches or assign an existing MR's milestone.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "related": (
            "gitlab_repository.py",
            "gitlab_milestone.py",
            "gitlab_pipeline.py",
        ),
        "references": ("GitLab merge requests",),
        "required_tools": ("glab",),
        "side_effects": "Confirmed apply creates one MR or changes only the selected MR's milestone.",
        "options": {
            "--project": "exact project path or numeric ID",
            "--source-branch": "existing source branch for create",
            "--target-branch": "existing target branch for create",
            "--title": "MR title for create",
            "--mr-iid": "project-local MR number for update, not its global ID",
            "--milestone-id": "global milestone ID, not its project-local IID",
            "--apply": "perform the confirmed MR change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write a sanitized operation receipt",
            ),
        },
        "subcommands": {
            "create": {
                "required_options": ["--source-branch", "--target-branch", "--title"],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "mutates": True,
                "read_back": "Read the exact MR IID, branches, title and requested milestone.",
            },
            "update": {
                "required_options": ["--mr-iid", "--milestone-id"],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "mutates": True,
                "read_back": "Read the selected MR and its assigned milestone ID.",
            },
        },
        "examples": [
            {
                "description": "Plan an MR",
                "command": 'gitlab_mr.py create --project "$PROJECT" --source-branch "$BRANCH" --target-branch "$BASE" --title "Consumer example"',
            },
            {
                "description": "Plan its milestone assignment",
                "command": 'gitlab_mr.py update --project "$PROJECT" --mr-iid "$MR_IID" --milestone-id "$MILESTONE_ID"',
            },
        ],
        "returns": "A plan or verified MR ID, project-local IID and milestone ID.",
    },
    "gitlab_wiki.py": {
        "references": (
            "GitLab REST inputs",
            "GitLab wiki content",
        ),
        "kind": "gitlab_wiki",
        "purpose": "Create or update an exact GitLab wiki page with independent read-back.",
        "use_when": "A selected project wiki page needs requested content.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "side_effects": "Confirmed apply may create or update the selected wiki page.",
        "options": {
            "--project": "exact project path or numeric ID",
            "--title": "page title for create",
            "--slug": "exact existing page slug for update",
            "--content-file": "file containing the requested page content",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint printed by the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write a sanitized, shareable operation receipt",
            ),
        },
        "subcommands": {
            "create": {
                "required_options": ["--title", "--content-file"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
            "update": {
                "required_options": ["--slug", "--content-file"],
                "result_schema": GITLAB_ACTION_SCHEMA,
            },
        },
        "returns": "A sanitized plan or verified wiki slug and independent GET evidence.",
    },
    "gitlab_runner.py": {
        "references": (
            "GitLab runner",
            "GitLab REST inputs",
            "GitLab runner configuration",
            "GitLab Runner feature flags",
            "GitLab Runner cache tuning",
            "GitLab Runner Operator",
        ),
        "related": ("gitlab_job.py", "gitlab_pipeline.py"),
        "kind": "gitlab_runner",
        "purpose": "Read and manage a selected GitLab runner record with verified actions.",
        "use_when": "A job names a runner to inspect, or a project needs runner record management.",
        "requires": ("gitlab",),
        "capabilities": (),
        "mutates": True,
        "side_effects": "Apply may create, assign, tag, or delete a runner record.",
        "options": {
            "--project": "exact project path or numeric ID",
            "--group": "exact group path or numeric ID",
            "--runner-id": "numeric runner ID, required for assign, tag, and delete",
            "--job-id": "project job ID whose related runner get should inspect",
            "--runner-type": "project or group scope for creation",
            "--description": "runner description and server-visible recovery key",
            "--limit": "maximum returned runner records for list (1-100, default 10)",
            "--filter": "repeatable runner state filter: protected, unprotected, online, offline, shared, dedicated, paused, active",
            "--tag": "runner tag for create or tag; repeat for multiple tags",
            "--token-out": "caller-selected 0600 file for the one-time runner token",
            "--live-plan": "read group assignment or runner deletion identity and print an apply-ready plan without writes",
            "--apply": "perform the validated change",
            "--confirm-plan": "SHA-256 fingerprint from the live plan for group assignment or delete, otherwise the dry-run plan",
            "--timeout": "maximum seconds for each external request",
            **persistence_options(
                GITLAB_MODE_POLICY,
                "write a sanitized, shareable operation receipt",
                read_operations=GITLAB_RUNNER_READ_OPERATIONS,
                live_plan=True,
            ),
        },
        "subcommands": {
            "get": {
                "required_options": [],
                "result_schema": "schemas/results/gitlab-runner-read.schema.json",
                "purpose": "Read one runner by ID, exact unique description, or related project job ID.",
                "mutates": False,
                "read_back": "Read the selected job when given, then runner details and selected-scope membership.",
            },
            "list": {
                "required_options": [],
                "result_schema": "schemas/results/gitlab-runner-read.schema.json",
                "purpose": "List bounded scoped runners with optional exact description and state filters.",
                "mutates": False,
                "read_back": "Read each matching runner from the bound GitLab API.",
            },
            "assign": {
                "required_options": ["--runner-id"],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "purpose": "Assign an existing runner to the selected project or group.",
                "mutates": True,
                "side_effects": "Apply may attach a runner to selected projects.",
                "read_back": "Read scoped runner membership after assignment.",
            },
            "create": {
                "required_options": ["--runner-type", "--description", "--token-out"],
                "result_schema": GITLAB_ACTION_SCHEMA,
                "purpose": "Create a runner record and save its one-time token.",
                "mutates": True,
                "side_effects": "Apply may create a runner record and write the token file.",
                "read_back": "Read the new runner record and scoped listing.",
            },
            "delete": {
                "required_options": ["--runner-id"],
                "result_schema": "schemas/results/gitlab-runner-delete.schema.json",
                "purpose": "Delete one runner by ID after a live plan binds its identity and project assignments.",
                "mutates": True,
                "side_effects": "Apply deletes the selected runner record.",
                "read_back": "Require global 404 and absence from the selected scope.",
            },
            "tag": {
                "required_options": ["--runner-id", "--tag"],
                "result_schema": "schemas/results/gitlab-runner-tag.schema.json",
                "purpose": "Add tags to an existing runner record.",
                "mutates": True,
                "side_effects": "Apply may update the runner tag list.",
                "read_back": "Read tags from a fresh runner GET and scoped listing.",
            },
        },
        "returns": "Public runner fields, a sanitized plan, or verified before/after action evidence.",
    },
    "gitlab_runner_manager.py": {
        "kind": "gitlab_runner_manager",
        "purpose": "Deploy or remove one toolbox-backed GitLab shell runner.",
        "use_when": "A pre-created project or group runner must execute on the "
        "exact worker proven ready for Podman.",
        "requires": ("gitlab", "kubernetes", "harbor"),
        "capabilities": ("namespaced", "node_scoped"),
        "mutates": True,
        "options": {
            "--project": "exact project path or numeric ID; otherwise use "
            "gitlab.project",
            "--group": "exact group path or numeric ID; otherwise use gitlab.group",
            "--runner-id": "positive ID returned by gitlab_runner.py create",
            "--description": "exact description used to create the runner record",
            "--tag": "GitLab runner tag given to gitlab_runner.py create --tag; "
            "repeat for multiple tags; --label is accepted as an alias",
            "--image": "digest-pinned toolbox image containing gitlab-runner and "
            "podman",
            "--token-file": "one-time token file written by gitlab_runner.py create",
            "--namespace": "existing namespace to retain; omit for one run-owned "
            "namespace",
            "--node": "exact worker already verified by image_build_readiness.py",
            "--cpu-request": "runner CPU request; repeat the proven probe value",
            "--cpu-limit": "runner CPU limit; repeat the proven probe value",
            "--memory-request": "runner memory request; repeat the proven probe value",
            "--memory-limit": "runner memory limit; repeat the proven probe value",
            "--storage-size": "runner Podman storage limit; repeat the proven probe value",
            "--apply": "perform the confirmed operation",
            "--confirm-plan": "exact fingerprint from the preceding plan",
            "--timeout": "operation timeout in seconds",
            "--receipt-out": "write a sanitized operation receipt",
        },
        "repeatable_options": ("--tag",),
        # --label was the option name before --tag matched gitlab_runner.py create.
        "option_aliases": {"--tag": ("--label",)},
        "subcommands": {
            "deploy": {
                "required_options": [
                    "--runner-id",
                    "--description",
                    "--tag",
                    "--image",
                    "--token-file",
                    "--node",
                ],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
                "requires_authorities": ["gitlab", "kubernetes", "harbor"],
                "side_effects": "Confirmed apply opens or reuses one run scope, "
                "creates a run-owned SCC RoleBinding only when required, and "
                "retains the verified manager workload.",
                "read_back": "Exact-node privileged Deployment and immutable Pod "
                "image, then the exact scoped GitLab runner online with contact "
                "evidence.",
            },
            "delete": {
                "required_options": ["--runner-id", "--description", "--run-id"],
                "result_schema": "schemas/results/toolbox-workflow.schema.json",
                "requires_authorities": ["gitlab", "kubernetes"],
                "side_effects": "Confirmed apply removes only run-owned manager "
                "resources and preserves the GitLab runner record.",
                "read_back": "Owned Deployment and Secrets absent, exact runner "
                "record preserved with its current cached status, run-owned SCC "
                "RoleBinding absent, and owned generated namespace absent.",
            },
        },
        "related": (
            "gitlab_runner.py",
            "gitlab_pipeline.py",
            "gitlab_job.py",
            "image_build_readiness.py",
        ),
        "references": (
            "GitLab runner manager",
            "OpenShift SCC access",
            "GitLab runner",
        ),
        "required_tools": ("glab", "kubectl", "oc on OpenShift"),
        "returns": "A run ID and confirmed plan, or verified workload and exact "
        "GitLab runner read-back with cleanup evidence.",
        "execution_surface": "installed skill, configured provider APIs, and the "
        "caller-selected digest-pinned toolbox image",
        "side_effects": "Deploy uses GitLab, Kubernetes and Harbor; delete uses "
        "GitLab and Kubernetes only. Provider runner deletion remains "
        "gitlab_runner.py delete.",
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
        "purpose": "Compare PCI Ethernet uplink MTUs across selected Kubernetes nodes.",
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
            "A versioned table of node, PCI NIC, optional IPv4 address and MTU, "
            "consistency findings, and temporary-Pod cleanup read-back."
        ),
        "side_effects": "oc debug creates temporary Pods; apply verifies their cleanup",
    },
}

# Node diagnostics use existing selected Pods through the same API target.
NODE_LOCAL_OPTIONS: dict[str, str] = {
    "--binding": "project binding for target and ordered kubeconfig sources",
    "--revision": "exact source commit of the installed copy, recorded as a claim",
    "--json": "versioned JSON document",
    "--yaml": "versioned YAML document",
    "--human": "human summary even when stdout is not a terminal",
    "--dry-run": "list API and existing-Pod probes without executing them",
    "--search": "case-insensitive text filter over returned records",
    "--describe": "this command's machine-readable contract, then exit",
}

NODE_LOCAL_COMMANDS: Mapping[str, NodeDefinition] = {
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

READ_OPTIONS: Final[Mapping[str, str]] = {
    option: UNIVERSAL_OPTIONS[option]
    for option in (
        "--json",
        "--yaml",
        "--describe",
        "--dry-run",
        "--log-format",
        "--log-level",
        "--log-file",
        "--run-id",
    )
}
READ_EXIT_CODES: Final[Mapping[str, str]] = {
    "0": "successful read or validated plan",
    "64": "invalid arguments",
    "65": "invalid provider data, manifest or source identity",
    "66": "missing input file or directory",
    "69": "provider, credential or required tool unavailable",
}
READ_COMMANDS: Final[Mapping[str, ReadDefinition]] = {
    "bin/ci-api": {
        "report_kind": "ci_api",
        "purpose": "Read one caller-selected GitHub or GitLab API endpoint with bounded output.",
        "use_when": "A supported domain command does not expose the selected GET resource.",
        "requires_authorities": ["one caller-selected GitHub or GitLab host"],
        "required_tools": ["python3", "gh or glab"],
        "required_options": ["--provider"],
        "options": {
            **READ_OPTIONS,
            "--provider": "native provider: github or gitlab",
            "--host": "provider host override; must match an explicit GitLab profile",
            GITLAB_PROFILE_OPTION: GITLAB_PROFILE_HELP,
            "--token-file": "existing token-file reference; otherwise use shared credential resolution",
            "--endpoint": "relative GET endpoint; required by get",
            "--field": "select one dotted JSON object field",
            "--decode-base64": "decode the selected field as UTF-8 text",
            "--lines": "numbered START:END slice of text; at most 200 lines",
            "--paginate": "collect native array pages within the response limit",
            "--output": "legacy output selection: json or text; default json",
        },
        "subcommands": {
            "check": {
                "purpose": "Read the authenticated provider identity.",
                "required_options": ["--provider"],
            },
            "get": {
                "purpose": "Read one relative endpoint.",
                "required_options": ["--provider", "--endpoint"],
            },
        },
        "description": "Use the shared target and credential resolver, bounded GET retries and a 1 MiB response limit. Native provider data stays native; field and text selection reduce what the agent reads.",
        "examples": [
            {
                "description": "Read the configured GitLab identity",
                "command": "ci-api check --provider gitlab",
            },
            {
                "description": "Select one field",
                "command": "ci-api get --provider github --endpoint user --field login --output text",
            },
        ],
        "output_modes": {
            "json": "native JSON or selected content; default",
            "yaml": "the same selected value as YAML",
            "text": "raw endpoint text or one selected scalar",
        },
        "usage": "Choose check or get and one provider. Target resolution is automatic; CI_SKILLS_TARGET and ci-skills-smoke support tests and live smoke.",
        "returns": "Bounded JSON, YAML or selected text; dry-run returns a request plan.",
        "read_only": True,
        "side_effects": "none",
        "execution_surface": "installed Python command and one caller-selected provider API",
    },
    "bin/ci-binary-build": {
        "report_kind": "ci_binary_build",
        "purpose": "Plan an exact-commit OpenShift Binary BuildConfig without applying it.",
        "use_when": "A selected BuildConfig and source commit need validation before a build.",
        "requires_authorities": [],
        "required_tools": ["python3", "PyYAML", "git"],
        "required_options": [
            "--spec",
            "--source-repo",
            "--source-commit",
            "--commit-label",
        ],
        "options": {
            **READ_OPTIONS,
            "--spec": "caller-owned Binary/Docker BuildConfig YAML or JSON",
            "--source-repo": "source Git repository containing the exact commit",
            "--source-commit": "exact lowercase 40-character source commit",
            "--commit-label": "BuildConfig metadata label containing the selected commit",
            "--context": "optional caller context recorded in the plan",
            "--dry-run": "validate the same plan; this command always plans and never builds",
        },
        "description": "Validate the caller's BuildConfig, commit label and Dockerfile at the exact Git commit. Return the manifest, source tree and SHA-256 fingerprint for a later callback.",
        "examples": [
            {
                "description": "Validate selected build inputs",
                "command": 'ci-binary-build --spec "$BUILD_CONFIG" --source-repo "$SOURCE_REPO" --source-commit "$SOURCE_COMMIT" --commit-label "$COMMIT_LABEL"',
            },
        ],
        "output_modes": {
            "json": "validated plan and fingerprint; default",
            "yaml": "the same plan and fingerprint as YAML",
        },
        "usage": "Supply the BuildConfig, source repository, exact commit and commit-label key. This operation never executes a build or publishes an image.",
        "returns": "A JSON or YAML plan and fingerprint bound to one Git tree and BuildConfig.",
        "read_only": True,
        "side_effects": "none",
        "execution_surface": "installed Python command and local Git checkout; no cluster call",
    },
}

PROPOSITION_VERSION: Final = "3.0"
PROPOSITION_CLUSTERS: Final = MappingProxyType(
    {
        "glab": "gitlab",
        "kubernetes": "kubernetes",
        "github": "github",
        "harbor": "harbor",
    }
)
PROPOSITION_REFERENCES: Final = MappingProxyType(
    {
        "GitLab runner configuration": {
            "summary": "Runner record inputs, token handoff, native registration and job selection.",
            "pointer": "references/gitlab-runner-config.md",
        },
        "GitLab Runner feature flags": {
            "summary": "Feature flag keys, defaults, job scope and runner configuration precedence.",
            "pointer": "references/gitlab-runner-feature-flags.md",
        },
        "GitLab Runner cache tuning": {
            "summary": "CACHE_CHUNK_SIZE, CACHE_CONCURRENCY, units, backend limits and memory costs.",
            "pointer": "references/gitlab-runner-cache.md",
        },
        "GitLab Runner Operator": {
            "summary": "OpenShift Runner spec.env, spec.config, ConfigMap examples and versioned properties.",
            "pointer": "references/gitlab-runner-operator.md",
        },
        "GitLab milestone dates": {
            "summary": "Milestone dates, state changes and distinct tag or label identities.",
            "pointer": "references/gitlab-milestones.md",
        },
        "GitLab wiki content": {
            "summary": "Wiki content, slug selection, markup formats and native API examples.",
            "pointer": "references/gitlab-wiki.md",
        },
        "Harbor authentication": {
            "summary": "Credential file keys, robot scope and OIDC CLI authentication.",
            "pointer": "references/harbor-authentication.md",
        },
        "Harbor Helm OCI": {
            "summary": "Chart coordinates, Helm registry login, publishing and installation.",
            "pointer": "references/harbor-helm-oci.md",
        },
        "access": {
            "summary": "Selected authority and credential sources.",
            "pointer": "references/access.md",
        },
        "project binding": {
            "summary": "Project target and credential binding.",
            "pointer": "references/project-binding.md",
        },
        "toolbox specification": {
            "summary": "Toolbox dependencies, installation methods and build inputs.",
            "pointer": "schemas/toolbox.schema.json",
        },
        "Tool Pod preparation": {
            "summary": "Tool input, preparation, reuse and removal examples.",
            "pointer": "references/tool-pod.md",
        },
        "GitLab pipeline execution": {
            "summary": "Exact pipeline read, confirmed start and bounded watch flow.",
            "pointer": "references/gitlab-pipeline.md",
        },
        "Nested Podman readiness": {
            "summary": "Podman probe inputs, planning, execution and evidence.",
            "pointer": "references/podman-readiness.md",
        },
        "Image build chain": {
            "summary": "Build and publish a toolbox, then start a runner using its immutable image.",
            "pointer": "references/image-build-chain.md",
        },
        "OpenShift SCC access": {
            "summary": "Discover SCCs, inspect permission and read the admitted constraint.",
            "pointer": "references/openshift-scc.md",
        },
        "GitLab runner manager": {
            "summary": "Runner token, exact-node deployment, verification and cleanup.",
            "pointer": "references/gitlab-runner-manager.md",
        },
        "Native OCP YAML example": {
            "summary": "Node MAC/NIC, disk and network inputs passed unchanged to OpenShift.",
            "pointer": "references/ocp-node-input.md",
        },
        "Native OCP command flags": {
            "summary": "OpenShift flags, ci-skills input mapping and upstream procedure.",
            "pointer": "references/ocp-node-image-flags.md",
        },
        "GitLab repository commits": {
            "summary": "Native file-action keys, text/base64 content and branch examples.",
            "pointer": "references/gitlab-repository.md",
        },
        "GitLab merge requests": {
            "summary": "Source/target branches, MR IID and milestone ID examples.",
            "pointer": "references/gitlab-merge-requests.md",
        },
        "GitLab runner": {
            "summary": "config.toml tables, concurrency, speed up and cache variables, feature flags, Operator CR.",
            "pointer": "references/gitlab-runner.md",
        },
        "GitLab REST inputs": {
            "summary": "Runner, milestone, pipeline, schedule, wiki, Harbor and agent config.yaml inputs.",
            "pointer": "references/gitlab-rest-inputs.md",
        },
        "Harbor API shapes": {
            "summary": "Project, robot, retention, quota, registry, replication, webhook and artifact request shapes.",
            "pointer": "references/harbor-api-shapes.md",
        },
        "Podman inside a Pod": {
            "summary": "Storage, containers.conf, build and push inputs that decide a nested Podman build.",
            "pointer": "references/podman-in-pod.md",
        },
        "mcp": {
            "summary": "GitLab MCP configuration and toolsets.",
            "pointer": "references/mcp.md",
        },
    }
)
NAV_OPTIONS: Final = {
    "--page": "one-based discovery or section page",
    "--search": "literal search within one selected reference",
    "--card": "read a declared reference card",
    "--part": "read one declared reference part",
}

DISCOVERY_COMMANDS: Final[Mapping[str, DiscoveryDefinition]] = {
    "reference.py": {
        "report_kind": "reference_next",
        "purpose": "Show what the agent can run and read at one node, or read one reference.",
        "use_when": "Choose a cluster or command, list a reference's sections or read one section.",
        "requires_authorities": [],
        "required_tools": ["python3"],
        "required_options": [],
        "options": [
            "--json",
            "--yaml",
            "--human",
            "--page",
            "--search",
            "--card",
            "--part",
            "--describe",
            "--dry-run",
            "--log-format",
            "--log-level",
            "--log-file",
            "--run-id",
        ],
        "description": "One answer per node: installed tools to run and references to read, each a pointer to the next call.",
        "examples": [
            {"description": "Show available clusters", "command": "reference.py"},
            {"description": "Expand GitLab tools", "command": "reference.py glab"},
            {
                "description": "Expand a command node",
                "command": "reference.py gitlab_runner.py",
            },
            {
                "description": "List one reference's sections",
                "command": "reference.py get mcp",
            },
        ],
        "output_modes": {
            "json": "Compact JSON; default when stdout is not a terminal",
            "yaml": "Versioned YAML with --yaml",
            "human": "Run and read lists; default on a terminal",
        },
        "usage": "reference.py [node] | get reference [anchor] [options]",
        "returns": "A node of tools and references, one reference's sections, or one section's own text.",
        "read_only": True,
        "side_effects": "Only an explicit log file is written; dry-run and describe do not write.",
        "execution_surface": "installed catalogue; no provider or credential access",
    },
}


def command_authorities(script: str) -> Sequence[str]:
    """Resolve provider choices from the registered command's access contract.

    :param script: Installed command key from one of the four catalogues.
    :returns: Provider names, or an empty sequence for an offline command.
        ``ci-api`` offers either Git provider; it does not require both.
    :raises KeyError: If the command is not registered.
    """  # noqa: DOC502 - Dictionary lookup rejects unknown command keys.
    if script in COMMANDS:
        entry = COMMANDS[script]
        return tuple(
            dict.fromkeys((*entry["requires"], *entry.get("optional_requires", ())))
        )
    if script in NODE_LOCAL_COMMANDS:
        return NODE_LOCAL_COMMANDS[script]["requires"]
    if script == "bin/ci-api":
        return ("github", "gitlab")
    if script in READ_COMMANDS:
        return READ_COMMANDS[script]["requires_authorities"]
    return DISCOVERY_COMMANDS[script]["requires_authorities"]


def reference_slug(name: str) -> str:
    """Derive the stable identifier a pointer uses for one registered reference.

    :param name: Registry key, the reference's display name.
    :returns: Lowercase identifier made of letters, digits, hyphens and underscores.
    """
    return re.sub(r"[^a-z0-9_-]", "", name.casefold().replace(" ", "-")) or "reference"


def reference_entry(name: str) -> MutableMapping[str, JSONValue]:
    """Render one registered reference as a read entry of a proposition node.

    :param name: Registry key in ``PROPOSITION_REFERENCES``.
    :returns: Name, summary and the next ``reference.py`` call as a string.
    """
    record = PROPOSITION_REFERENCES[name]
    return {
        "name": name,
        "summary": record["summary"],
        "pointer": f"get {reference_slug(name)}",
    }


def _proposition_offering(
    scripts: Sequence[str],
    commands: Mapping[str, Mapping[str, object]],
    *,
    provider: Optional[str] = None,
    include_command_references: bool = True,
) -> MutableMapping[str, JSONValue]:
    """Offer what the agent can run and what it can read at one node.

    :param scripts: Command keys selected for a cluster or related-command node.
    :param commands: Existing catalogue definitions for the offered commands.
    :param provider: Selected provider; command nodes derive their own dependencies.
    :param include_command_references: Expand task references after a node is selected.
    :returns: Installed filenames under ``tools`` and an ordered array of read entries.
    """
    authorities = (
        {provider}
        if provider is not None
        else {name for script in scripts for name in command_authorities(script)}
    )
    names = ["access", "project binding"] if authorities else []
    if "gitlab" in authorities:
        names.append("mcp")
    if "harbor" in authorities:
        names.append("toolbox specification")
    if include_command_references:
        for script in scripts:
            names.extend(json_strings(commands[script].get("references", ())))
    return {
        "tools": {script: {} for script in sorted(scripts)},
        "reference": [reference_entry(name) for name in dict.fromkeys(names)],
    }


def proposition(node: Optional[str] = None) -> MutableMapping[str, JSONValue]:
    """Offer one graph node from every installed-command catalogue.

    :param node: Cluster name or installed command key; None selects the root.
    :returns: Versioned root or selected tools/reference node. Commands without
        a provider requirement appear in the offline cluster.
    :raises ValueError: If the selected node is not in the catalogue.
    """
    commands: Mapping[str, Mapping[str, object]] = {
        **COMMANDS,
        **NODE_LOCAL_COMMANDS,
        **READ_COMMANDS,
        **DISCOVERY_COMMANDS,
    }
    authorities = {script: command_authorities(script) for script in commands}
    clusters = {
        cluster: _proposition_offering(
            [script for script, names in authorities.items() if authority in names],
            commands,
            provider=authority,
            include_command_references=node is not None,
        )
        for cluster, authority in PROPOSITION_CLUSTERS.items()
    }
    clusters["offline"] = _proposition_offering(
        [script for script, names in authorities.items() if not names],
        commands,
        include_command_references=node is not None,
    )
    if node is None:
        return {"schema_version": PROPOSITION_VERSION, **clusters}
    if node in clusters:
        return {"schema_version": PROPOSITION_VERSION, **clusters[node]}
    if node not in commands:
        raise ValueError("path_unknown")
    entry = copy_json_object(commands[node])
    scripts = [node, *json_strings(entry.get("related", ()))]
    return {
        "schema_version": PROPOSITION_VERSION,
        **_proposition_offering(scripts, commands),
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


def missing_required_options(script: str, args: Namespace) -> list[str]:
    """Return missing options from the selected verb or default command.

    :param script: Command name in the catalog.
    :param args: Parsed command arguments.
    :returns: Missing required option names from the published contract.
    """
    entry = COMMANDS[script]
    action = getattr(args, "action", None)
    verb = entry.get("subcommands", {}).get(action, {}) if action else {}
    required = verb.get("required_options", entry.get("required_options", ()))
    missing = []
    for option in required:
        destination = option.removeprefix("--").replace("-", "_")
        if not getattr(args, destination, None):
            missing.append(option)
    return missing


def supports_gitlab_profile(script: str) -> bool:
    """Identify commands whose declared authority set includes GitLab.

    :param script: Registered API command filename.
    :returns: Whether any supported operation selects GitLab configuration.
    """
    entry = COMMANDS[script]
    authorities = (*entry["requires"], *entry.get("optional_requires", ()))
    return "gitlab" in authorities or any(
        "gitlab" in command.get("requires_authorities", ())
        for command in entry.get("subcommands", {}).values()
    )


def options_for(script: str) -> dict[str, str]:
    """Collect universal, capability, and command options.

    :param script: Command filename registered in the catalog.
    :returns: Option names mapped to their help descriptions, including each
        accepted alias described as ``alias of <option>``.
    """
    entry = COMMANDS[script]
    merged = dict(UNIVERSAL_OPTIONS)
    if supports_gitlab_profile(script):
        merged[GITLAB_PROFILE_OPTION] = GITLAB_PROFILE_HELP
    for capability in entry["capabilities"]:
        merged.update(CAPABILITY_OPTIONS[capability])
    merged.update(entry["options"])
    for option, aliases in entry.get("option_aliases", {}).items():
        merged.update({alias: f"alias of {option}" for alias in aliases})
    return merged


def describe(script: str) -> MutableMapping[str, JSONValue]:
    """Build one command contract for ``--describe``.

    :param script: Command filename registered in the catalog.
    :returns: Machine-readable command contract.
    """
    discovery = script in DISCOVERY_COMMANDS
    read = script in READ_COMMANDS
    entry = copy_json_object(
        READ_COMMANDS[script]
        if read
        else DISCOVERY_COMMANDS[script]
        if discovery
        else COMMANDS[script]
    )
    authorities = json_strings(
        entry.get("requires", entry.get("requires_authorities", ()))
    )
    access = command_authorities(script)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "command_contract",
        "skill": SKILL_NAME,
        "command": script,
        "report_kind": entry["report_kind"] if discovery or read else entry["kind"],
        "purpose": entry["purpose"],
        "use_when": entry["use_when"],
        "requires_authorities": list(authorities),
        "access_protocol": {name: ACCESS_PROTOCOL[name] for name in access},
        "capabilities": list(json_strings(entry.get("capabilities", ()))),
        "required_options": list(json_strings(entry.get("required_options", ()))),
        "options": (
            {
                option: {**UNIVERSAL_OPTIONS, **NAV_OPTIONS}[option]
                for option in json_strings(entry["options"])
            }
            if discovery
            else dict(json_object(entry["options"]))
            if read
            else options_for(script)
        ),
        "returns": entry["returns"],
        "mutates": entry.get("mutates", False),
        "subcommands": entry.get("subcommands", {}),
        "side_effects": entry.get("side_effects", "none"),
        "execution_surface": entry.get("execution_surface", "selected authority API"),
        "required_tools": list(json_strings(entry.get("required_tools", ()))),
        "status_values": STATUS_MEANING,
        "exit_codes": dict(READ_EXIT_CODES) if read else EXIT_CODES,
        "default_output": (
            "json for legacy nodes; human for next/get"
            if discovery
            else "json"
            if read
            else "json when stdout is not a terminal, human when it is"
        ),
        "target_protocol": []
        if discovery or script == "bin/ci-binary-build"
        else TARGET_PROTOCOL,
        **(
            {
                "summary": entry["purpose"],
                "description": entry["description"],
                "examples": entry["examples"],
                "output_modes": entry["output_modes"],
                "usage": entry["usage"],
            }
            if discovery or read
            else {
                key: entry[key]
                for key in ("description", "examples", "output_modes", "usage")
                if key in entry
            }
        ),
    }


def describe_node(script: str) -> MutableMapping[str, JSONValue]:
    """Build the contract for a node read through a selected Pod.

    :param script: Registered node-local command filename.
    :returns: Machine-readable node command contract.
    """
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


def manifest() -> MutableMapping[str, JSONValue]:
    """Build the complete skill manifest for ``tools.json``.

    :returns: Command and authority catalog for installed agents.
    """
    return copy_json_object(
        {
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
                        "required_options": list(
                            json_strings(entry.get("required_options", ()))
                        ),
                        "options": sorted(options_for(script)),
                        "returns": entry["returns"],
                        "read_only": not entry.get("mutates", False),
                        "subcommands": entry.get("subcommands", {}),
                        "side_effects": entry.get("side_effects", "none"),
                        "execution_surface": entry.get(
                            "execution_surface", "selected authority API"
                        ),
                        "required_tools": list(
                            json_strings(entry.get("required_tools", ()))
                        ),
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
                **{
                    script: {
                        **{
                            key: value
                            for key, value in entry.items()
                            if key
                            not in {
                                "description",
                                "examples",
                                "output_modes",
                                "usage",
                                "options",
                            }
                        },
                        "options": sorted(entry["options"]),
                    }
                    for script, entry in READ_COMMANDS.items()
                },
                **DISCOVERY_COMMANDS,
            },
            "routing": {
                "prove nested Podman runtime readiness": "image_build_readiness.py",
                "build and publish a toolbox for a running GitLab runner": "image_build_readiness.py",
                "build or inspect a cluster-held OpenShift node ISO": "ocp_node_onboard.py",
                "prepare or remove reusable cluster tools": "prepare_tool_pod.py",
                "build or delete a toolbox": "toolbox.py",
                "publish an image to Harbor": "harbor_push.py",
                "verify a Harbor toolbox pull": "harbor_pull.py",
                "create or delete a GitHub runner": "create_selfhosted_runner.py",
                "browse tools and references": "reference.py",
                "prove configured access authorities": "access_check.py",
                "a named CI job failed": "gitlab_job.py",
                "check GitLab pipeline progress": "gitlab_pipeline.py",
                "GitLab operation access": "gitlab_access.py",
                "create or change a GitLab milestone": "gitlab_milestone.py",
                "open a GitLab bug": "gitlab_issue.py",
                "commit files to a GitLab branch": "gitlab_repository.py",
                "create a GitLab MR or assign its milestone": "gitlab_mr.py",
                "create or change a GitLab wiki page": "gitlab_wiki.py",
                "read or manage a GitLab runner": "gitlab_runner.py",
                "deploy or remove a GitLab runner manager": "gitlab_runner_manager.py",
                "a volume or claim is stuck": "storage_report.py",
                "what the cluster said during an interval": "event_trace.py",
                "connectivity or CNI health": "cilium_status.py",
                "Cilium daemon and health on a selected node": "cilium_node.py",
                "host Ceph or RBD kernel messages through an existing Pod": "ceph_kernel.py",
                "Ceph cluster health and OSD/monitor Pods": "ceph_cluster.py",
                "physical uplink MTU consistency across nodes": "k8s_verify_mtu_consistency.py",
                "read a caller-selected Git API resource": "bin/ci-api",
                "plan an exact-commit binary BuildConfig": "bin/ci-binary-build",
            },
        }
    )
