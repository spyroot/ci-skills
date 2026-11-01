"""Behavioral coverage for runtime-selected smoke and test targets.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Final

import pytest
from conftest import REPO_ROOT, import_script_module, load_module
from jsonschema import Draft202012Validator, ValidationError

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONValue

PYTHON_SOURCE: Final[str] = """import subprocess

def smoke(target, token_file):
    subprocess.run(
        ["glab", "api", "--hostname", target.gitlab.host,
         f"projects/{target.gitlab.project}/pipelines"], check=True
    )
    subprocess.run(
        ["gh", "run", "list", "--repo", target.github.repository], check=True
    )
    subprocess.run(
        ["kubectl", "--context", target.kubernetes.context, "get", "pods"], check=True
    )
    subprocess.run(
        ["ci-api", "check", "--provider", "gitlab", "--token-file", str(token_file)],
        check=True
    )
    subprocess.run(["podman", "push", target.harbor.destination], check=True)
"""
SHELL_SOURCE: Final[str] = """#!/usr/bin/env bash
set -euo pipefail
glab api --hostname "$GITLAB_HOST" "projects/$GITLAB_PROJECT/pipelines"
gh run list --repo "$GITHUB_REPOSITORY"
kubectl --context "$KUBE_CONTEXT" get pods
ci-api check --provider gitlab --token-file "$TOKEN_FILE"
podman push "$IMAGE_REF"
"""


@pytest.fixture(scope="module")
def portability() -> ModuleType:
    """Load the shared scanner through the existing repository import helpers.

    :returns: Portability module under test, without running a scan or command.
    """
    import_script_module("lib.python.core.endpoints")
    return load_module(
        "proposed_portability_gate", REPO_ROOT / "tools/skillkit/portability_gate.py"
    )


@pytest.mark.parametrize(
    ("before", "after", "selector"),
    (
        ("target.gitlab.host", '"gitlab.delivery.corp"', "host"),
        ("target.gitlab.host", '"gitlab." "delivery.corp"', "host"),
        ("target.gitlab.host", '"gitlab." + "delivery.corp"', "host"),
        (
            "target.gitlab.host",
            'target.gitlab.host or "gitlab.delivery.corp"',
            "host",
        ),
        ("target.github.repository", '"operator/fixed-consumer"', "repository"),
        (
            'f"projects/{target.gitlab.project}/pipelines"',
            '"projects/operator%2Ffixed-consumer/pipelines"',
            "project",
        ),
        ("target.kubernetes.context", '"production-admin"', "cluster"),
        (
            "str(token_file)",
            '"/home/operator/.ci-skills/gitlab.token.env"',
            "credential",
        ),
        (
            "target.harbor.destination",
            '"registry.delivery.corp/operator/fixed-image:candidate"',
            "registry",
        ),
    ),
    ids=(
        "fixed-host",
        "adjacent-python-literals",
        "python-literal-concatenation",
        "python-literal-fallback",
        "fixed-repository",
        "fixed-project-endpoint",
        "fixed-cluster-context",
        "fixed-credential-location",
        "fixed-publication-destination",
    ),
)
def test_python_operational_selectors_reject_one_fixed_target(
    portability: ModuleType, before: str, after: str, selector: str
) -> None:
    """Reject one embedded deployment selector on an immutable runtime baseline.

    :param portability: Shared scanner module under test.
    :param before: Unique runtime-selected expression in the baseline.
    :param after: Literal expression replacing only that selected invariant.
    :param selector: Target category the resulting finding must identify.
    :returns: None; assertions verify real syntax analysis and redacted findings.
    """
    assert PYTHON_SOURCE.count(before) == 1
    source = PYTHON_SOURCE.replace(before, after, 1)

    findings = portability.scan_source("tests/python/test_live_smoke.py", source)

    assert selector in {finding.selector for finding in findings}
    assert all(
        finding.source == "tests/python/test_live_smoke.py" for finding in findings
    )
    assert all(finding.line > 0 and finding.rule for finding in findings)


@pytest.mark.parametrize(
    ("before", "after", "selector"),
    (
        ('"$GITLAB_HOST"', '"gitlab.delivery.corp"', "host"),
        ('"$GITLAB_HOST"', '"${GITLAB_HOST:-gitlab.delivery.corp}"', "host"),
        ('"$GITHUB_REPOSITORY"', '"operator/fixed-consumer"', "repository"),
        (
            '"projects/$GITLAB_PROJECT/pipelines"',
            '"projects/operator%2Ffixed-consumer/pipelines"',
            "project",
        ),
        (
            '"projects/$GITLAB_PROJECT/pipelines"',
            '"projects/${GITLAB_PROJECT:-operator%2Ffixed-consumer}/pipelines"',
            "project",
        ),
        ('"$KUBE_CONTEXT"', '"production-admin"', "cluster"),
        ('"$TOKEN_FILE"', '"/home/operator/.ci-skills/gitlab.token.env"', "credential"),
        (
            '"$IMAGE_REF"',
            '"registry.delivery.corp/operator/fixed-image:candidate"',
            "registry",
        ),
    ),
    ids=(
        "fixed-host",
        "shell-host-fallback",
        "fixed-repository",
        "fixed-project-endpoint",
        "shell-project-fallback",
        "fixed-cluster",
        "fixed-credential",
        "fixed-publication",
    ),
)
def test_shell_operational_selectors_reject_one_fixed_target(
    portability: ModuleType, before: str, after: str, selector: str
) -> None:
    """Reject literal and fallback selectors without requiring live execution.

    :param portability: Shared scanner module under test.
    :param before: Unique runtime-selected fragment in the immutable baseline.
    :param after: Replacement breaking one target-selection invariant.
    :param selector: Expected target category reported by syntax analysis.
    :returns: None; assertions verify the shell scanner reports the fixed selector.
    """
    assert SHELL_SOURCE.count(before) == 1
    source = SHELL_SOURCE.replace(before, after, 1)

    findings = portability.scan_source("tests/smoke_live.sh", source)

    assert selector in {finding.selector for finding in findings}


@pytest.mark.parametrize(
    ("source", "body"),
    (
        ("tests/python/test_smoke.py", PYTHON_SOURCE),
        ("tests/smoke_live.sh", SHELL_SOURCE),
        (
            "tests/python/test_fixture.py",
            (
                'import subprocess\nsubprocess.run(["glab", "api", "--hostname", '
                '"gitlab.example.test", "projects/fixture%2Frepo/pipelines"], check=True)\n'
            ),
        ),
        (
            "tests/python/test_fixture.py",
            (
                'import subprocess\nsubprocess.run(["gh", "api", "--hostname", '
                '"github.example.test", "repos/fixture/repo/actions/runs"], check=True)\n'
            ),
        ),
        (
            "tests/python/test_fixture.py",
            (
                'import subprocess\nsubprocess.run(["kubectl", "--server", '
                '"https://api.cluster.example.test", "--context", "fixture-context", '
                '"get", "pods"], check=True)\n'
            ),
        ),
        (
            "tests/python/test_fixture.py",
            (
                "import subprocess\ndef test_fixture(tmp_path):\n"
                '    token_file = tmp_path / "fixture.token"\n'
                '    subprocess.run(["ci-api", "check", "--provider", "gitlab", '
                '"--token-file", str(token_file)], check=True)\n'
            ),
        ),
        (
            "tests/python/test_redaction.py",
            (
                "def test_redaction():\n"
                '    value = {"url": "https://gitlab.delivery.corp", '
                '"project": "operator/fixed-consumer"}\n'
                '    assert redact_tree(value)["project"] == "operator/fixed-consumer"\n'
            ),
        ),
        (
            "tests/python/test_protocol.py",
            (
                'ACCEPT = "application/vnd.github+json"\nAPI_VERSION = "2022-11-28"\n'
                'BASE_IMAGE = "ubuntu:24.04"\n'
            ),
        ),
        (
            "tests/smoke_fixture.sh",
            (
                "glab api --hostname gitlab.example.test "
                "projects/fixture%2Frepo/pipelines\n"
            ),
        ),
        (
            "tests/smoke_fixture.sh",
            "podman push registry.example.test/fixture/image:candidate\n",
        ),
    ),
    ids=(
        "python-runtime",
        "shell-runtime",
        "own-synthetic-gitlab-project",
        "own-synthetic-github-repository",
        "own-synthetic-kubernetes-context",
        "temporary-credential",
        "assertion-and-redaction-data",
        "protocol-and-base-image-constants",
        "shell-synthetic-project",
        "shell-synthetic-registry",
    ),
)
def test_portability_permits_runtime_inputs_and_scoped_fixture_values(
    portability: ModuleType, source: str, body: str
) -> None:
    """Keep runtime values, synthetic fixtures, and inert data usable in tests.

    :param portability: Shared scanner module under test.
    :param source: Operational or fixture source path used for classification.
    :param body: Valid source selecting runtime inputs or its own synthetic target.
    :returns: None; assertions verify no portability finding is emitted.
    """
    assert portability.scan_source(source, body) == ()


def test_an_unrelated_synthetic_marker_does_not_exempt_an_operational_target(
    portability: ModuleType,
) -> None:
    """Reject a real fixed target even beside unrelated mock or synthetic data.

    :param portability: Shared scanner module under test.
    :returns: None; assertions prove exemption is scoped to the selected operation.
    """
    source = 'MOCK_ORIGIN = "https://unused.example.test"\n' + PYTHON_SOURCE.replace(
        "target.gitlab.host", '"gitlab.delivery.corp"', 1
    )

    findings = portability.scan_source("tests/python/test_mocked_smoke.py", source)

    assert "host" in {finding.selector for finding in findings}


@pytest.mark.parametrize(
    ("name", "body"),
    (
        (
            "tests/acceptance/live.py",
            PYTHON_SOURCE.replace("target.gitlab.host", '"gitlab.delivery.corp"', 1),
        ),
        (
            "tests/acceptance/live.sh",
            SHELL_SOURCE.replace('"$GITLAB_HOST"', '"gitlab.delivery.corp"', 1),
        ),
        (
            "tests/acceptance/live-smoke",
            SHELL_SOURCE.replace('"$GITLAB_HOST"', '"gitlab.delivery.corp"', 1),
        ),
    ),
    ids=("python", "shell", "extensionless-shell"),
)
def test_executable_acceptance_sources_are_scanned_and_values_are_redacted(
    portability: ModuleType, tmp_path: Path, name: str, body: str
) -> None:
    """Scan executable acceptance sources while exposing only finding identities.

    :param portability: Shared gate implementation under test.
    :param tmp_path: Root containing one operational source file.
    :param name: Selected acceptance source path, including extensionless scripts.
    :param body: Source with one embedded host invariant violation.
    :returns: None; assertions verify selection, rejection, and redacted reporting.
    """
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o700)

    result = portability.PortabilityGate(tmp_path).run((name,))

    assert result.exit_code != 0
    assert result.findings
    assert name in result.checked_files
    record = result.record()
    assert record["status"] != "PASS"
    assert "gitlab.delivery.corp" not in json.dumps(record)


def test_json_receipts_remain_data_in_the_selected_acceptance_inventory(
    portability: ModuleType, tmp_path: Path
) -> None:
    """Allow a recorded target in receipt data without exempting executable files.

    :param portability: Shared gate implementation under test.
    :param tmp_path: Root containing synthetic receipt data only.
    :returns: None; assertions verify data is excluded from operational scanning.
    """
    name = "tests/acceptance/receipt.json"
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    path.write_text('{"target":"https://gitlab.delivery.corp"}', encoding="utf-8")

    result = portability.PortabilityGate(tmp_path).run((name,))

    assert result.exit_code == 0
    assert not result.findings


@pytest.mark.parametrize("failure", ("missing", "invalid-python", "invalid-utf8"))
def test_selected_source_read_or_parse_failures_never_pass(
    portability: ModuleType, tmp_path: Path, failure: str
) -> None:
    """Fail closed when one selected operational source cannot be inspected.

    :param portability: Shared gate implementation under test.
    :param tmp_path: Root containing the isolated baseline file when applicable.
    :param failure: Single source-read or syntax invariant invalidated per case.
    :returns: None; assertions verify the gate cannot report success.
    """
    name = "tests/smoke_live.py"
    path = tmp_path / name
    path.parent.mkdir()
    path.write_text(PYTHON_SOURCE, encoding="utf-8")
    if failure == "missing":
        path.unlink()
    elif failure == "invalid-python":
        path.write_text("def smoke(\n", encoding="utf-8")
    else:
        path.write_bytes(b"\xff\n")

    result = portability.PortabilityGate(tmp_path).run((name,))

    assert result.exit_code != 0
    assert result.record()["status"] != "PASS"


@pytest.fixture(scope="module")
def portability_schema() -> Draft202012Validator:
    """Load and validate the gate's repository-controlled output schema.

    :returns: Draft 2020-12 validator for portability result records.
    """
    schema = json.loads(
        (REPO_ROOT / "schemas/ci_skills_portability.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture(scope="module")
def portability_result_baseline(portability: ModuleType) -> str:
    """Serialize one actual failure record as an immutable schema-test baseline.

    :param portability: Shared producer whose record shape is under validation.
    :returns: JSON text copied independently by each schema rejection case.
    """
    name = "tests/smoke_live.py"
    findings = portability.scan_source(
        name, PYTHON_SOURCE.replace("target.gitlab.host", '"gitlab.delivery.corp"', 1)
    )
    assert findings
    return json.dumps(
        portability.GateResult((name,), findings).record(), sort_keys=True
    )


@pytest.mark.parametrize(
    "state", ("pass", "fixed-selector", "source-error", "no-sources")
)
def test_actual_portability_results_satisfy_the_output_schema(
    portability: ModuleType,
    portability_schema: Draft202012Validator,
    tmp_path: Path,
    state: str,
) -> None:
    """Validate success, rejected target, and inspection-error producer records.

    :param portability: Shared gate implementation producing the result record.
    :param portability_schema: Validated repository-controlled output schema.
    :param tmp_path: Isolated root containing the selected operational source.
    :param state: Producer outcome whose actual record is schema-validated.
    :returns: None; schema validation and assertions verify the emitted contract.
    """
    name = "tests/smoke_live.py"
    if state not in {"source-error", "no-sources"}:
        path = tmp_path / name
        path.parent.mkdir()
        source = PYTHON_SOURCE
        if state == "fixed-selector":
            source = source.replace("target.gitlab.host", '"gitlab.delivery.corp"', 1)
        path.write_text(source, encoding="utf-8")
    paths = () if state == "no-sources" else (name,)

    record = portability.PortabilityGate(tmp_path).run(paths).record()

    portability_schema.validate(record)
    assert record["status"] == (
        "FAIL" if state in {"fixed-selector", "source-error"} else "PASS"
    )


@pytest.mark.parametrize(
    ("section", "field", "value", "remove"),
    (
        ("result", "status", None, True),
        ("result", "extra", True, False),
        ("result", "kind", "another_gate", False),
        ("result", "schema_version", "2.0", False),
        ("result", "status", "PARTIAL", False),
        ("result", "checked_files", "tests/smoke_live.py", False),
        ("result", "checked_files", [""], False),
        (
            "result",
            "checked_files",
            ["tests/smoke_live.py", "tests/smoke_live.py"],
            False,
        ),
        ("finding", "source", None, True),
        ("finding", "source", "", False),
        ("finding", "extra", True, False),
        ("finding", "line", "4", False),
        ("finding", "line", -1, False),
        ("finding", "line", True, False),
        ("finding", "rule", "unknown-rule", False),
        ("finding", "selector", "unknown-selector", False),
        ("result", "status", "PASS", False),
        ("result", "findings", [], False),
    ),
    ids=(
        "missing-required-status",
        "unknown-envelope-field",
        "wrong-kind",
        "unsupported-version",
        "unsupported-status",
        "checked-files-is-not-array",
        "empty-checked-file",
        "duplicate-checked-file",
        "missing-finding-source",
        "empty-finding-source",
        "unknown-finding-field",
        "line-is-not-integer",
        "negative-line",
        "boolean-is-not-line",
        "unknown-rule",
        "unknown-selector",
        "pass-cannot-have-findings",
        "fail-needs-a-finding",
    ),
)
def test_portability_schema_rejects_one_invalid_result_invariant(
    portability_schema: Draft202012Validator,
    portability_result_baseline: str,
    section: str,
    field: str,
    value: JSONValue,
    remove: bool,
) -> None:
    """Reject one malformed field on an isolated copy of the valid producer result.

    :param portability_schema: Validated repository-controlled output schema.
    :param portability_result_baseline: Shared immutable actual producer record.
    :param section: Envelope or first finding containing the modified field.
    :param field: Single required, additional, or constrained field under test.
    :param value: Invalid replacement, unused when removing the selected field.
    :param remove: Whether this case deletes rather than replaces the selected field.
    :returns: None; assertions verify baseline validity and schema rejection.
    """
    record = json.loads(portability_result_baseline)
    portability_schema.validate(record)
    selected = record if section == "result" else record["findings"][0]
    if remove:
        del selected[field]
    else:
        selected[field] = value

    with pytest.raises(ValidationError):
        portability_schema.validate(record)


TARGET_HEREDOC: Final[str] = """#!/usr/bin/env bash
set -euo pipefail
target_file="$(mktemp)"
cat >"$target_file" <<TOML
[github]
host = "$GITHUB_HOST"
repository = "$GITHUB_REPOSITORY"
[gitlab]
url = "$GITLAB_URL"
project = "$GITLAB_PROJECT"
token_file = "$TOKEN_FILE"
[kubernetes]
context = "$KUBE_CONTEXT"
server = "$KUBERNETES_SERVER"
[harbor]
url = "$HARBOR_URL"
project = "$HARBOR_PROJECT"
TOML
CI_SKILLS_TARGET="$target_file" ci-api check --provider gitlab
"""
EXECUTED_HEREDOC: Final[str] = (
    "kubectl exec \"$POD\" -- bash -s <<'SCRIPT'\n" + SHELL_SOURCE + "SCRIPT\n"
)


@pytest.mark.parametrize(
    ("form", "before", "after", "selector"),
    (
        ("target", '"$GITLAB_URL"', '"https://gitlab.delivery.corp"', "host"),
        ("target", '"$GITHUB_REPOSITORY"', '"operator/fixed-consumer"', "repository"),
        ("target", '"$GITLAB_PROJECT"', '"operator/fixed-project"', "project"),
        ("target", '"$KUBE_CONTEXT"', '"production-admin"', "cluster"),
        (
            "target",
            '"$TOKEN_FILE"',
            '"/home/operator/.ci-skills/gitlab.token.env"',
            "credential",
        ),
        ("target", '"$HARBOR_URL"', '"https://registry.delivery.corp"', "registry"),
        ("executed", '"$GITLAB_HOST"', '"gitlab.delivery.corp"', "host"),
        ("executed", '"$GITHUB_REPOSITORY"', '"operator/fixed-consumer"', "repository"),
        (
            "executed",
            '"projects/$GITLAB_PROJECT/pipelines"',
            '"projects/operator%2Ffixed-project/pipelines"',
            "project",
        ),
        ("executed", '"$KUBE_CONTEXT"', '"production-admin"', "cluster"),
        (
            "executed",
            '"$TOKEN_FILE"',
            '"/home/operator/.ci-skills/gitlab.token.env"',
            "credential",
        ),
        (
            "executed",
            '"$IMAGE_REF"',
            '"registry.delivery.corp/operator/fixed-image:candidate"',
            "registry",
        ),
    ),
    ids=(
        "consumed-target-host",
        "consumed-target-repository",
        "consumed-target-project",
        "consumed-target-cluster",
        "consumed-target-credential",
        "consumed-target-registry",
        "executed-script-host",
        "executed-script-repository",
        "executed-script-project",
        "executed-script-cluster",
        "executed-script-credential",
        "executed-script-registry",
    ),
)
def test_operational_heredocs_reject_one_embedded_target_selector(
    portability: ModuleType, form: str, before: str, after: str, selector: str
) -> None:
    """Inspect consumed target files and explicitly executed stdin shell bodies.

    :param portability: Shared scanner module under test.
    :param form: Immutable consumed-target or executed-shell source baseline.
    :param before: Unique runtime expression defining the selected invariant.
    :param after: Fixed selector replacing only that expression in this case.
    :param selector: Expected target category inside the operational heredoc.
    :returns: None; assertions verify the heredoc is inspected as operational input.
    """
    baseline = TARGET_HEREDOC if form == "target" else EXECUTED_HEREDOC
    assert baseline.count(before) == 1
    source = baseline.replace(before, after, 1)

    findings = portability.scan_source("tests/smoke_heredoc.sh", source)

    assert selector in {finding.selector for finding in findings}
    assert all(finding.source == "tests/smoke_heredoc.sh" for finding in findings)


@pytest.mark.parametrize(
    "source",
    (
        TARGET_HEREDOC,
        EXECUTED_HEREDOC,
        """cat >"$receipt" <<'JSON'
{"target":"https://gitlab.delivery.corp","project":"operator/fixed-project"}
JSON
""",
        """cat >"$notes" <<'EXAMPLE'
