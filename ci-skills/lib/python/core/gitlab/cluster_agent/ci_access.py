"""Verify one selected-project GitLab Agent CI access artifact."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Final

from lib.python.core.cli_api import response_decoder
from lib.python.core.gitlab.cluster_agent.ci_access_proof import (
    CI_ACCESS_ARTIFACT,
    CI_ACCESS_DEFINITION,
    CI_ACCESS_LIBRARY,
    ProofError,
    _producer_hash,
)
from lib.python.core.gitlab.cluster_agent.provider import (
    AgentProject,
    AgentRegistration,
)
from lib.python.core.gitlab.gitlab_api import GitLabAPIError
from lib.python.core.gitlab.gitlab_repository import _file
from lib.python.core.gitlab.gitlab_session import GitLabService
from lib.python.core.gitlab.job_read import gitlab_job_reference
from lib.python.core.json_types import JSONObject, JSONValue, json_integer, json_object
from lib.python.core.schema import validate_specification

CI_ACCESS_JOB: Final = "ci-agent-access"
_PROOF_SCHEMA = "gitlab-agent-ci-access.schema.json"
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_SAFE_ID = 2**53 - 1
# Consumer repository path of the staged probe; ci_access_proof owns the others.
CI_ACCESS_PROBE: Final = "ci_access_probe.py"
# Installed file that the consumer stages as CI_ACCESS_DEFINITION.
CI_ACCESS_JOB_SOURCE: Final = "ci_access_job.yml"
CI_ACCESS_KIT: Final = Path(__file__).parent
_STAGED_FILES: Final = (
    ("definition_sha256", CI_ACCESS_DEFINITION, CI_ACCESS_JOB_SOURCE),
    ("probe_sha256", CI_ACCESS_PROBE, CI_ACCESS_PROBE),
    ("library_sha256", CI_ACCESS_LIBRARY, CI_ACCESS_LIBRARY),
)
_PRODUCER_KEYS: Final = tuple(key for key, _, _ in _STAGED_FILES)
_PORTABLE_LIBRARY: Final = CI_ACCESS_KIT / CI_ACCESS_LIBRARY


class CiAccessError(ValueError):
    """Report a bounded verification failure without provider response values."""


@dataclass(frozen=True)
class ProducerBlob:
    """Bind a trusted staged producer file to its consumer repository path.

    .. attribute :: path
        :type: str

        Exact relative path at the consumer commit.

    .. attribute :: sha256
        :type: str

        Digest of the trusted staged file bytes.
    """

    path: str
    sha256: str


@dataclass(frozen=True)
class ExpectedCiAccess:
    """Hold current native Agent and Kubernetes identities for the proof check.

    .. attribute :: project
        :type: AgentProject

        Selected configuration project observed through the bound GitLab service.

    .. attribute :: agent
        :type: AgentRegistration

        Exact run-owned Agent registration with a verified active connection.

    .. attribute :: run_id
        :type: str

        Run identity from the verified ownership record.

    .. attribute :: kas_proxy_url
        :type: str

        Kubernetes proxy URL independently read from selected GitLab metadata.

    .. attribute :: cluster_uid
        :type: str

        Current cluster identity measured from the kube-system namespace.

    .. attribute :: namespace
        :type: str

        Namespace selected for the owned installation.

    .. attribute :: namespace_uid
        :type: str

        Current native UID of the installation namespace.

    .. attribute :: producers
        :type: Mapping[str, ProducerBlob]

        Trusted staged definition, probe, and library paths and digests.
    """

    project: AgentProject
    agent: AgentRegistration
    run_id: str
    kas_proxy_url: str
    cluster_uid: str
    namespace: str
    namespace_uid: str
    producers: Mapping[str, ProducerBlob]


@dataclass(frozen=True)
class VerifiedCiAccess:
    """Retain only schema-checked proof and its native job reference.

    .. attribute :: job_url
        :type: str

        Exact selected-project URL returned by GitLab for this job.

    .. attribute :: artifact_sha256
        :type: str

        Digest of the exact UTF-8 evidence artifact.

    .. attribute :: proof
        :type: JSONObject

        Verified proof document in the installed Agent CI access schema.
    """

    job_url: str
    artifact_sha256: str
    proof: JSONObject


def staged_producers(kit: Path = CI_ACCESS_KIT) -> Mapping[str, ProducerBlob]:
    """Identify the installed proof files a consumer stages at its repository root.

    :param kit: Directory holding the installed definition, probe, and library.
    :returns: Consumer path and digest of each trusted producer, keyed as in the artifact.
    :raises CiAccessError: An installed producer file is missing or oversized.
    """
    try:
        return MappingProxyType(
            {
                key: ProducerBlob(consumer, _producer_hash(kit / installed))
                for key, consumer, installed in _STAGED_FILES
            }
        )
    except ProofError:
        raise CiAccessError("agent_ci_installed_producer_unavailable") from None


def _valid_path(path: str) -> bool:
    return (
        bool(path)
        and not any(part in ("", ".", "..", ".git") for part in path.split("/"))
        and all(char.isprintable() and char != "\\" for char in path)
    )


def _trusted_producers(
    declared: Mapping[str, ProducerBlob],
) -> tuple[tuple[str, ProducerBlob], ...]:
    if not isinstance(declared, Mapping):
        raise CiAccessError("agent_ci_trusted_producers_missing")
    producers = dict(declared)
    if set(producers) != set(_PRODUCER_KEYS):
        raise CiAccessError("agent_ci_trusted_producers_missing")
    if any(
        not isinstance(blob, ProducerBlob)
        or not _valid_path(blob.path)
        or _SHA256.fullmatch(blob.sha256) is None
        for blob in producers.values()
    ):
        raise CiAccessError("agent_ci_trusted_producers_invalid")
    if (
        len({blob.path for blob in producers.values()}) != len(_PRODUCER_KEYS)
        or producers["definition_sha256"].path != CI_ACCESS_DEFINITION
    ):
        raise CiAccessError("agent_ci_trusted_producers_invalid")
    try:
        library_sha256 = _producer_hash(_PORTABLE_LIBRARY)
    except ProofError:
        raise CiAccessError("agent_ci_installed_producer_unavailable") from None
    if producers["library_sha256"].sha256 != library_sha256:
        raise CiAccessError("agent_ci_staged_library_mismatch")
    return tuple((key, producers[key]) for key in _PRODUCER_KEYS)


def _candidate(service: GitLabService) -> JSONObject:
    skill = service.session.skill
    revision = skill["revision"]
    value = revision["value"]
    digest = skill["functional_digest"]
    if (
        revision["verified"] is not True
        or value is None
        or _GIT_SHA.fullmatch(value) is None
        or _SHA256.fullmatch(digest) is None
    ):
        raise CiAccessError("agent_ci_candidate_unverified")
    return {"tested_revision": value, "functional_digest": digest}


def _native_id(value: JSONValue) -> int:
    result = json_integer(value)
    if not 0 < result <= _MAX_SAFE_ID:
        raise CiAccessError("agent_ci_native_id_invalid")
    return result


def _selected_job_id(
    job_url: str, service: GitLabService, project: AgentProject
) -> int:
    if not 0 < len(job_url) <= 2048 or any(
        not char.isprintable() or char.isspace() for char in job_url
    ):
        raise CiAccessError("agent_ci_job_url_invalid")
    path, identifier = gitlab_job_reference(job_url, service.session.host)
    if path != project.path or not identifier.isascii() or len(identifier) > 16:
        raise CiAccessError("agent_ci_job_project_mismatch")
    result = _native_id(int(identifier))
    if str(result) != identifier:
        raise CiAccessError("agent_ci_native_id_invalid")
    return result


def _job_identity(
    service: GitLabService, project: AgentProject, job_id: int, base: str
) -> tuple[str, int, str]:
    session = service.session
    job = json_object(service.api.get_json(session, f"{base}/jobs/{job_id}"))
    url = job.get("web_url")
    if (
        _native_id(job.get("id")) != job_id
        or job.get("name") != CI_ACCESS_JOB
        or job.get("status") != "success"
        or not isinstance(url, str)
        or _selected_job_id(url, service, project) != job_id
    ):
        raise CiAccessError("agent_ci_job_identity_mismatch")
    commit = json_object(job.get("commit")).get("id")
    if not isinstance(commit, str) or _GIT_SHA.fullmatch(commit) is None:
        raise CiAccessError("agent_ci_job_commit_invalid")
    job_pipeline = json_object(job.get("pipeline"))
    pipeline_id = _native_id(job_pipeline.get("id"))
    pipeline = json_object(
        service.api.get_json(session, f"{base}/pipelines/{pipeline_id}")
    )
    if (
        job_pipeline.get("sha") != commit
        or _native_id(job_pipeline.get("project_id")) != project.id
        or _native_id(pipeline.get("id")) != pipeline_id
        or _native_id(pipeline.get("project_id")) != project.id
        or pipeline.get("sha") != commit
    ):
        raise CiAccessError("agent_ci_pipeline_identity_mismatch")
    return url, pipeline_id, commit


def _read_artifact(service: GitLabService, endpoint: str) -> tuple[JSONObject, str]:
    raw = service.api.get_text(service.session, f"{endpoint}/{CI_ACCESS_ARTIFACT}")
    if "\ufffd" in raw:
        raise CiAccessError("agent_ci_artifact_encoding_invalid")
    proof = json_object(response_decoder().decode(raw))
    validate_specification(proof, _PROOF_SCHEMA)
    timestamp = proof.get("captured_at")
    if (
        not isinstance(timestamp, str)
        or datetime.fromisoformat(timestamp).utcoffset() is None
    ):
        raise CiAccessError("agent_ci_artifact_timestamp_invalid")
    return proof, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verify_blobs(
    service: GitLabService,
    base: str,
    commit: str,
    artifact_producer: JSONObject,
    producers: tuple[tuple[str, ProducerBlob], ...],
) -> None:
    if any(artifact_producer.get(key) != blob.sha256 for key, blob in producers):
        raise CiAccessError("agent_ci_artifact_producer_mismatch")
    for _, blob in producers:
        source = _file(
            service.api, service.session, f"{base}/repository", blob.path, commit
        )
        content = source.get("content") if source else None
        if not isinstance(content, str):
            raise CiAccessError("agent_ci_producer_blob_unavailable")
        try:
            decoded = base64.b64decode(content, validate=True)
        except ValueError:
            raise CiAccessError("agent_ci_producer_blob_encoding_invalid") from None
        if hashlib.sha256(decoded).hexdigest() != blob.sha256:
            raise CiAccessError("agent_ci_producer_blob_mismatch")


def verify_ci_access(
    service: GitLabService, job_url: str, expected: ExpectedCiAccess
) -> VerifiedCiAccess:
    """Verify the selected Agent CI proof through independent native reads.

    :param service: Existing access-verified GitLab service for the selected project.
    :param job_url: Exact selected-project CI access proof job URL.
    :param expected: Current owned installation identities and trusted staged files.
    :returns: Verified artifact identity and proof safe for the operation report.
    :raises CiAccessError: A required expectation, GitLab read, or proof is invalid.
    """
    project = expected.project
    if (
        service.session.target_kind != "project"
        or service.target_id != project.id
        or expected.agent.project_id != project.id
    ):
        raise CiAccessError("agent_ci_selected_project_mismatch")
    try:
        producers = _trusted_producers(expected.producers)
        candidate = _candidate(service)
        job_id = _selected_job_id(job_url, service, project)
        base = f"projects/{project.id}"
        canonical_url, pipeline_id, commit = _job_identity(
            service, project, job_id, base
        )
        proof, artifact_sha256 = _read_artifact(
            service, f"{base}/jobs/{job_id}/artifacts"
        )
        checks: JSONObject = {
            "status": "PASS",
            "run_id": expected.run_id,
            "project_id": project.id,
            "agent_id": expected.agent.id,
            "agent_context": f"{project.path}:{expected.agent.name}",
            "kas_proxy_url": expected.kas_proxy_url,
            "pipeline_id": pipeline_id,
            "job_id": job_id,
            "consumer_sha": commit,
            "cluster_uid": expected.cluster_uid,
            "namespace": expected.namespace,
            "namespace_uid": expected.namespace_uid,
            "expected_candidate": candidate,
        }
        if any(proof.get(key) != value for key, value in checks.items()):
            raise CiAccessError("agent_ci_artifact_identity_mismatch")
        artifact_producer = json_object(proof.get("producer"))
        _verify_blobs(service, base, commit, artifact_producer, producers)
    except CiAccessError:
        raise
    except GitLabAPIError as exc:
        raise CiAccessError(f"agent_ci_native_read_failed:{exc.reason}") from None
    except (KeyError, OSError, RecursionError, TypeError, ValueError):
        raise CiAccessError("agent_ci_readback_invalid") from None
    return VerifiedCiAccess(canonical_url, artifact_sha256, proof)
