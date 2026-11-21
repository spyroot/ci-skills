"""Offline verification of native selected-project GitLab Agent job proof."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec
from urllib.parse import quote

import pytest
import yaml
from conftest import import_script_module

CI_ACCESS = import_script_module("lib.python.core.gitlab.cluster_agent.ci_access")
API = import_script_module("lib.python.core.gitlab.gitlab_api")
SESSION = import_script_module("lib.python.core.gitlab.gitlab_session")
SCHEMA = import_script_module("lib.python.core.schema")

HOST: Final = "gitlab.example.test"
PROJECT_PATH: Final = "consumer/native-repo"
PROJECT_ID: Final = 42
AGENT_ID: Final = 17
AGENT_NAME: Final = "consumer-agent"
PIPELINE_ID: Final = 58
JOB_ID: Final = 73
RUN_ID: Final = "agent-run"
NAMESPACE: Final = "consumer-agent-system"
NAMESPACE_UID: Final = "native-namespace-uid"
CLUSTER_UID: Final = "native-cluster-uid"
KAS_PROXY: Final = f"https://{HOST}/-/kubernetes-agent/k8s-proxy"
CONSUMER_SHA: Final = "a" * 40
OTHER_SHA: Final = "b" * 40
SKILL_REVISION: Final = "c" * 40
FUNCTIONAL_DIGEST: Final = "d" * 64
JOB_URL: Final = f"https://{HOST}/{PROJECT_PATH}/-/jobs/{JOB_ID}"
BASE: Final = f"projects/{PROJECT_ID}"
JOB_ROUTE: Final = f"{BASE}/jobs/{JOB_ID}"
PIPELINE_ROUTE: Final = f"{BASE}/pipelines/{PIPELINE_ID}"
ARTIFACT_ROUTE: Final = f"{JOB_ROUTE}/artifacts/{CI_ACCESS.CI_ACCESS_ARTIFACT}"
DEFINITION: Final = CI_ACCESS.CI_ACCESS_DEFINITION
PROBE: Final = CI_ACCESS.CI_ACCESS_PROBE
LIBRARY: Final = CI_ACCESS.CI_ACCESS_LIBRARY
KIT: Final = CI_ACCESS.CI_ACCESS_KIT
# Installed file name of each staged consumer path.
INSTALLED: Final = MappingProxyType(
    {DEFINITION: CI_ACCESS.CI_ACCESS_JOB_SOURCE, PROBE: PROBE, LIBRARY: LIBRARY}
)
PRODUCER_KEYS: Final = ("definition_sha256", "probe_sha256", "library_sha256")
PICKED_URL: Final = "selected_url"
NATIVE: Final = "native"
ARTIFACT: Final = "artifact"
ENCODING: Final = "base64"
UNAVAILABLE_KIT: Final = "agent_ci_installed_producer_unavailable"
NO_KUBECONFIG: Final = "gitlab_kubeconfig_unavailable"
PROOF_SCHEMA: Final = "gitlab-agent-ci-access.schema.json"
JOB_TIMEOUT: Final = 30
# Pipeline variables and GitLab job variables the staged job reads; KUBECONFIG is absent.
JOB_VARIABLES: Final = MappingProxyType(
    {
        "CI_AGENT_ID": str(AGENT_ID),
        "CI_AGENT_RUN_ID": RUN_ID,
        "CI_AGENT_NAME": AGENT_NAME,
        "CI_AGENT_NAMESPACE": NAMESPACE,
        "CI_AGENT_EXPECTED_REVISION": SKILL_REVISION,
        "CI_AGENT_EXPECTED_FUNCTIONAL_DIGEST": FUNCTIONAL_DIGEST,
        "CI_PROJECT_PATH": PROJECT_PATH,
        "CI_PROJECT_ID": str(PROJECT_ID),
        "CI_PIPELINE_ID": str(PIPELINE_ID),
        "CI_JOB_ID": str(JOB_ID),
        "CI_COMMIT_SHA": CONSUMER_SHA,
    }
)


def _encoded(content: bytes) -> str:
    """Represent trusted or changed repository bytes as a native blob value."""
    return base64.b64encode(content).decode("ascii")


def _blob_route(path: str) -> str:
    """State the required immutable read route for the test-owned producer."""
    return f"{BASE}/repository/files/{quote(path, safe='')}?ref={CONSUMER_SHA}"


def _read_calls(session: SimpleNamespace) -> list[object]:
    """List the verifier's independent reads in order of dependency."""
    return [
        call.get_json(session, JOB_ROUTE),
        call.get_json(session, PIPELINE_ROUTE),
        call.get_text(session, ARTIFACT_ROUTE),
        *(
            call.get_json(session, _blob_route(path))
            for path in (DEFINITION, PROBE, LIBRARY)
        ),
    ]