glab api --hostname gitlab.delivery.corp projects/operator%2Ffixed-project/pipelines
EXAMPLE
""",
    ),
    ids=("runtime-target", "runtime-executed-script", "receipt-data", "stored-example"),
)
def test_runtime_and_inert_data_heredocs_remain_portable(
    portability: ModuleType, source: str
) -> None:
    """Permit runtime selectors and heredoc data that no execution sink consumes.

    :param portability: Shared scanner module under test.
    :param source: Runtime operation or inert receipt/example heredoc source.
    :returns: None; assertions keep data distinct from executed/configured input.
    """
    assert portability.scan_source("tests/smoke_heredoc.sh", source) == ()


CONSTRUCTOR_SOURCE: Final[
    str
] = """from lib.python.core.target import Target, KubernetesTarget
import requests
import subprocess

selected = Target(
    github=None,
    gitlab=None,
    kubernetes=KubernetesTarget(
        context="fixture-context",
        server="https://api.cluster.example.test",
        kubeconfig=None,
    ),
    active_surfaces=("kubernetes",),
)
"""


@pytest.mark.parametrize(
    "source",
    (
        """from lib.python.core.target import Target, KubernetesTarget
from lib.python.access.kubernetes import kubectl_argv

def test_argv(base):
    selected = Target(
        github=None,
        gitlab=None,
        kubernetes=KubernetesTarget(
            context="chosen-context", server=base.server, kubeconfig=None
        ),
        active_surfaces=("kubernetes",),
    )
    assert kubectl_argv(selected, "get", "pods") == [
        "kubectl", "--context", "chosen-context", "get", "pods"
    ]
