"""Offline verification of native selected-project GitLab Agent job proof."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Final
from unittest.mock import Mock, call, create_autospec
from urllib.parse import quote

import pytest
import yaml
from conftest import import_script_module

CI_ACCESS = import_script_module("lib.python.core.gitlab.cluster_agent.ci_access")
PROOF = import_script_module("lib.python.core.gitlab.cluster_agent.ci_access_proof")
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
CONTEXT_MISMATCH: Final = "agent_context_mismatch"
CLUSTER_NAMESPACE: Final = "kube-system"
KUBECTL: Final = "kubectl"
KUBECONFIG_VARIABLE: Final = "KUBECONFIG"
KUBECONFIG_FILE: Final = "job-kubeconfig"
CONSUMER_DIR: Final = "consumer"
TOOLS_DIR: Final = "bin"
PROOF_SCHEMA: Final = "gitlab-agent-ci-access.schema.json"
JOB_TIMEOUT: Final = 30
CONTEXT: Final = f"{PROJECT_PATH}:{AGENT_NAME}"
OTHER_NAMESPACE: Final = "default"
OTHER_DIGEST: Final = "e" * 64
EXTRA_PRODUCER_KEY: Final = "kit_sha256"
PATH_VARIABLE: Final = "PATH"
EMPTY_DIR: Final = "empty"
BLOCKED: Final = "BLOCKED"
PASSED: Final = "PASS"
# Reason codes the probe and the verifier report.
CONTEXT_READ_FAILED: Final = "agent_context_read_failed"
NAMESPACE_READ_FAILED: Final = "namespace_read_failed"
IDENTITY_INVALID: Final = "namespace_identity_invalid"
PROXY_INVALID: Final = "kas_proxy_url_invalid"
NO_KUBECTL: Final = "kubectl_unavailable"
PRODUCERS_MISSING: Final = "agent_ci_trusted_producers_missing"
PRODUCERS_INVALID: Final = "agent_ci_trusted_producers_invalid"
LIBRARY_MISMATCH: Final = "agent_ci_staged_library_mismatch"
DEFINITION_KEY: Final = PRODUCER_KEYS[0]
PROBE_KEY: Final = PRODUCER_KEYS[1]
LIBRARY_KEY: Final = PRODUCER_KEYS[2]
# Artifact fields the probe fills in read order, and their values on PASS.
PROXY_URL_KEY: Final = "kas_proxy_url"
CLUSTER_UID_KEY: Final = "cluster_uid"
OBSERVED_KEYS: Final = (
    PROXY_URL_KEY,
    "agent_context",
    CLUSTER_UID_KEY,
    "namespace_uid",
)
OBSERVED_VALUES: Final = (KAS_PROXY, CONTEXT, CLUSTER_UID, NAMESPACE_UID)
# Number of OBSERVED_KEYS a BLOCKED artifact keeps, by the read that failed:
# the context read, the kube-system read, or the installation namespace read.
NONE_KEPT: Final = 0
PROXY_KEPT: Final = 2
CLUSTER_KEPT: Final = 3
# Length bounds of a namespace UID and of the proxy URL (schema maxLength).
MAX_UID: Final = 128
MAX_URL: Final = 2048
LONGEST_UID: Final = "u" * MAX_UID
# Fake client replies: a failing exit, bytes that are not UTF-8, and two hangs.
HANG_SECONDS: Final = 5
SHORT_TIMEOUT: Final = 1
FAILED_EXIT: Final = "exit 3"
NOT_UTF8: Final = "printf '\\377'"
OVER_LIMIT: Final = f"printf '%{PROOF._MAX_CLIENT_BYTES + 1}s' x"
SILENT_CLIENT: Final = f"exec sleep {HANG_SECONDS}"
CLOSED_STDOUT: Final = f"exec >&-; exec sleep {HANG_SECONDS}"
# Keyword of _fake_kubectl that replaces the reply to one read.
VIEW_REPLY: Final = "view"
CLUSTER_REPLY: Final = "cluster"
NAMESPACE_REPLY: Final = "namespace"
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


@pytest.mark.parametrize(
    ("size", "accepted"),
    (
        (0, False),
        (PROOF._MAX_PRODUCER_BYTES, True),
        (PROOF._MAX_PRODUCER_BYTES + 1, False),
    ),
    ids=("empty", "at-bound", "over-bound"),
)
def test_staged_producers_trust_only_a_library_within_the_size_bound(
    tmp_path: Path, size: int, accepted: bool
) -> None:
    """An empty or oversized installed library is not trusted; one at the bound is.

    A truncated install leaves an empty file and a replaced install can hold any
    size, so the kit applies the same byte bound the job applies to its own files.
    """
    for name in INSTALLED.values():
        (tmp_path / name).write_bytes((KIT / name).read_bytes())
    content = b"#" * size
    (tmp_path / INSTALLED[LIBRARY]).write_bytes(content)

    if not accepted:
        with pytest.raises(CI_ACCESS.CiAccessError) as rejected:
            CI_ACCESS.staged_producers(tmp_path)
        assert str(rejected.value) == UNAVAILABLE_KIT
        return
    assert CI_ACCESS.staged_producers(tmp_path)[LIBRARY_KEY] == CI_ACCESS.ProducerBlob(
        LIBRARY, hashlib.sha256(content).hexdigest()
    )


# One change per case to the trusted producers verify receives.
PRODUCER_CHANGES: Final[
    Mapping[str, tuple[Callable[[Mapping[str, object]], object], str]]
] = MappingProxyType(
    {
        "missing-key": (
            lambda blobs: {k: v for k, v in blobs.items() if k != PROBE_KEY},
            PRODUCERS_MISSING,
        ),
        "extra-key": (
            lambda blobs: {**blobs, EXTRA_PRODUCER_KEY: blobs[PROBE_KEY]},
            PRODUCERS_MISSING,
        ),
        "not-a-mapping": (lambda blobs: tuple(blobs.items()), PRODUCERS_MISSING),
        "duplicate-path": (
            lambda blobs: {**blobs, PROBE_KEY: replace(blobs[PROBE_KEY], path=LIBRARY)},
            PRODUCERS_INVALID,
        ),
        "definition-elsewhere": (
            lambda blobs: {
                **blobs,
                DEFINITION_KEY: replace(
                    blobs[DEFINITION_KEY], path=f"{CONSUMER_DIR}/{DEFINITION}"
                ),
            },
            PRODUCERS_INVALID,
        ),
        "parent-path": (
            lambda blobs: {
                **blobs,
                PROBE_KEY: replace(blobs[PROBE_KEY], path=f"../{PROBE}"),
            },
            PRODUCERS_INVALID,
        ),
        "library-digest": (
            lambda blobs: {
                **blobs,
                LIBRARY_KEY: replace(blobs[LIBRARY_KEY], sha256=OTHER_DIGEST),
            },
            LIBRARY_MISMATCH,
        ),
    }
)


@pytest.mark.parametrize(
    ("change", "reason"), tuple(PRODUCER_CHANGES.values()), ids=tuple(PRODUCER_CHANGES)
)
def test_verify_refuses_trusted_producers_before_any_native_read(
    valid_wire: tuple[CI_ACCESS.ExpectedCiAccess, str],
    change: Callable[[Mapping[str, object]], object],
    reason: str,
) -> None:
    """Verify rejects an incomplete, ambiguous or foreign trust set without reading GitLab.

    The caller builds the trust set from an installed kit; a kit from another
    release carries another library digest, and a hand-built set can repeat a
    path or move the CI definition away from the path GitLab runs.
    """
    expected, baseline = valid_wire
    wire = json.loads(baseline)
    service, api, _, _ = _bound_service(wire)
    changed = replace(expected, producers=change(expected.producers))

    with pytest.raises(CI_ACCESS.CiAccessError) as rejected:
        CI_ACCESS.verify_ci_access(service, wire[PICKED_URL], changed)

    assert str(rejected.value) == reason
    assert api.mock_calls == []


def test_verify_refuses_when_the_installed_library_is_unreadable(
    valid_wire: tuple[CI_ACCESS.ExpectedCiAccess, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify cannot compare the staged library with an installed file that is gone.

    An install directory changed after the kit was read loses the library file.
    """
    expected, baseline = valid_wire
    wire = json.loads(baseline)
    service, api, _, _ = _bound_service(wire)
    monkeypatch.setattr(CI_ACCESS, "_PORTABLE_LIBRARY", tmp_path / LIBRARY)

    with pytest.raises(CI_ACCESS.CiAccessError) as rejected:
        CI_ACCESS.verify_ci_access(service, wire[PICKED_URL], expected)

    assert str(rejected.value) == UNAVAILABLE_KIT
    assert api.mock_calls == []