@pytest.fixture(scope="module")
def valid_wire() -> tuple[CI_ACCESS.ExpectedCiAccess, str]:
    """Supply immutable baseline replies and independently measured producer bytes."""
    files = tuple(
        (key, path, (KIT / INSTALLED[path]).read_bytes())
        for key, path in zip(PRODUCER_KEYS, (DEFINITION, PROBE, LIBRARY), strict=True)
    )
    producers = MappingProxyType(
        {
            key: CI_ACCESS.ProducerBlob(path, hashlib.sha256(content).hexdigest())
            for key, path, content in files
        }
    )
    expected = CI_ACCESS.ExpectedCiAccess(
        project=CI_ACCESS.AgentProject(PROJECT_ID, PROJECT_PATH),
        agent=CI_ACCESS.AgentRegistration(AGENT_ID, AGENT_NAME, PROJECT_ID),
        run_id=RUN_ID,
        kas_proxy_url=KAS_PROXY,
        cluster_uid=CLUSTER_UID,
        namespace=NAMESPACE,
        namespace_uid=NAMESPACE_UID,
        producers=producers,
    )
    pipeline = {"id": PIPELINE_ID, "project_id": PROJECT_ID, "sha": CONSUMER_SHA}
    return expected, json.dumps(
        {
            PICKED_URL: JOB_URL,
            NATIVE: {
                JOB_ROUTE: {
                    "id": JOB_ID,
                    "name": CI_ACCESS.CI_ACCESS_JOB,
                    "status": "success",
                    "web_url": JOB_URL,
                    "commit": {"id": CONSUMER_SHA},
                    "pipeline": pipeline,
                },
                PIPELINE_ROUTE: pipeline,
                **{
                    _blob_route(path): {
                        "file_path": path,
                        "commit_id": CONSUMER_SHA,
                        "content": _encoded(content),
                        "encoding": ENCODING,
                    }
                    for _, path, content in files
                },
            },
            ARTIFACT: {
                "schema_version": "1.0",
                "kind": "gitlab_agent_ci_access",
                "status": "PASS",
                "captured_at": "2026-01-01T01:02:03Z",
                "run_id": RUN_ID,
                "project_id": PROJECT_ID,
                "agent_id": AGENT_ID,
                "agent_context": f"{PROJECT_PATH}:{AGENT_NAME}",
                "kas_proxy_url": KAS_PROXY,
                "pipeline_id": PIPELINE_ID,
                "job_id": JOB_ID,
                "consumer_sha": CONSUMER_SHA,
                "cluster_uid": CLUSTER_UID,
                "namespace": NAMESPACE,
                "namespace_uid": NAMESPACE_UID,
                "producer": {key: value.sha256 for key, value in producers.items()},
                "expected_candidate": {
                    "tested_revision": SKILL_REVISION,
                    "functional_digest": FUNCTIONAL_DIGEST,
                },
                "kubeconfig_source": "gitlab_ci",
                "errors": [],
            },
        }
    )


def _bound_service(
    wire: dict[str, object],
) -> tuple[SESSION.GitLabService, Mock, SimpleNamespace, str]:
    """Bind the selected project with only scripted transport responses."""
    session = SimpleNamespace(
        target_kind="project",
        host=HOST,
        skill={
            "revision": {"verified": True, "value": SKILL_REVISION},
            "functional_digest": FUNCTIONAL_DIGEST,
        },
    )
    api = create_autospec(API.GlabAPIClient, instance=True)
    artifact_text = json.dumps(wire[ARTIFACT])
    api.get_json.side_effect = lambda _session, endpoint: wire[NATIVE][endpoint]
    api.get_text.return_value = artifact_text
    return SESSION.GitLabService(api, session, PROJECT_ID), api, session, artifact_text


def test_agent_ci_access_binds_native_job_pipeline_and_exact_producer_bytes(
    valid_wire: tuple[CI_ACCESS.ExpectedCiAccess, str],
) -> None:
    """Accept the selected job only after its artifact and producer reads agree."""
    expected, baseline = valid_wire
    wire = json.loads(baseline)
    service, api, session, artifact_text = _bound_service(wire)

    verified = CI_ACCESS.verify_ci_access(service, wire[PICKED_URL], expected)

    assert verified.job_url == JOB_URL
    assert (
        verified.artifact_sha256
        == hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()
    )
    assert verified.proof == wire[ARTIFACT]
    assert api.mock_calls == _read_calls(session)


