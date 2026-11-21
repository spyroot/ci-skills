"""Produce selected-project Agent CI evidence using only Python's standard library.

The consumer job uses a byte-for-byte copy of this module. Values supplied by
the installer remain expectations until independently checked by the verifier
against native GitLab job, commit, artifact, and repository reads.
"""

from __future__ import annotations

import hashlib
import os
import re
import selectors
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

# Consumer repository paths the job reads: the CI definition at the working
# directory and this library beside the probe; the job uploads the artifact.
CI_ACCESS_DEFINITION: Final = ".gitlab-ci.yml"
CI_ACCESS_LIBRARY: Final = "ci_access_proof.py"
CI_ACCESS_ARTIFACT: Final = "agent-access.json"

_NAMESPACE = re.compile(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?")
_PROJECT_PATH = re.compile(r"[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[1-9][0-9]{0,15}")
_MAX_SAFE_ID = 2**53 - 1
_MAX_PRODUCER_BYTES = 1_048_576
_MAX_CLIENT_BYTES = 4096
_CLIENT_TIMEOUT = 10
CLUSTER_ID_NAMESPACE = "kube-system"
_CONTEXT_FIELDS = 'jsonpath={.current-context}{"\\n"}{.clusters[0].cluster.server}'
_NAMESPACE_FIELDS = 'jsonpath={.metadata.name}{"\\n"}{.metadata.uid}'


class ProofError(ValueError):
    """A safe failure code that never includes native output or credentials."""


def _required(
    environ: Mapping[str, str], key: str, pattern: re.Pattern[str], maximum: int
) -> str:
    value = environ.get(key, "")
    if len(value) > maximum or pattern.fullmatch(value) is None:
        raise ProofError(f"invalid_{key.lower()}")
    return value


def _native_id(environ: Mapping[str, str], key: str) -> int:
    result = int(_required(environ, key, _ID, 16))
    if result > _MAX_SAFE_ID:
        raise ProofError(f"invalid_{key.lower()}")
    return result


def _producer_hash(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            payload = stream.read(_MAX_PRODUCER_BYTES + 1)
    except OSError:
        raise ProofError("producer_file_unavailable") from None
    if not payload or len(payload) > _MAX_PRODUCER_BYTES:
        raise ProofError("producer_file_size_invalid")
    return hashlib.sha256(payload).hexdigest()


def _run_client(
    arguments: Sequence[str], environ: Mapping[str, str], failure: str
) -> str:
    """Read only the bounded projected fields and discard native diagnostics."""
    payload = bytearray()
    deadline = time.monotonic() + _CLIENT_TIMEOUT
    try:
        with subprocess.Popen(
            arguments,
            env=environ,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                if process.stdout is None:
                    raise ProofError(failure)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise ProofError(failure)
                        chunk = os.read(
                            process.stdout.fileno(),
                            _MAX_CLIENT_BYTES + 1 - len(payload),
                        )
                        if not chunk:
                            break
                        payload.extend(chunk)
                        if len(payload) > _MAX_CLIENT_BYTES:
                            raise ProofError(failure)
                if process.wait(timeout=max(0, deadline - time.monotonic())) != 0:
                    raise ProofError(failure)
            finally:
                if process.poll() is None:
                    process.kill()
        return payload.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise ProofError(failure) from None


def _proxy_url(server: str) -> str:
    try:
        parsed = urlsplit(server)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and 0 < len(server) <= 2048
            and all(char.isprintable() and not char.isspace() for char in server)
        ):
            return server
    except ValueError:
        pass
    raise ProofError("kas_proxy_url_invalid")


class _KubernetesRead:
    """Bind every client call to the job kubeconfig and exact Agent context."""

    def __init__(self, environ: Mapping[str, str], context: str):
        """Bind reads to the job's kubeconfig and the selected Agent context.

        :param environ: Native GitLab job environment with KUBECONFIG and PATH.
        :param context: Exact configuration project path and Agent name.
        :raises ProofError: If the kubeconfig or kubectl is unavailable.
        """
        kubeconfig = environ.get("KUBECONFIG", "")
        path = Path(kubeconfig)
        if not path.is_absolute() or os.pathsep in kubeconfig or not path.is_file():
            raise ProofError("gitlab_kubeconfig_unavailable")
        executable = shutil.which("kubectl", path=environ.get("PATH", os.defpath))
        if executable is None:
            raise ProofError("kubectl_unavailable")
        self._environment = environ
        self._context = context
        self._base = (
            executable,
            "--kubeconfig",
            kubeconfig,
            "--context",
            context,
            f"--request-timeout={_CLIENT_TIMEOUT}s",
        )

    def proxy(self) -> str:
        value = _run_client(
            (*self._base, "config", "view", "--minify", "-o", _CONTEXT_FIELDS),
            self._environment,
            "agent_context_read_failed",
        )
        selected, _, url = value.partition("\n")
        if selected != self._context:
            raise ProofError("agent_context_mismatch")
        return _proxy_url(url)

    def namespace_uid(self, name: str) -> str:
        value = _run_client(
            (*self._base, "get", "namespace", name, "-o", _NAMESPACE_FIELDS),
            self._environment,
            "namespace_read_failed",
        )
        observed, _, uid = value.partition("\n")
        if (
            observed != name
            or not 0 < len(uid) <= 128
            or not all(char.isprintable() and not char.isspace() for char in uid)
        ):
            raise ProofError("namespace_identity_invalid")
        return uid


def collect_ci_access(
    environ: Mapping[str, str],
    *,
    agent_id: int,
    definition: Path,
    probe: Path,
    library: Path,
) -> dict[str, object]:
    """Produce the exact artifact document, retaining safe partial observations.

    The fixture adapter supplies the selected registration ID and three
    repository paths. Native job variables, the Agent context, and the two
    native namespace reads are independently observed here. Missing required
    envelope inputs raise ProofError because a conforming artifact cannot be
    produced without them.

    :param environ: Native GitLab job and qualification input variables.
    :param agent_id: Native registration ID from the connection run.
    :param definition: Exact consumer CI definition file.
    :param probe: Exact consumer fixture adapter file.
    :param library: Exact copy of the portable proof library used in the job.
    :returns: PASS or BLOCKED artifact with sanitized observations.
    :raises ProofError: If required input identity or producer data is invalid.
    """
    if type(agent_id) is not int or not 0 < agent_id <= _MAX_SAFE_ID:
        raise ProofError("agent_id_invalid")
    run_id = _required(environ, "CI_AGENT_RUN_ID", _NAMESPACE, 63)
    agent_name = _required(environ, "CI_AGENT_NAME", _NAMESPACE, 63)
    namespace = _required(environ, "CI_AGENT_NAMESPACE", _NAMESPACE, 63)
    project_path = _required(environ, "CI_PROJECT_PATH", _PROJECT_PATH, 1024)
    expected_context = f"{project_path}:{agent_name}"
    result: dict[str, object] = {
        "schema_version": "1.0",
        "kind": "gitlab_agent_ci_access",
        "status": "BLOCKED",
        "captured_at": "",
        "run_id": run_id,
        "project_id": _native_id(environ, "CI_PROJECT_ID"),
        "agent_id": agent_id,
        "agent_context": None,
        "kas_proxy_url": None,
        "pipeline_id": _native_id(environ, "CI_PIPELINE_ID"),
        "job_id": _native_id(environ, "CI_JOB_ID"),
        "consumer_sha": _required(environ, "CI_COMMIT_SHA", _GIT_SHA, 40),
        "cluster_uid": None,
        "namespace": namespace,
        "namespace_uid": None,
        "producer": {
            "definition_sha256": _producer_hash(definition),
            "probe_sha256": _producer_hash(probe),
            "library_sha256": _producer_hash(library),
        },
        "expected_candidate": {
            "tested_revision": _required(
                environ, "CI_AGENT_EXPECTED_REVISION", _GIT_SHA, 40
            ),
            "functional_digest": _required(
                environ, "CI_AGENT_EXPECTED_FUNCTIONAL_DIGEST", _SHA256, 64
            ),
        },
        "kubeconfig_source": "gitlab_ci",
        "errors": [],
    }
    try:
        client = _KubernetesRead(environ, expected_context)
        result["kas_proxy_url"] = client.proxy()
        result["agent_context"] = expected_context
        result["cluster_uid"] = client.namespace_uid(CLUSTER_ID_NAMESPACE)
        result["namespace_uid"] = client.namespace_uid(namespace)
    except (ProofError, OSError) as exc:
        code = str(exc) if isinstance(exc, ProofError) else "kubernetes_read_failed"
        result["errors"] = [{"code": code, "message": "Agent CI read-back failed."}]
    else:
        result["status"] = "PASS"
    result["captured_at"] = datetime.now(UTC).isoformat()
    return result