def _lines(first: str, second: str) -> str:
    """Render a shell command printing two values separated by one newline.

    :param first: Value before the newline.
    :param second: Value after the newline, with no trailing newline.
    :returns: ``printf`` command line.
    """
    return f"printf '%s\\n%s' {shlex.quote(first)} {shlex.quote(second)}"


def _fake_kubectl(
    directory: Path,
    context: str,
    *,
    view: str | None = None,
    cluster: str | None = None,
    namespace: str | None = None,
) -> None:
    """Write a kubectl that answers the probe's three reads like the Agent proxy.

    :param directory: Directory placed first on the job's PATH.
    :param context: Context name the fake reports as current.
    :param view: Shell command replacing the context and proxy URL reply.
    :param cluster: Shell command replacing the ``kube-system`` namespace reply.
    :param namespace: Shell command replacing the installation namespace reply.
    """
    replies = (
        ("view", view or _lines(context, KAS_PROXY)),
        (
            f"namespace {CLUSTER_NAMESPACE}",
            cluster or _lines(CLUSTER_NAMESPACE, CLUSTER_UID),
        ),
        (f"namespace {NAMESPACE}", namespace or _lines(NAMESPACE, NAMESPACE_UID)),
    )
    script = directory / KUBECTL
    script.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        + "".join(f"  *' {read} '*) {reply} ;;\n" for read, reply in replies)
        + "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


