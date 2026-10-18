"""Parse the ci-skills wrapper while preserving OpenShift's native node input.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Optional

from lib.python.core.action_modes import (
    ActionMode,
    ActionModePolicy,
    validate_action_mode,
)
from lib.python.core.cli import parser
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.artifact_storage_models import StorageDestination
from lib.python.core.kubernetes.ocp_csr import JoinSelector
from lib.python.core.kubernetes.ocp_native_input import NativeNodeInput
from lib.python.core.kubernetes.pod_credential_models import PullSecretReference
from lib.python.core.kubernetes.run_scope import validate_run_id
from lib.python.core.kubernetes.tool_pod_models import ToolPodSpec
from lib.python.core.paths import SKILL_ROOT
from lib.python.core.provenance import functional_digest
from lib.python.core.target import Target, TargetError

NODE_IMAGE_KIND: Final = "ocp_node_onboard"
NODE_IMAGE_COMMAND: Final = "ocp_node_onboard.py"
NODE_IMAGE_REFERENCE: Final = "references/ocp-node-image.md"
DEFAULT_TIMEOUT: Final = 1800
OCP_CONFIG_NAMESPACE: Final = "openshift-config"
OCP_PULL_SECRET: Final = "pull-secret"
REGISTRY_CONFIG_KEY: Final = ".dockerconfigjson"
MODE_POLICY: Final = ActionModePolicy(
    default_mode=ActionMode.OFFLINE,
    receipt_modes=frozenset((ActionMode.APPLY, ActionMode.READ)),
    allow_offline_output=True,
)


class NodeOperation(StrEnum):
    """Operations over a caller-selected in-cluster node ISO.

    .. attribute :: BUILD

        Build and verify delivery from native caller input.

    .. attribute :: VERIFY

        Inspect the artifact addressed by an existing live run.

    .. attribute :: REMOVE

        Release run-owned resources while preserving persistent output.

    .. attribute :: JOIN

        Approve the selected booted node and verify its Ready state.
    """

    BUILD = "build"
    VERIFY = "verify"
    REMOVE = "remove"
    JOIN = "join"


def build_parser() -> argparse.ArgumentParser:
    """Expose native input selection, exact destination, and shared output controls.

    :returns: Installed-command parser with no invented host or storage values.
    """
    result = parser(
        "Build, verify, join or remove an OpenShift node-image run.",
        kind=NODE_IMAGE_KIND,
    )
    result.set_defaults(kind=NODE_IMAGE_KIND)
    result.epilog = (
        "Caller supplies the intended host's MAC or native nodes-config.yaml, "
        "and the cluster storage destination. Network/disk overrides are optional. "
        "Native YAML is passed unchanged to OpenShift. "
        f"Examples: {NODE_IMAGE_REFERENCE}. No ISO is downloaded to this computer."
    )
    result.add_argument("action", nargs="?", choices=tuple(NodeOperation))
    native = result.add_mutually_exclusive_group()
    native.add_argument(
        "--spec", metavar="FILE", help="native nodes-config.yaml; passed unchanged"
    )
    native.add_argument(
        "--mac-address", metavar="MAC", help="joining host's actual NIC MAC address"
    )
    result.add_argument(
        "--network-config-path",
        metavar="FILE",
        help="native network YAML for the MAC route",
    )
    result.add_argument(
        "--destination",
        metavar="FILE",
        help="cluster storage JSON with the exact final ISO path",
    )
    result.add_argument(
        "--tool-pod-spec",
        metavar="FILE",
        help="optional CI12.0 image and dependency override",
    )
    result.add_argument(
        "--node-name",
        metavar="HOSTNAME",
        help="exact Kubernetes Node name selected for join",
    )
    result.add_argument(
        "--ip-address",
        metavar="ADDRESS",
        action="append",
        default=[],
        help="joining node address; repeat for every expected serving address",
    )
    result.add_argument(
        "--pull-secret-namespace",
        default=OCP_CONFIG_NAMESPACE,
        help="namespace of the existing registry Secret (default: OpenShift configuration namespace)",
    )
    result.add_argument(
        "--pull-secret-name",
        default=OCP_PULL_SECRET,
        help="existing registry Secret (default: OpenShift global pull-secret)",
    )
    result.add_argument(
        "--pull-secret-key",
        default=REGISTRY_CONFIG_KEY,
        help="data key containing registry configuration (default: .dockerconfigjson)",
    )
    result.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="positive native-operation timeout in seconds",
    )
    result.add_argument(
        "--live-plan",
        action="store_true",
        help="read prerequisites and produce the confirmation digest",
    )
    result.add_argument(
        "--apply",
        action="store_true",
        help="perform the confirmed build, join or removal",
    )
    result.add_argument(
        "--confirm-plan", metavar="SHA256", help="exact digest returned by the plan"
    )
    result.add_argument(
        "--receipt-out", metavar="PATH", help="write the result to this exact path"
    )
    return result


def execution_mode(args: argparse.Namespace) -> ActionMode:
    """Check mutation controls before reading credentials or producing output.

    Verification reads an existing artifact; its temporary reader is part of that
    operation. Build and remove retain the shared plan/confirmation contract.

    :param args: Parsed invocation settings.
    :returns: Selected operation mode; verify uses the read operation path.
    :raises ValueError: Operation, mode flags, confirmation or timeout is invalid.
    """  # noqa: DOC502,DOC503 - the shared mode validator rejects invalid combinations.
    operation = NodeOperation(args.action)
    return validate_action_mode(
        apply=args.apply,
        dry_run=args.dry_run,
        live_plan=args.live_plan,
        confirmed=bool(args.confirm_plan),
        receipt_requested=bool(args.receipt_out),
        output_requested=bool(args.output_dir or args.log_file),
        timeout=args.timeout,
        policy=MODE_POLICY,
        read_only=operation == NodeOperation.VERIFY,
    )


@dataclass(frozen=True)
class NodeBuildInputs:
    """Capture caller input once so confirmation and execution use identical bytes.

    :param node: Untouched native file bytes or exact MAC flag value.
    :param destination: Caller-selected cluster volume and final path.
    :param tools: Optional caller tool override, resolved before any build mutation.
    :param pull_secret: Existing native registry Secret selector, never its contents.

    .. attribute :: node
        :type: NativeNodeInput

        Unchanged provider input with checksum-only report evidence.

    .. attribute :: destination
        :type: StorageDestination

        Exact caller-selected storage wrapper.

    .. attribute :: tools
        :type: Optional[ToolPodSpec]

        Explicit tool override, absent when provider discovery is requested.

    .. attribute :: pull_secret
        :type: PullSecretReference

        Selected existing registry configuration for native oc.
    """

    node: NativeNodeInput
    destination: StorageDestination
    tools: Optional[ToolPodSpec]
    pull_secret: PullSecretReference

    @classmethod
    def read(cls, args: argparse.Namespace) -> NodeBuildInputs:
        """Read wrapper and native inputs without interpreting OCP YAML.

        :param args: Build invocation with required destination and one native route.
        :returns: One immutable snapshot of all caller files.
        :raises ValueError: A destination or native route is missing or conflicts.
        :raises OSError: One of the explicitly named files cannot be read.
        """  # noqa: DOC503 - shared wrapper and file readers propagate failures.
        if not args.destination:
            raise ValueError(
                "build_requires_destination:choose_cluster_storage_and_final_iso_path"
            )
        return cls(
            node=NativeNodeInput.read(
                spec=Path(args.spec) if args.spec else None,
                mac_address=args.mac_address,
                network_config=Path(args.network_config_path)
                if args.network_config_path
                else None,
            ),
            destination=StorageDestination.load(Path(args.destination)),
            tools=ToolPodSpec.load(Path(args.tool_pod_spec))
            if args.tool_pod_spec
            else None,
            pull_secret=PullSecretReference(
                args.pull_secret_namespace, args.pull_secret_name, args.pull_secret_key
            ),
        )

    def evidence(self) -> JSONObject:
        """Bind native bytes, storage and an explicitly selected tool specification.

        :returns: Nonsecret input evidence; native YAML contents are excluded.
        """
        return {
            "node_input": self.node.evidence(),
            "pull_secret": self.pull_secret.as_dict(),
            "destination": self.destination.as_dict(),
            "tool_pod": self.tools.as_dict() if self.tools is not None else {},
            "tool_source": "caller" if self.tools is not None else "provider",
        }


@dataclass(frozen=True)
class NodeJoinInputs:
    """Capture the stable caller selector for one existing ISO run.

    :param selector: Exact Node name, IP addresses and fixed signer sequence.

    .. attribute :: selector
        :type: JoinSelector

        Caller-selected identity used for every CSR and Node read-back.
    """

    selector: JoinSelector

    @classmethod
    def read(cls, args: argparse.Namespace) -> NodeJoinInputs:
        """Read join inputs without querying the provider.

        :param args: Join invocation containing node name and repeated addresses.
        :returns: Immutable digest-stable selector.
        :raises ValueError: Node identity or address input is absent or invalid.
        """
        if not args.node_name:
            raise ValueError("join_requires_node_name")
        if not args.ip_address:
            raise ValueError("join_requires_ip_address")
        return cls(JoinSelector(args.node_name, tuple(args.ip_address)))

    def evidence(self) -> JSONObject:
        """Render stable join inputs without provider-generated CSR names.

        :returns: Exact caller selector and fixed signer sequence.
        """
        return {"node_selector": self.selector.as_dict()}


def operation_plan(
    args: argparse.Namespace, target: Target, inputs: JSONObject
) -> JSONObject:
    """Bind a selected operation to current functional code and exact inputs.

    :param args: Parsed operation, optional run ID and timeout.
    :param target: Kubernetes-only target resolved through the normal user path.
    :param inputs: Captured build inputs or live artifact reference for later operations.
    :returns: Confirmation document without generated run IDs or credential values.
    :raises TargetError: The selected target lacks Kubernetes configuration.
    :raises ValueError: The operation or required run ID is invalid.
    :raises OSError: Installed functional package files cannot be read.
    """  # noqa: DOC503 - functional hashing propagates file access errors.
    operation = NodeOperation(args.action)
    selected = target.kubernetes
    if selected is None:
        raise TargetError("kubernetes_target_missing")
    build_inputs = (
        args.spec,
        args.mac_address,
        args.network_config_path,
        args.destination,
        args.tool_pod_spec,
    )
    join_inputs = (args.node_name, *args.ip_address)
    if operation == NodeOperation.BUILD:
        if any(join_inputs):
            raise ValueError("build_does_not_accept_join_inputs")
    else:
        if not args.run_id:
            raise ValueError("operation_requires_run_id")
        if any(build_inputs):
            raise ValueError("existing_run_does_not_accept_build_inputs")
        if operation == NodeOperation.JOIN:
            NodeJoinInputs.read(args)
        elif any(join_inputs):
            raise ValueError("artifact_operation_does_not_accept_join_inputs")
    if args.run_id:
        validate_run_id(args.run_id)
    return {
        "kind": NODE_IMAGE_KIND,
        "operation": operation.value,
        "target": {"context": selected.context, "server": selected.server},
        "functional_digest": functional_digest(SKILL_ROOT),
        "run_id": args.run_id or "",
        "timeout_seconds": args.timeout,
        "inputs": inputs,
    }
