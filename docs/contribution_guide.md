# Contribution guide

Add an atomic operation once, then compose it with existing callbacks into a small installed command.
The [CI11 callback specification](phases/CI11-TOOLS.md#callback-workflow-boilerplate) owns the design;
this guide shows its current Python implementation.

## Shared standards — read first

Read the exact standards revision and required contracts in [standards-binding.yaml](../standards-binding.yaml).
Use the project environment declared in [environment.yml](../environment.yml) for development commands.

## 1. Inspect and reuse

Start with the nearest installed command in [ci-skills/bin](../ci-skills/bin/) and its reusable operation in
[core](../ci-skills/lib/python/core/). Commands parse inputs and compose callbacks; provider logic belongs in `core`.

- **Callback hooks and ordered binding:**
  [callback.py](../ci-skills/lib/python/core/abstract/callback.py),
  [callbacks.py](../ci-skills/lib/python/core/abstract/callbacks.py).
- **Typed state, results and execution:**
  [skill_state.py](../ci-skills/lib/python/core/abstract/skill_state.py),
  [SkillExecutor](../ci-skills/lib/python/core/abstract/executor.py),
  [Executor and WorkflowExecutor](../ci-skills/lib/python/core/executor.py).
- **Targets and credential selection:**
  [target.py](../ci-skills/lib/python/core/target.py),
  [credentials.py](../ci-skills/lib/python/access/credentials.py).
- **Provider services and authentication:**
  [GitlabCallback](../ci-skills/lib/python/core/gitlab/callback.py),
  [gitlab_session.py](../ci-skills/lib/python/core/gitlab/gitlab_session.py),
  [github.py](../ci-skills/lib/python/core/github/github.py),
  [kubernetes.py](../ci-skills/lib/python/core/kubernetes/kubernetes.py),
  [registry_mirror.py](../ci-skills/lib/python/core/harbor/registry_mirror.py).
- **Runner inputs and provider operations:**
  [runner.py](../ci-skills/lib/python/core/runner.py),
  [GitHub registration](../ci-skills/lib/python/core/github/runner.py),
  [Kubernetes workload](../ci-skills/lib/python/core/kubernetes/runner.py).
- **Toolbox workflow inputs, planning and reports:**
  [toolbox_actions.py](../ci-skills/lib/python/core/toolbox_actions.py).
- **Command declaration, help and discovery:**
  [catalog.py](../ci-skills/lib/python/core/catalog.py),
  [cli.py](../ci-skills/lib/python/core/cli.py).

Consume existing provider APIs and native clients. Extend their shared adapters when needed; do not copy transport,
credential lookup, JSON parsing, retry, or report code into an entrypoint.

## 2. Implement one atomic callback

The shared interface uses `StateT = TypeVar("StateT", bound=SkillState)` and
`ActionT_co = TypeVar("ActionT_co", bound=SkillAction, covariant=True)`:

```text
AbstractCallback[StateT, ActionT_co]
└── Callback[StateT, ActionT_co]           # shared binding and lifecycle defaults
    ├── GitlabCallback[ActionT_co]
    ├── GitHubCallback[ActionT_co]
    ├── K8SCallback[ActionT_co]
    ├── HarborCallback[ActionT_co]
    └── LocalCallback[StateT, ActionT_co] # no external provider configuration
```

Every callback receives a concrete `SkillAction`, including reads and authentication. A direct `AbstractCallback`
subclass implements its full abstract interface. Normally, subclass the provider base: it owns configuration,
credentials and shared utilities, while each operation overrides only the lifecycle hooks it needs.

| Required provider interface | Purpose |
| --- | --- |
| `plugin_type: SkillPluginType` | Enum identity from [skill_plugin_type.py](../ci-skills/lib/python/core/abstract/skill_plugin_type.py). |
| `read_config(self) -> SkillResult` | Resolve this provider's configuration and retain values in shared state. |
| `is_configured(self) -> bool` | Check this callback's requirements against that retained configuration. |

The executor owns one concrete state. `WorkflowExecutor` binds the supplied context; `Executor` creates a fresh
`ExecutionContext`. Both pass it to `Callbacks.bind()`, which binds each callback to that same state in one ordered pass.
The first callback for a plugin reads its configuration; every callback checks readiness.
Successful configuration is cached in `state.plugin_ready`, an initially empty mapping keyed by `SkillPluginType`.
Later callbacks of the same type reuse it. Missing required configuration rejects construction before any lifecycle
hook runs. Unused provider entries in `Target` remain `Optional`: a GitLab-only command does not require Harbor or K8s.

| Hook | Signature | Implement when |
| --- | --- | --- |
| `collect()` | `collect(self) -> SkillResult` | Inputs, observed state or recovery state must be collected. |
| `run()` | `run(self) -> SkillResult` | The operation performs its read or confirmed write. |
| `update()` | `update(self) -> SkillResult` | The operation needs post-run work. |
| `cleanup()` | `cleanup(self) -> SkillResult` | The operation owns temporary resources or failure recovery. |

All hooks return result mappings; `{}` is an empty result. The executor runs `collect → run → update` for each callback
in order. `state.results[callback]` retains its run result, exposed by `callback.result`; `state.result` holds the latest
successful hook result. An exception or `complete == False` stops later callbacks. Cleanup runs in reverse order for
every entered callback, including one whose `collect()` failed; initialize cleanup state before the first operation
that can fail. Use `state.failed` for recovery. Reuse the existing state serialization methods unchanged.

Put `GitLabAuth`, `GitHubAuth`, `K8SAuth`, or `HarborAuth` before operations using that provider. Configuration is checked
at construction; authentication hooks then verify live access and retain the service in shared state.

This read-only callback inherits Harbor configuration and needs only `run()`. Its image input is explicit; registry
access comes from the shared provider service:

```python
from dataclasses import dataclass

from lib.python.core.abstract.skill_action import SkillAction
from lib.python.core.harbor.registry_mirror import HarborCallback
from lib.python.core.json_types import JSONObject


@dataclass(frozen=True)
class ImageAction(SkillAction):
    image: str


class InspectImage(HarborCallback[ImageAction]):
    def run(self) -> JSONObject:
        """Return the selected image and its digest from the bound Harbor service."""
        return {
            "image": self.action.image,
            "digest": self.harbor.inspect(self.action.image),
        }
```

For writes, retain the operation's existing plan/apply confirmation and independent read-back. Compare desired and
observed state before writing; use [status.py](../ci-skills/lib/python/sys/status.py)'s `APPLIED` and `NO_OP` values.
Reuse named shared constants for fixed domain values. Obtain deployment values such as hosts, project IDs and runner
names from action inputs, configuration or discovery; obtain credentials through the existing target and credential
resolver.
Reuse [ResourceSet](../ci-skills/lib/python/core/kubernetes/resources.py) for Kubernetes ownership, apply/read-back
and recovery,
and `TagSnapshot` in `registry_mirror.py` for Harbor tag restoration. Cleanup releases temporary resources and restores
failed changes; it does not delete a successfully requested persistent runner or image.

## 3. Compose a thin workflow

Declare dependencies through callback order and concrete state fields. For example, `ConfirmWorkflow` assigns
`state.source_image`; the later `HarborPush(PushAction(repository, tag))` consumes it. Do not execute a producer's `run()`
while constructing the workflow. `Callbacks` normalizes one callback or an ordered iterable through `listify()`;
it does not infer dependencies or reorder them.

The following composition is abridged from [harbor_push.py](../ci-skills/bin/harbor_push.py).
Authentication establishes access, confirmation resolves the source image and checks the plan,
and confirmed publication consumes that shared state:

```python
from collections.abc import Sequence
from typing import Optional

import _bootstrap  # installed bin/ import setup
from lib.python.core.abstract.callbacks import Callbacks
from lib.python.core.executor import WorkflowExecutor
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.harbor.registry_mirror import HarborAuth, HarborPush, PushAction
from lib.python.core.toolbox_actions import ConfirmWorkflow, WorkflowAction, workflow_parser


def workflow(action: WorkflowAction) -> WorkflowExecutor[DeliveryState]:
    callbacks = [
        HarborAuth(
            action.target, action.repository, ("pull", "push"), action.args.timeout
        ),
        ConfirmWorkflow(action),
    ]
    if action.args.apply:
        callbacks.append(HarborPush(PushAction(action.repository, action.args.tag)))
    return WorkflowExecutor(context=action.context, callbacks=Callbacks(callbacks))


def main(argv: Optional[Sequence[str]] = None) -> int:
    action = WorkflowAction(workflow_parser("harbor_push").parse_args(argv))
    return action.execute(lambda: workflow(action))


if __name__ == "__main__":
    raise SystemExit(main())
```

`WorkflowAction` is the existing toolbox/registry/runner adapter: it prepares inputs before constructing the executor,
handles dry-run and confirmed execution, and emits the shared report. `workflow_parser()` requires a registered
catalogue kind. A new kind also needs compatible preparation and reporting; renaming this example alone is insufficient.
For other domains, reuse their action adapter over the same executor. See
[create_selfhosted_runner.py](../ci-skills/bin/create_selfhosted_runner.py) for a composition spanning three providers.

## 4. Expose the command contract

Make the installed command usable from help and its schema without requiring the caller to read implementation code.

1. Add the entrypoint under `ci-skills/bin/` and its declaration in `core/catalog.py`. Declare the short purpose,
   required authorities/tools, accepted options, verbs, side effects and paired result schema. Describe a combination
   in one sentence, such as “Build, publish, deploy and register a toolbox-backed GitHub runner.”
2. Build the parser with the existing shared parser or applicable workflow parser. Preserve `--help`, `--describe`,
   `--json` and `--yaml`. Reuse target/credential selection; do not add password/token flags or a second lookup path.
3. For structured inputs, reuse the installed schema in `ci-skills/schemas/` and
   [validate_specification()](../ci-skills/lib/python/core/schema.py). The toolbox example is
   [toolbox.schema.json](../ci-skills/schemas/toolbox.schema.json). Help must point to the accepted input schema.
   A new input shape needs its own paired schema only when an existing one cannot describe it.
4. Reuse the applicable [installed result schema](../ci-skills/schemas/results/). Extend the owning schema when the
   declared result changes. Keep the existing report envelope and shared emitter.
5. Add relevant `related` commands or reference pointers to the existing catalogue. `reference.py` derives its compact
   offerings from that catalogue; it does not need a second inventory or embedded reference documents. Regenerate
   `ci-skills/tools.json` from the repository root with
   `conda run -n ci-skills python tools/render_manifest.py`.

Use the shared diagnostic arguments from
[core.logging.add_log_arguments()](../ci-skills/lib/python/core/logging.py):

```text
--log-level debug --log-format json --log-file PATH
```

These names are the command contract; do not add synonyms such as `--logging-level`. Shared executor, transport and
subprocess tracing records callback outcomes, API methods/status codes and command exit codes without response bodies
or credentials. Reuse it rather than logging provider payloads. Toolbox workflow reports expose `trace_artifact` when
both debug logging and a log file are selected; keep the trace separate from result JSON.

## 5. Verify the installed behavior

Follow the existing [validation surfaces](../README.md#validation) and
[installer procedure](../README.md#install-and-first-run). Extend the relevant
[callback tests](../tests/python/test_callbacks.py), [workflow tests](../tests/python/test_toolbox_workflows.py) and
[catalogue tests](../tests/python/test_catalog.py) with fake services for the changed behavior and its concrete failure
path.

After QA approves the candidate, install it and use discovery and help as a consumer would. Execute the affected live
operations, read back their results, repeat applicable no-op checks, and verify cleanup of smoke-owned resources. Keep
the PR candidate identity and installed skill identity in the smoke report. Point to the compact execution trace through
`trace_artifact`; do not copy or parse the entire trace into another report.

The shared [provenance calculation](../ci-skills/references/access.md#package-and-functional-identity) records
both the package checksum and the functional checksum of `bin/`, `lib/`, and `schemas/`. GitHub compares the
recorded functional checksum with the candidate's checksum. If live use exposes a code defect, fix it, obtain
review, then collect live evidence for the corrected candidate. Documentation and test edits do not require
live reruns when the functional checksum is unchanged.
