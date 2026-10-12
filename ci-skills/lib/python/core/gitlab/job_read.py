"""Read a GitLab job, linked metadata, and bounded trace.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Final, Optional, Union
from urllib.parse import quote, urlsplit

from lib.python.access.gitlab import gitlab_env, glab_argv
from lib.python.core.collection import CommandReadError, _read_json, _search
from lib.python.core.json_types import JSONValue
from lib.python.core.report import report
from lib.python.core.runtime import error_class, run_command_tail, sanitize
from lib.python.core.target import GitLabOperationTarget, Target


def gitlab_job_reference(job_url: str, host: str) -> tuple[str, str]:
    """Extract a project path and numeric job ID from one selected-host URL.

    :param job_url: HTTPS GitLab job URL ending in ``/-/jobs/<id>``.
    :param host: Exact selected GitLab network authority.
    :returns: Relative project path and decimal job ID as text.
    :raises ValueError: Scheme, host, path, query, fragment, or job ID is invalid.
    """
    parsed = urlsplit(job_url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != host
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("job URL must use the exact selected GitLab FQDN")
    prefix, marker, job_id = parsed.path.rpartition("/-/jobs/")
    if not marker or not job_id.isdecimal() or not prefix.strip("/"):
        raise ValueError("job URL must contain a project path and numeric job ID")
    return prefix.strip("/"), job_id


JOB_METADATA_FIELDS: Final = {
    "pipeline": ("id", "status", "sha", "ref", "created_at", "updated_at"),
    "runner": (
        "id",
        "description",
        "runner_type",
        "status",
        "tag_list",
        "active",
        "paused",
        "online",
        "contacted_at",
    ),
}
JOB_FIELDS: Final = (
    "name",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "failure_reason",
)
TRACE_TIMEOUT: Final = 30
TRACE_LINES: Final = 200
TRACE_CHARACTERS: Final = 64000


def _linked_metadata(
    target: Union[Target, GitLabOperationTarget],
    base: str,
    job: Mapping[str, JSONValue],
    credential: Optional[Mapping[str, Optional[str]]],
) -> tuple[Mapping[str, JSONValue], Sequence[Mapping[str, str]]]:
    """Read only the pipeline and runner referenced by the selected job.

    :param target: Selected GitLab authority used for every native request.
    :param base: Encoded project endpoint prefix.
    :param job: Validated job response containing optional linked objects.
    :param credential: Bound environment for native GitLab requests.
    :returns: Reduced metadata and per-reference errors; an absent link is an error record.
    :raises OSError: A native metadata request cannot be started.
    """  # noqa: DOC503 - native startup failures propagate from the future.
    metadata: MutableMapping[str, JSONValue] = {}
    errors: MutableSequence[Mapping[str, str]] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        for name in JOB_METADATA_FIELDS:
            reference = job.get(name)
            identifier = reference.get("id") if isinstance(reference, Mapping) else None
            if not identifier:
                errors.append({"source": name, "reason": f"{name}_reference_missing"})
                continue
            endpoint = (
                f"{base}/pipelines/{identifier}"
                if name == "pipeline"
                else f"runners/{identifier}"
            )
            future = pool.submit(
                _read_json, glab_argv(target, endpoint), env=credential
            )
            futures[future] = (name, str(identifier))
        for future in as_completed(futures):
            name, identifier = futures[future]
            try:
                value = future.result()
                if not isinstance(value, Mapping) or str(value.get("id")) != identifier:
                    raise CommandReadError("invalid_response")
            except CommandReadError as exc:
                errors.append({"source": name, "reason": str(exc)})
            else:
                metadata[name] = {
                    field: value[field]
                    for field in JOB_METADATA_FIELDS[name]
                    if field in value
                }
    return metadata, errors


def _trace_tail(
    target: Union[Target, GitLabOperationTarget],
    endpoint: str,
    credential: Optional[Mapping[str, Optional[str]]],
) -> tuple[str, Sequence[Mapping[str, str]]]:
    """Read and sanitize the bounded trace for one selected job.

    :param target: Exact GitLab authority for the trace request.
    :param endpoint: Project job trace endpoint.
    :param credential: Bound native-client environment.
    :returns: Redacted trace tail and an error record when the native read fails.
    :raises OSError: The native trace process cannot be started.
    """  # noqa: DOC502 - run_command_tail can fail to start its subprocess.
    result = run_command_tail(
        glab_argv(target, endpoint), timeout=TRACE_TIMEOUT, env=credential
    )
    if result.returncode:
        return "", [{"source": "trace", "reason": error_class(result)}]
    return sanitize(
        "\n".join(result.stdout.splitlines()[-TRACE_LINES:]), TRACE_CHARACTERS
    ), []


def collect_gitlab_job(
    target: Union[Target, GitLabOperationTarget],
    args: Namespace,
    credential: Optional[Mapping[str, Optional[str]]] = None,
) -> MutableMapping[str, JSONValue]:
    """Read one GitLab job and its referenced pipeline and runner.

    :param target: GitLab target selected by the caller.
    :param args: Required job URL and optional text-search filter.
    :param credential: Bound child environment; when absent, a full target
        resolves its own GitLab credentials.
    :returns: Reduced job report, bounded trace and per-reference errors.
    :raises ValueError: The URL or GitLab authority is absent/invalid, or an
        operation target lacks its injected credential environment.
    :raises OSError: A native job, metadata or trace process cannot be started.
    """  # noqa: DOC503 - URL, target and native I/O failures propagate.
    if not getattr(args, "job_url", None):
        raise ValueError("--job-url is required; see --describe for the contract")
    selected = target.require_gitlab() if isinstance(target, Target) else target.gitlab
    project, job_id = gitlab_job_reference(args.job_url, selected.host)
    if credential is None:
        if isinstance(target, GitLabOperationTarget):
            raise ValueError("bound_gitlab_credential_required")
        credential = gitlab_env(target)
    base = f"projects/{quote(project, safe='')}"
    filters = {"job_url": args.job_url, "search": args.search}
    try:
        job = _read_json(glab_argv(target, f"{base}/jobs/{job_id}"), env=credential)
        if not isinstance(job, Mapping) or str(job.get("id")) != job_id:
            raise CommandReadError("invalid_response")
    except CommandReadError as exc:
        return report(
            "gitlab_job",
            selected.url,
            filters,
            [],
            [{"source": "job", "reason": str(exc)}],
        )
    metadata, errors = _linked_metadata(target, base, job, credential)
    trace, trace_errors = _trace_tail(target, f"{base}/jobs/{job_id}/trace", credential)
    row = {
        "job_id": job.get("id"),
        **{name: job.get(name) for name in JOB_FIELDS},
        "pipeline": metadata.get("pipeline"),
        "runner": metadata.get("runner"),
        "trace_tail": trace,
    }
    rows = [row] if _search(row, args.search) else []
    return report("gitlab_job", selected.url, filters, rows, [*errors, *trace_errors])
