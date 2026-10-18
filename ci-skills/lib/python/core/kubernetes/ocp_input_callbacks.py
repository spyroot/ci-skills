"""Stage native OpenShift input and clean only files owned by this invocation.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import MutableSequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.kubernetes import KubernetesError
from lib.python.core.kubernetes.ocp_native_input import NativeNodeInput
from lib.python.core.kubernetes.pod_files import (
    StagedPodFile,
    remove_pod_file,
    stage_pod_file,
)
from lib.python.core.kubernetes.tool_pod_models import ToolPodState
from lib.python.core.kubernetes.tool_pod_runtime import ToolPodK8SCallback
from lib.python.sys.status import APPLIED, NO_OP


@dataclass(frozen=True)
class StageNodeInputAction(SkillAction):
    """Select the unchanged input and native command's container asset directory.

    :param node_input: Caller-selected flags or exact native file bytes.
    :param asset_directory: Absolute directory passed to native oc --dir.

    .. attribute :: node_input
        :type: NativeNodeInput

        Unchanged native input bound by the confirmed plan.

    .. attribute :: asset_directory
        :type: PurePosixPath

        Container directory where oc discovers its native files.
    """

    node_input: NativeNodeInput
    asset_directory: PurePosixPath

    def __post_init__(self) -> None:
        """Require a container-side asset directory before any provider write.

        :raises ValueError: The directory is not absolute.
        """
        if not self.asset_directory.is_absolute():
            raise ValueError("node_image_asset_directory_must_be_absolute")


@dataclass
class NodeInputState(ToolPodState):
    """Retain native input identities separately from generic tool dependencies.

    .. attribute :: staged_inputs
        :type: MutableSequence[StagedPodFile]

        Staged native files and explicit ownership in successful staging order.
    """

    staged_inputs: MutableSequence[StagedPodFile] = field(default_factory=list)


class StageNodeInput(ToolPodK8SCallback[StageNodeInputAction]):
    """Stage exact native files without parsing or changing their contents."""

    @property
    def input_state(self) -> NodeInputState:
        """Use the concrete input state selected by the composed workflow.

        :returns: Shared state retaining file ownership.
        :raises TypeError: The workflow supplied another state type.
        """
        state = self.tool_state
        if not isinstance(state, NodeInputState):
            raise TypeError("node_input_state_required")
        return state

    def run(self) -> JSONObject:
        """Write each native file or reuse identical bytes without ownership.

        :returns: Exact input hashes, native paths and per-file ownership evidence.
        :raises KubernetesError: Staging or byte read-back fails.
        """  # noqa: DOC502 - The shared Pod file operation propagates native errors.
        state = self.input_state
        records: list[JSONObject] = []
        for source in self.action.node_input.files:
            staged = stage_pod_file(
                state, self.action.asset_directory / source.name, source.content
            )
            state.staged_inputs.append(staged)
            records.append(
                {
                    **staged.artifact.as_dict(),
                    "action": APPLIED if staged.created else NO_OP,
                    "owned": staged.created,
                }
            )
        return {
            "action": APPLIED if any(item["owned"] for item in records) else NO_OP,
            "verified": True,
            "native_input": self.action.node_input.evidence(),
            "files": records,
        }

    def cleanup(self) -> JSONObject:
        """Remove created native files while preserving reused or changed files.

        :returns: Removed and preserved exact paths.
        :raises KubernetesError: One or more owned files cannot be removed safely.
        """
        state = self.input_state
        removed: list[str] = []
        preserved: list[str] = []
        failures: list[str] = []
        retained: list[StagedPodFile] = []
        for staged in reversed(state.staged_inputs):
            if staged.artifact.path in state.retained_pod_files:
                preserved.append(staged.artifact.path)
                retained.append(staged)
                continue
            if not staged.created:
                preserved.append(staged.artifact.path)
                continue
            try:
                remove_pod_file(state, staged.artifact)
                removed.append(staged.artifact.path)
            except KubernetesError as exc:
                failures.append(str(exc))
        if failures:
            raise KubernetesError("node_input_cleanup_failed:" + ";".join(failures))
        state.staged_inputs[:] = reversed(retained)
        return {
            "action": APPLIED if removed else NO_OP,
            "verified": True,
            "removed": removed,
            "preserved": preserved,
        }