@pytest.mark.parametrize(
    ("client", "code", "status", "errors"),
    (
        (None, 1, "BLOCKED", [NO_KUBECONFIG]),
        (f"{PROJECT_PATH}:{AGENT_NAME}", 0, "PASS", []),
        (f"{PROJECT_PATH}:other-agent", 1, "BLOCKED", [CONTEXT_MISMATCH]),
    ),
    ids=("no-agent-kubeconfig", "agent-context", "other-context"),
)
def test_staged_job_writes_the_artifact_verify_reads(
    tmp_path: Path,
    client: str | None,
    code: int,
    status: str,
    errors: list[str],
) -> None:
    """Run the shipped job script in a consumer layout and read its artifact.

    Without a kubeconfig the probe reports BLOCKED; with a kubectl answering for
    the selected context it reports PASS with the context, proxy and UIDs. Every
    artifact carries the producer digests verify compares with the kit.
    """
    consumer = tmp_path / CONSUMER_DIR
    tools = tmp_path / TOOLS_DIR
    consumer.mkdir()
    tools.mkdir()
    for path, name in INSTALLED.items():
        (consumer / path).write_bytes((KIT / name).read_bytes())
    definition = yaml.safe_load((consumer / DEFINITION).read_text(encoding="utf-8"))
    job = definition[CI_ACCESS.CI_ACCESS_JOB]
    environment = {
        "PATH": os.pathsep.join(
            (str(tools), str(Path(sys.executable).parent), os.defpath)
        ),
        **JOB_VARIABLES,
    }
    if client is not None:
        _fake_kubectl(tools, client)
        kubeconfig = tmp_path / KUBECONFIG_FILE
        kubeconfig.write_text("", encoding="utf-8")
        environment[KUBECONFIG_VARIABLE] = str(kubeconfig)

    exits = [
        subprocess.run(
            ["/bin/sh", "-c", line],
            cwd=consumer,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=JOB_TIMEOUT,
        ).returncode
        for line in job["script"]
    ]

    assert exits == [code]
    assert job["artifacts"]["paths"] == [CI_ACCESS.CI_ACCESS_ARTIFACT]
    artifact = json.loads(
        (consumer / CI_ACCESS.CI_ACCESS_ARTIFACT).read_text(encoding="utf-8")
    )
    SCHEMA.validate_specification(artifact, PROOF_SCHEMA)
    assert artifact["status"] == status
    assert [error["code"] for error in artifact["errors"]] == errors
    assert artifact["agent_id"] == AGENT_ID
    assert artifact["producer"] == {
        key: blob.sha256 for key, blob in CI_ACCESS.staged_producers().items()
    }
    if status == "PASS":
        assert (
            artifact["agent_context"],
            artifact["kas_proxy_url"],
            artifact["cluster_uid"],
            artifact["namespace_uid"],
        ) == (client, KAS_PROXY, CLUSTER_UID, NAMESPACE_UID)