@pytest.mark.parametrize(
    ("path", "value", "reason", "reads"),
    (
        (
            (PICKED_URL,),
            JOB_URL.replace(PROJECT_PATH, "other/repo"),
            "agent_ci_job_project_mismatch",
            0,
        ),
        ((NATIVE, JOB_ROUTE, "id"), JOB_ID + 1, "agent_ci_job_identity_mismatch", 1),
        (
            (NATIVE, PIPELINE_ROUTE, "project_id"),
            PROJECT_ID + 1,
            "agent_ci_pipeline_identity_mismatch",
            2,
        ),
        (
            (ARTIFACT, "consumer_sha"),
            OTHER_SHA,
            "agent_ci_artifact_identity_mismatch",
            3,
        ),
        (
            (ARTIFACT, "agent_context"),
            f"{PROJECT_PATH}:different",
            "agent_ci_artifact_identity_mismatch",
            3,
        ),
        (
            (ARTIFACT, "expected_candidate", "tested_revision"),
            OTHER_SHA,
            "agent_ci_artifact_identity_mismatch",
            3,
        ),
        (
            (ARTIFACT, "producer", "library_sha256"),
            "e" * 64,
            "agent_ci_artifact_producer_mismatch",
            3,
        ),
        (
            (NATIVE, _blob_route(DEFINITION), "content"),
            _encoded(b"changed producer"),
            "agent_ci_producer_blob_mismatch",
            4,
        ),
        (
            (NATIVE, _blob_route(DEFINITION), "commit_id"),
            OTHER_SHA,
            "agent_ci_readback_invalid",
            4,
        ),
    ),
    ids=(
        "foreign-job-url",
        "native-job-replaced",
        "native-pipeline-foreign-project",
        "artifact-consumer-sha",
        "artifact-agent-context",
        "artifact-candidate-revision",
        "artifact-producer-digest",
        "native-producer-bytes",
        "native-producer-commit",
    ),
)
def test_agent_ci_access_rejects_one_native_or_artifact_mismatch(
    valid_wire: tuple[CI_ACCESS.ExpectedCiAccess, str],
    path: tuple[str, ...],
    value: object,
    reason: str,
    reads: int,
) -> None:
    """Reject a single differing value without reading unrelated or later resources."""
    expected, baseline = valid_wire
    wire = json.loads(baseline)
    target = wire
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    service, api, session, _ = _bound_service(wire)

    with pytest.raises(CI_ACCESS.CiAccessError) as rejected:
        CI_ACCESS.verify_ci_access(service, wire[PICKED_URL], expected)

    assert str(rejected.value) == reason
    assert api.mock_calls == _read_calls(session)[:reads]


def test_staged_producers_name_consumer_paths_and_installed_bytes(
    valid_wire: tuple[CI_ACCESS.ExpectedCiAccess, str],
) -> None:
    """Verify trusts the installed kit bytes at the paths the consumer stages."""
    expected, _ = valid_wire

    assert dict(CI_ACCESS.staged_producers()) == dict(expected.producers)


@pytest.mark.parametrize("missing", tuple(INSTALLED.values()))
def test_staged_producers_fail_closed_without_one_installed_file(
    tmp_path: Path, missing: str
) -> None:
    """A copy missing any kit file reports one reason instead of a partial trust set."""
    for name in INSTALLED.values():
        if name != missing:
            (tmp_path / name).write_bytes((KIT / name).read_bytes())

    with pytest.raises(CI_ACCESS.CiAccessError) as rejected:
        CI_ACCESS.staged_producers(tmp_path)

    assert str(rejected.value) == UNAVAILABLE_KIT


def test_staged_job_writes_the_artifact_verify_reads(tmp_path: Path) -> None:
    """Run the shipped job script in a consumer layout and read its artifact.

    The job has no Agent kubeconfig here, so the probe reports BLOCKED, but the
    artifact still carries the producer digests verify compares with the kit.
    """
    for path, name in INSTALLED.items():
        (tmp_path / path).write_bytes((KIT / name).read_bytes())
    definition = yaml.safe_load((tmp_path / DEFINITION).read_text(encoding="utf-8"))
    job = definition[CI_ACCESS.CI_ACCESS_JOB]
    environment = {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), os.defpath)),
        **JOB_VARIABLES,
    }

    exits = [
        subprocess.run(
            ["/bin/sh", "-c", line],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=JOB_TIMEOUT,
        ).returncode
        for line in job["script"]
    ]

    assert exits == [1]
    assert job["artifacts"]["paths"] == [CI_ACCESS.CI_ACCESS_ARTIFACT]
    artifact = json.loads(
        (tmp_path / CI_ACCESS.CI_ACCESS_ARTIFACT).read_text(encoding="utf-8")
    )
    SCHEMA.validate_specification(artifact, PROOF_SCHEMA)
    assert artifact["status"] == "BLOCKED"
    assert [error["code"] for error in artifact["errors"]] == [NO_KUBECONFIG]
    assert artifact["agent_id"] == AGENT_ID
    assert artifact["producer"] == {
        key: blob.sha256 for key, blob in CI_ACCESS.staged_producers().items()
    }
