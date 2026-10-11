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

| Responsibility | Existing owner |
| --- | --- |
| Callback hooks, context, ordering, result emission | [action.py](../ci-skills/lib/python/core/action.py) |
| Targets and credential selection | [target.py](../ci-skills/lib/python/core/target.py), [credentials.py](../ci-skills/lib/python/core/credentials.py) |
| Provider services and authentication | [gitlab_session.py](../ci-skills/lib/python/core/gitlab_session.py), [github.py](../ci-skills/lib/python/core/github.py), [kubernetes.py](../ci-skills/lib/python/core/kubernetes.py), [registry_mirror.py](../ci-skills/lib/python/core/registry_mirror.py) |
| Toolbox workflow inputs, planning and reports | [toolbox_actions.py](../ci-skills/lib/python/core/toolbox_actions.py) |
| Command declaration, help and discovery | [catalog.py](../ci-skills/lib/python/core/catalog.py), [cli.py](../ci-skills/lib/python/core/cli.py) |

Consume existing provider APIs and native clients. Extend their shared adapters when needed; do not copy transport,
credential lookup, JSON parsing, retry, or report code into an entrypoint.

## 2. Implement one atomic callback

`core.action` defines this hierarchy:

```text
AbstractCallback[Result]                 # four abstract hooks
└── Callback[Action, Result]              # defaults for all four hooks
    ├── GitlabCallback[Action, Result]
    ├── GitHubCallback[Action, Result]
    ├── K8SCallback[Action, Result]
    └── HarborCallback[Action, Result]
```

A direct `AbstractCallback` subclass must implement every abstract hook. Normally, subclass `Callback` or a provider
base and override only the hooks the operation needs. Unused hooks need no implementations.

| Hook | Signature | Implement when |
| --- | --- | --- |
| `collect()` | `collect(self) -> None` | Inputs, observed state or recovery state must be collected. |
| `run()` | `run(self) -> Result \| None` | The operation performs its read or confirmed write. |
| `update()` | `update(self) -> None` | The operation needs post-run work. |
| `cleanup()` | `cleanup(self) -> None` | The operation owns temporary resources or failure recovery. |

The executor binds one `ExecutionContext`, then runs `collect → run → update` for each callback in order. It assigns
`run()`'s return value to that callback's `result`. An exception or `complete == False` stops later callbacks. Cleanup
runs in reverse order for every entered callback, including one whose `collect()` failed; initialize cleanup state
before the first operation that can fail. Use `context.failed` to distinguish recovery from successful completion.

Authentication callbacks populate `context.services`. Put `GitLabAuth`, `GitHubAuth`, `K8SAuth`, or `HarborAuth` before
operations using the corresponding provider property. They reuse the selected target and credential resolver.

This illustrative read-only callback needs only `run()`. Its image input is explicit; registry access comes from
`HarborAuth`, not another credential argument:

```python
from dataclasses import dataclass
from typing import cast

from core.action import HarborCallback
from core.registry_mirror import HarborService


@dataclass(frozen=True)
class ImageAction:
    image: str


class InspectImage(HarborCallback[ImageAction, str]):
    def run(self) -> str:
        """Return the selected image's digest through the bound Harbor service."""
        if self.action is None:
            raise ValueError("image_action_required")
        return cast(HarborService, self.harbor).inspect(self.action.image)
```

For writes, retain the operation's existing plan/apply confirmation and independent read-back. Compare desired and
observed state before writing; use [status.py](../ci-skills/lib/python/core/status.py)'s `APPLIED` and `NO_OP` values.
Reuse named shared constants for fixed domain values. Obtain deployment values such as hosts, project IDs and runner
names from action inputs, configuration or discovery; obtain credentials through the existing target and credential
resolver.
Reuse [ResourceSet](../ci-skills/lib/python/core/resources.py) for Kubernetes ownership, apply/read-back and recovery,
and `TagSnapshot` in `registry_mirror.py` for Harbor tag restoration. Cleanup releases temporary resources and restores
failed changes; it does not delete a successfully requested persistent runner or image.

## 3. Compose a thin workflow

Pass callback instances to their consumers when the consumer supports an output dependency. For example,
`HarborPush(action=PushAction(repository, tag, build))` reads the earlier build callback's image result. Do not execute
`build.run()` while constructing the workflow. The executor accepts one callback or an ordered sequence through
`listify()`; it does not infer dependencies or reorder them.

The following composition is from [harbor_push.py](../ci-skills/bin/harbor_push.py), with imports shown. Authentication
establishes access, confirmation resolves the source image and checks the plan, and publication consumes that result:

```python
import _bootstrap  # installed bin/ import setup
from core.action import Executor
from core.registry_mirror import HarborAuth, HarborPush, PushAction
from core.toolbox_actions import ConfirmWorkflow, WorkflowAction, workflow_parser


def workflow(action: WorkflowAction) -> Executor:
    confirmation = ConfirmWorkflow(action=action)
    return Executor(
        context=action.context,
        callbacks=[
            HarborAuth(
                target=action.target,
                repository=action.repository,
                actions=("pull", "push"),
                timeout=action.args.timeout,
            ),
            confirmation,
            HarborPush(action=PushAction(action.repository, action.args.tag, confirmation)),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    args = workflow_parser("harbor_push").parse_args(argv)
    action = WorkflowAction(args)
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
4. Reuse the applicable result schema under [schemas/results](../schemas/results/). Extend the owning schema when the
   declared result changes. Keep the existing report envelope and shared emitter.
5. Add relevant `related` commands or reference pointers to the existing catalogue. `reference.py` derives its compact
   offerings from that catalogue; it does not need a second inventory or embedded reference documents. Regenerate
   `ci-skills/tools.json` from the repository root with
   `conda run -n ci-skills python tools/render_manifest.py`.

Use the shared diagnostic arguments from `core.cli.add_log_arguments()`:

```text
--log-level debug --log-format json --log-file PATH
```

These names are the command contract; do not add synonyms such as `--logging-level`. Shared executor, transport and
subprocess tracing records callback outcomes, API methods/status codes and command exit codes without response bodies
or credentials. Reuse it rather than logging provider payloads. Toolbox workflow reports expose `trace_artifact` when
both debug logging and a log file are selected; keep the trace separate from result JSON.

## 5. Verify the installed behavior

Follow the existing [validation surfaces](../README.md#validation):

- **Local static checks:** run `./bless.sh --staged` from the repository root before committing; it uses the shared
  lint checks.
- **GitHub tests:** extend the relevant [callback tests](../tests/python/test_callbacks.py),
  [workflow tests](../tests/python/test_toolbox_workflows.py) and [catalogue tests](../tests/python/test_catalog.py).
  Use fake services to prove the changed behavior and its concrete failure path, including applicable confirmation,
  read-back, no-op and recovery behavior. These test suites run on GitHub, not the laptop.
- **Live functionality:** install the candidate through [install.sh](../install.sh), then use the installed discovery,
  help/schema and command as a user would. Execute the affected operation against the selected real targets, read back
  its result, repeat applicable no-op checks and verify cleanup of smoke-owned resources. A dry-run is not live proof.

Keep the PR candidate identity, installed skill identity, result and cleanup evidence in the smoke report. Point to the
compact execution trace through `trace_artifact`; do not copy or parse the entire trace into another report. If code
changes the exercised behavior, reinstall and repeat that affected live check before claiming it is proved.

[Target selection](../README.md#target-selection) explains project and user `target.toml` locations for runtime endpoints
and credentials. This configuration is not repository delivery policy;
GitHub's mocked code checks do not require access to those private targets.