@pytest.fixture
def agent_job(tmp_path: Path, fake_bin: Path) -> dict[str, str]:
    """Give the probe the job variables, a kubeconfig file and the fake-bin PATH.

    :param tmp_path: Pytest temporary directory holding the kubeconfig file.
    :param fake_bin: Directory where each test writes its fake kubectl.
    :returns: Job environment the probe reads.
    """
    kubeconfig = tmp_path / KUBECONFIG_FILE
    kubeconfig.write_text("", encoding="utf-8")
    return {
        **JOB_VARIABLES,
        PATH_VARIABLE: os.pathsep.join((str(fake_bin), os.defpath)),
        KUBECONFIG_VARIABLE: str(kubeconfig),
    }


def _collect(environment: Mapping[str, str]) -> dict[str, object]:
    """Run the proof library in-process on the installed producer files.

    :param environment: Job environment for the probe.
    :returns: Artifact document the job would upload.
    """
    return PROOF.collect_ci_access(
        environment,
        agent_id=AGENT_ID,
        definition=KIT / INSTALLED[DEFINITION],
        probe=KIT / INSTALLED[PROBE],
        library=KIT / INSTALLED[LIBRARY],
    )


def _assert_blocked(artifact: Mapping[str, object], code: str, kept: int) -> None:
    """Check one BLOCKED artifact: its code, the reads it keeps, its producer digests.

    :param artifact: Document returned by the probe.
    :param code: Single expected error code.
    :param kept: Number of ``OBSERVED_KEYS`` read before the failure.
    """
    SCHEMA.validate_specification(artifact, PROOF_SCHEMA)
    assert artifact["status"] == BLOCKED
    assert [error["code"] for error in artifact["errors"]] == [code]
    assert tuple(artifact[key] for key in OBSERVED_KEYS) == (
        OBSERVED_VALUES[:kept] + (None,) * (len(OBSERVED_KEYS) - kept)
    )
    assert artifact["producer"] == {
        key: blob.sha256 for key, blob in CI_ACCESS.staged_producers().items()
    }


def _padded(prefix: str, length: int) -> str:
    """Extend a URL with one path segment to an exact length.

    :param prefix: URL to extend.
    :param length: Total length of the result.
    :returns: ``prefix`` followed by ``/`` and filler characters.
    """
    return f"{prefix}/{'a' * (length - len(prefix) - 1)}"


@pytest.mark.parametrize(
    ("replies", "code", "kept"),
    (
        (
            {CLUSTER_REPLY: f"{_lines(CLUSTER_NAMESPACE, CLUSTER_UID)}; {FAILED_EXIT}"},
            NAMESPACE_READ_FAILED,
            PROXY_KEPT,
        ),
        (
            {VIEW_REPLY: OVER_LIMIT},
            CONTEXT_READ_FAILED,
            NONE_KEPT,
        ),
        ({VIEW_REPLY: NOT_UTF8}, CONTEXT_READ_FAILED, NONE_KEPT),
        (
            {CLUSTER_REPLY: _lines(OTHER_NAMESPACE, CLUSTER_UID)},
            IDENTITY_INVALID,
            PROXY_KEPT,
        ),
        (
            {CLUSTER_REPLY: _lines(CLUSTER_NAMESPACE, CLUSTER_UID.replace("-", " "))},
            IDENTITY_INVALID,
            PROXY_KEPT,
        ),
        ({CLUSTER_REPLY: _lines(CLUSTER_NAMESPACE, "")}, IDENTITY_INVALID, PROXY_KEPT),
        (
            {CLUSTER_REPLY: _lines(CLUSTER_NAMESPACE, f"{LONGEST_UID}u")},
            IDENTITY_INVALID,
            PROXY_KEPT,
        ),
        (
            {NAMESPACE_REPLY: _lines(OTHER_NAMESPACE, NAMESPACE_UID)},
            IDENTITY_INVALID,
            CLUSTER_KEPT,
        ),
    ),
    ids=(
        "exit-after-valid-reply",
        "over-byte-limit",
        "not-utf8",
        "cluster-namespace-renamed",
        "uid-with-space",
        "uid-empty",
        "uid-over-length",
        "installation-namespace-renamed",
    ),
)
def test_probe_blocks_on_a_failed_client_read_or_namespace_identity(
    agent_job: dict[str, str],
    fake_bin: Path,
    replies: Mapping[str, str],
    code: str,
    kept: int,
) -> None:
    """A client failure or a reply naming another object blocks the artifact.

    The job PATH decides which kubectl runs, so a wrapper or another client can
    exit non-zero after printing, print more than the byte limit or bytes that
    are not UTF-8, or name another namespace or a malformed UID. The artifact
    keeps the reads that passed before the failure and the producer digests.
    """
    _fake_kubectl(fake_bin, CONTEXT, **replies)

    _assert_blocked(_collect(agent_job), code, kept)