""",
        """from lib.python.core.target import select_gitlab_reference

def test_reference(target):
    assert select_gitlab_reference(target, project="fixture/fixed") == (
        "project", "fixture/fixed"
    )
""",
        """from lib.python.access.gitlab_credentials import bind_gitlab_session

def test_binding(target):
    session = bind_gitlab_session(
        target,
        target_kind="project",
        target_reference="fixture/fixed",
        target_source="fixture",
    )
    assert session.target_reference == "fixture/fixed"
""",
    ),
    ids=("pure-kubectl-argv", "pure-reference-selection", "session-binding-only"),
)
def test_target_construction_and_pure_helpers_are_not_provider_execution(
    portability: ModuleType, source: str
) -> None:
    """Keep inert target fixtures and pure argument/session helpers usable.

    :param portability: Shared scanner module under test.
    :param source: Target construction used only by an assertion or pure helper.
    :returns: None; assertions distinguish data preparation from provider execution.
    """
    assert portability.scan_source("tests/python/test_target_helpers.py", source) == ()


@pytest.mark.parametrize(
    "consumer",
    (
        (
            'subprocess.run(["kubectl", "--server", selected.kubernetes.server, '
            '"--context", selected.kubernetes.context, "get", "pods"], check=True)\n'
        ),
        'requests.get(selected.kubernetes.server + "/version")\n',
    ),
    ids=("subprocess-argv", "http-url"),
)
@pytest.mark.parametrize(
    ("origin", "rejected"),
    (
        ("https://api.cluster.example.test", False),
        ("https://api.cluster.delivery.corp", True),
    ),
    ids=("own-synthetic-authority", "fixed-operational-authority"),
)
def test_constructed_target_fields_are_checked_when_execution_consumes_them(
    portability: ModuleType, consumer: str, origin: str, rejected: bool
) -> None:
    """Carry target fields into execution without treating construction as a call.

    :param portability: Shared scanner module under test.
    :param consumer: Real process or HTTP sink consuming the constructed field.
    :param origin: Single server selection replacing the immutable baseline value.
    :param rejected: Whether that authority is a fixed deployment selector.
    :returns: None; assertions preserve synthetic fixtures and reject real targets.
    """
    source = CONSTRUCTOR_SOURCE.replace("https://api.cluster.example.test", origin, 1)

    findings = portability.scan_source(
        "tests/python/test_target_consumption.py", source + consumer
    )

    if rejected:
        assert "host" in {finding.selector for finding in findings}
    else:
        assert findings == ()


@pytest.mark.parametrize(
    "wrapper",
    (
        "if {command}; then :; fi",
        "if false; then :; elif {command}; then :; fi",
        "while {command}; do break; done",
        "until {command}; do break; done",
        "! {command}",
        "if ! {command}; then :; fi",
    ),
    ids=("if", "elif", "while", "until", "negated", "negated-if"),
)
@pytest.mark.parametrize("fixed_host", (False, True), ids=("runtime", "fixed-host"))
def test_shell_control_prefixes_preserve_operational_selector_checks(
    portability: ModuleType, wrapper: str, fixed_host: bool
) -> None:
    """Inspect supported conditional and negated commands through one runtime baseline.

    :param portability: Shared scanner module under test.
    :param wrapper: Supported shell control form surrounding the existing invocation.
    :param fixed_host: Whether only the selected host becomes a fixed deployment value.
    :returns: None; assertions preserve runtime inputs and reject the fixed host.
    """
    command = SHELL_SOURCE.splitlines()[2]
    assert SHELL_SOURCE.count(command) == 1
    source = SHELL_SOURCE.replace(command, wrapper.format(command=command), 1)
    if fixed_host:
        source = source.replace('"$GITLAB_HOST"', '"gitlab.delivery.corp"', 1)

    findings = portability.scan_source("tests/smoke_control_flow.sh", source)

    if fixed_host:
        assert "host" in {finding.selector for finding in findings}
    else:
        assert findings == ()


@pytest.mark.parametrize("form", ("statement", "expression"))
@pytest.mark.parametrize(
    "candidate_first", (False, True), ids=("candidate-second", "candidate-first")
)
@pytest.mark.parametrize("fixed_host", (False, True), ids=("runtime", "fixed-host"))
def test_python_branch_merges_preserve_command_argument_selectors(
    portability: ModuleType, form: str, candidate_first: bool, fixed_host: bool
) -> None:
    """Keep argv structure across either branch order without hiding a fixed target.

    :param portability: Shared scanner module under test.
    :param form: If statement with a retained default, or a conditional expression.
    :param candidate_first: Whether the changed host is in the first or second branch.
    :param fixed_host: Whether the single changed selector is fixed or runtime-derived.
    :returns: None; assertions verify both possible command lists remain inspected.
    """
    start = PYTHON_SOURCE.index('["glab"')
    end = PYTHON_SOURCE.index(", check=True", start)
    runtime_argv = PYTHON_SOURCE[start:end]
    candidate_argv = runtime_argv.replace(
        "target.gitlab.host",
        '"gitlab.delivery.corp"' if fixed_host else "target.alternate_gitlab.host",
        1,
    )
    first, second = (
        (candidate_argv, runtime_argv)
        if candidate_first
        else (runtime_argv, candidate_argv)
    )
    if form == "statement":
        assignment = (
            f"    command = {first}\n"
            "    if target.override:\n"
            f"        command = {second}\n"
        )
    else:
        assignment = f"    command = {first} if target.override else {second}\n"
    source = PYTHON_SOURCE.replace(runtime_argv, "command", 1)
    signature = "def smoke(target, token_file):\n"
    source = source.replace(signature, signature + assignment, 1)

    findings = portability.scan_source("tests/python/test_branch_selection.py", source)

    if fixed_host:
        assert "host" in {finding.selector for finding in findings}
    else:
        assert findings == ()


@pytest.mark.parametrize(
    "declaration", ("export", "readonly", "local", "declare", "typeset")
)
@pytest.mark.parametrize("fixed_host", (False, True), ids=("runtime", "fixed-host"))
def test_shell_declaration_assignments_preserve_selected_host_values(
    portability: ModuleType, declaration: str, fixed_host: bool
) -> None:
    """Inspect scalar declaration assignments before native commands consume them.

    :param portability: Shared scanner module under test.
    :param declaration: Supported scalar shell declaration or export keyword.
    :param fixed_host: Whether only the assigned runtime host becomes a fixed target.
    :returns: None; assertions preserve runtime aliases and reject fixed host aliases.
    """
    command = SHELL_SOURCE.splitlines()[2]
    assignment = f'{declaration} GITLAB_HOST="$RUNTIME_GITLAB_HOST"\n'
    source = SHELL_SOURCE.replace(command, assignment + command, 1)
    lines = source.splitlines()
    source = "\n".join(
        (lines[0], "smoke() {", *("  " + line for line in lines[1:]), "}", "smoke", "")
    )
    if fixed_host:
        source = source.replace('"$RUNTIME_GITLAB_HOST"', '"gitlab.delivery.corp"', 1)

    findings = portability.scan_source("tests/smoke_declarations.sh", source)

    if fixed_host:
        assert "host" in {finding.selector for finding in findings}
    else:
        assert findings == ()