# Proxy URLs the probe refuses: another scheme, credentials, a query, a fragment,
# no host, and one character over the URL bound.
INVALID_PROXIES: Final = MappingProxyType(
    {
        "ftp-scheme": f"ftp://{HOST}/k8s-proxy",
        "userinfo": f"https://{AGENT_NAME}@{HOST}/k8s-proxy",
        "query": f"{KAS_PROXY}?agent={AGENT_ID}",
        "fragment": f"{KAS_PROXY}#{AGENT_NAME}",
        "no-host": "https:///k8s-proxy",
        "over-length": _padded(KAS_PROXY, MAX_URL + 1),
    }
)


@pytest.mark.parametrize(
    "url", tuple(INVALID_PROXIES.values()), ids=tuple(INVALID_PROXIES)
)
def test_probe_blocks_a_proxy_url_it_cannot_report(
    agent_job: dict[str, str], fake_bin: Path, url: str
) -> None:
    """A proxy URL with credentials or extra parts blocks before any namespace read.

    The probe copies the server field of the job kubeconfig into the artifact,
    so a URL with userinfo, a query or a fragment is never reported.
    """
    _fake_kubectl(fake_bin, CONTEXT, view=_lines(CONTEXT, url))

    _assert_blocked(_collect(agent_job), PROXY_INVALID, NONE_KEPT)


@pytest.mark.parametrize(
    ("replies", "key", "value"),
    (
        (
            {CLUSTER_REPLY: _lines(CLUSTER_NAMESPACE, LONGEST_UID)},
            CLUSTER_UID_KEY,
            LONGEST_UID,
        ),
        (
            {VIEW_REPLY: _lines(CONTEXT, _padded(KAS_PROXY, MAX_URL))},
            PROXY_URL_KEY,
            _padded(KAS_PROXY, MAX_URL),
        ),
    ),
    ids=("uid-at-bound", "url-at-bound"),
)
def test_probe_accepts_a_uid_and_proxy_url_at_their_length_bounds(
    agent_job: dict[str, str],
    fake_bin: Path,
    replies: Mapping[str, str],
    key: str,
    value: str,
) -> None:
    """A value exactly at its schema bound passes and is reported unchanged."""
    _fake_kubectl(fake_bin, CONTEXT, **replies)

    artifact = _collect(agent_job)

    SCHEMA.validate_specification(artifact, PROOF_SCHEMA)
    assert (artifact["status"], artifact["errors"], artifact[key]) == (
        PASSED,
        [],
        value,
    )


@pytest.mark.parametrize(
    ("spare", "status", "errors"),
    ((0, PASSED, []), (-1, BLOCKED, [CONTEXT_READ_FAILED])),
    ids=("reply-at-limit", "reply-one-byte-over"),
)
def test_probe_bounds_each_client_reply_to_the_byte_limit(
    agent_job: dict[str, str],
    fake_bin: Path,
    monkeypatch: pytest.MonkeyPatch,
    spare: int,
    status: str,
    errors: list[str],
) -> None:
    """A reply of exactly the byte limit is read; one byte more is refused.

    The limit is set to the length of the longest valid reply, the context and
    proxy URL read, so only the size check separates the two cases.
    """
    limit = len(f"{CONTEXT}\n{KAS_PROXY}".encode()) + spare
    monkeypatch.setattr(PROOF, "_MAX_CLIENT_BYTES", limit)
    _fake_kubectl(fake_bin, CONTEXT)

    artifact = _collect(agent_job)

    assert artifact["status"] == status
    assert [error["code"] for error in artifact["errors"]] == errors


@pytest.mark.parametrize(
    ("replies", "code", "kept"),
    (
        ({VIEW_REPLY: SILENT_CLIENT}, CONTEXT_READ_FAILED, NONE_KEPT),
        (
            {
                CLUSTER_REPLY: f"{_lines(CLUSTER_NAMESPACE, CLUSTER_UID)}; {CLOSED_STDOUT}"
            },
            NAMESPACE_READ_FAILED,
            PROXY_KEPT,
        ),
    ),
    ids=("never-writes", "replies-then-never-exits"),
)
def test_probe_stops_a_client_at_the_read_deadline(
    agent_job: dict[str, str],
    fake_bin: Path,
    monkeypatch: pytest.MonkeyPatch,
    replies: Mapping[str, str],
    code: str,
    kept: int,
) -> None:
    """A client that hangs is killed at the deadline instead of holding the job.

    A stalled Agent proxy can leave kubectl waiting with no output, and a client
    can close its output and keep running. The read ends at the client timeout,
    before the hung client would exit on its own.
    """
    monkeypatch.setattr(PROOF, "_CLIENT_TIMEOUT", SHORT_TIMEOUT)
    _fake_kubectl(fake_bin, CONTEXT, **replies)
    started = time.monotonic()

    artifact = _collect(agent_job)

    assert time.monotonic() - started < HANG_SECONDS
    _assert_blocked(artifact, code, kept)


def _relative_kubeconfig(tmp_path: Path) -> dict[str, str]:
    """Name the job kubeconfig relative to the working directory."""
    return {KUBECONFIG_VARIABLE: KUBECONFIG_FILE}


def _kubeconfig_list(tmp_path: Path) -> dict[str, str]:
    """Name an existing file whose path contains the list separator."""
    listed = tmp_path / f"{CONSUMER_DIR}{os.pathsep}{TOOLS_DIR}"
    listed.mkdir()
    (listed / KUBECONFIG_FILE).write_text("", encoding="utf-8")
    return {KUBECONFIG_VARIABLE: str(listed / KUBECONFIG_FILE)}


def _kubeconfig_directory(tmp_path: Path) -> dict[str, str]:
    """Name a directory instead of a kubeconfig file."""
    return {KUBECONFIG_VARIABLE: str(tmp_path)}


def _no_kubectl(tmp_path: Path) -> dict[str, str]:
    """Set PATH to an empty directory, so no kubectl is found."""
    empty = tmp_path / EMPTY_DIR
    empty.mkdir()
    return {PATH_VARIABLE: str(empty)}


@pytest.mark.parametrize(
    ("change", "code"),
    (
        (_relative_kubeconfig, NO_KUBECONFIG),
        (_kubeconfig_list, NO_KUBECONFIG),
        (_kubeconfig_directory, NO_KUBECONFIG),
        (_no_kubectl, NO_KUBECTL),
    ),
    ids=("relative-path", "path-list", "directory", "no-kubectl-on-path"),
)
def test_probe_requires_one_absolute_kubeconfig_file_and_kubectl(
    agent_job: dict[str, str],
    fake_bin: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[Path], dict[str, str]],
    code: str,
) -> None:
    """The probe binds exactly one kubeconfig file and a kubectl from the job PATH.

    A runner image or a job ``variables`` block can set KUBECONFIG to a relative
    path, a list of files, or a directory, and an image can lack kubectl. Each
    case keeps a working fake kubectl and a readable file so that only the one
    changed input blocks the read.
    """
    monkeypatch.chdir(tmp_path)
    _fake_kubectl(fake_bin, CONTEXT)

    _assert_blocked(_collect({**agent_job, **change(tmp_path)}), code, NONE_KEPT)
