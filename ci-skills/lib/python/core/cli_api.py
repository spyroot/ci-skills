"""Shared bounded CLI API transport for provider callbacks.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
from collections.abc import (
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import NoReturn, Optional, Protocol
from urllib.parse import urlsplit

from lib.python.core.json_types import (
    JSONDocument,
    JSONObject,
    JSONValue,
    copy_json_object,
    is_json_value,
)
from lib.python.core.runtime import (
    CommandResult,
    error_class,
    run_command_bounded,
    trace_event,
)

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
READ_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 30
REQUEST_DEADLINE_EXCEEDED = "request_deadline_exceeded"
_RETRYABLE = frozenset(
    {"timeout", "transport", "rate_limited", "provider_408", "provider_5xx"}
)
_RETRY_AFTER = re.compile(r"(?im)^retry-after\s*:\s*([^\r\n]+)")


class APISession(Protocol):
    """Provider-neutral inputs required by the native API transport."""

    @property
    def host(self) -> str:
        """Expose the provider selected for this session.

        :returns: Exact provider hostname passed to the native CLI.
        """
        ...

    @property
    def environment(self) -> Mapping[str, Optional[str]]:
        """Expose credential overrides for child requests.

        :returns: Environment mapping whose ``None`` values remove ambient variables.
        """
        ...


class CommandRunner(Protocol):
    """Execute one bounded native request with explicit process inputs."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, Optional[str]],
        max_stdout_bytes: int = MAX_RESPONSE_BYTES,
    ) -> CommandResult:
        """Run the selected executable without shell interpolation.

        :param argv: Executable and ordered argument values.
        :param timeout: Maximum child process runtime in seconds.
        :param env: Child environment changes; None removes a named variable.
        :param max_stdout_bytes: Maximum retained stdout before process termination.
        :returns: Native process exit code and bounded stdout and stderr.
        :raises OSError: If the executable cannot start or its pipes cannot be read.
        """  # noqa: DOC502 - Protocol declares implementer native I/O failures.
        ...


@dataclass(frozen=True)
class ReadSession:
    """Provider identity and credential overrides used by a native request.

    :param host: Exact provider hostname selected for the request.
    :param environment: Child environment overrides; ``None`` removes a variable.
    :param credential_source: Reportable file, environment, or CLI-store reference.

    .. attribute :: host
        :type: str

        Exact provider hostname selected for the request.

    .. attribute :: environment
        :type: Mapping[str, Optional[str]]

        Child environment overrides; ``None`` removes a variable.

    .. attribute :: credential_source
        :type: str

        Reportable file, environment, or CLI-store reference.
    """

    host: str
    environment: Mapping[str, Optional[str]] = field(repr=False)
    credential_source: str


class APIError(RuntimeError):
    """A classified API failure with no response body or credential value."""

    def __init__(self, reason: str, *, attempts: int = 1):
        """Record a redacted provider failure and retry count.

        :param reason: Classified error reason without response content.
        :param attempts: Number of transport attempts made.
        """
        self.reason = reason
        self.attempts = attempts
        super().__init__(reason)


@dataclass(frozen=True)
class RequestDeadline:
    """Share an absolute read deadline without mutating a transport client.

    :param expires_at: Expiration in the supplied clock's monotonic time domain.
    :param monotonic: Clock used by every concurrent request sharing this deadline.

    .. attribute :: expires_at
        :type: float

        Absolute monotonic expiration time.

    .. attribute :: monotonic
        :type: Callable[[], float]

        Clock used to calculate the remaining request budget.
    """

    expires_at: float
    monotonic: Callable[[], float] = time.monotonic

    def remaining(self) -> float:
        """Return the request budget, refusing a read after expiration.

        :returns: Positive seconds remaining before the shared deadline.
        :raises APIError: The shared request deadline has expired.
        """
        remaining = self.expires_at - self.monotonic()
        if remaining <= 0:
            raise APIError(REQUEST_DEADLINE_EXCEEDED)
        return remaining


def _reject_constant(_value: str) -> NoReturn:
    """Reject JSON extensions for nonfinite numbers.

    :param _value: Nonstandard constant encountered by the JSON decoder.
    :raises ValueError: Always, because nonfinite numbers are not valid JSON.
    """
    raise ValueError("nonfinite_json_number")


def _unique_fields(pairs: Sequence[tuple[str, JSONValue]]) -> JSONObject:
    """Reject duplicate object fields before a decoder can overwrite them.

    :param pairs: Object fields in the order received from the JSON decoder.
    :returns: A mapping retaining every uniquely named field.
    :raises ValueError: If an object repeats a field name and would lose data.
    """
    fields: MutableMapping[str, JSONValue] = {}
    for name, value in pairs:
        if name in fields:
            raise ValueError("duplicate_json_field")
        fields[name] = value
    return fields


class ResponseDecoder:
    """Decode provider values while exposing their validated JSON types."""

    def __init__(self):
        """Use the shared rejection policy for nonfinite JSON constants."""
        self._decoder = json.JSONDecoder(
            parse_constant=_reject_constant, object_pairs_hook=_unique_fields
        )

    def decode(self, s: str) -> JSONValue:
        """Decode one complete provider response.

        :param s: Text containing one JSON value and optional whitespace.
        :returns: String-keyed mapping, list, string, finite number, boolean,
            or JSON null matching the response.
        :raises ValueError: If text is malformed, repeats object fields, or
            contains nonfinite numbers.
        :raises RecursionError: If nesting exceeds the decoder's capacity.
        """  # noqa: DOC503 - Nested JSON decoding can raise RecursionError.
        value: object = self._decoder.decode(s)
        if not is_json_value(value):
            raise ValueError("invalid_json_value")
        return value

    def raw_decode(self, s: str, idx: int = 0) -> tuple[JSONValue, int]:
        """Decode a value from a native CLI stream containing multiple pages.

        :param s: Text beginning with JSON at the selected offset.
        :param idx: Zero-based offset where decoding begins.
        :returns: Validated JSON value and the first offset after that value.
        :raises ValueError: If the value is malformed, repeats object fields, or
            contains nonfinite numbers.
        :raises RecursionError: If nesting exceeds the decoder's capacity.
        """  # noqa: DOC503 - Nested JSON decoding can raise RecursionError.
        value, end = self._decoder.raw_decode(s, idx)
        if not is_json_value(value):
            raise ValueError("invalid_json_value")
        return value, end


def response_decoder() -> ResponseDecoder:
    """Build the shared decoder for one response or native page stream.

    :returns: Decoder exposing validated JSON values and rejecting nonfinite numbers.
    """
    return ResponseDecoder()


@dataclass(frozen=True)
class NativeResponse:
    """Successful bounded CLI response before provider data interpretation.

    :param headers: Native HTTP headers, empty for raw-text requests.
    :param text: Provider response body without the included HTTP headers.

    .. attribute :: headers
        :type: str

        Native HTTP headers, empty for raw-text requests.

    .. attribute :: text
        :type: str

        Provider response body without the included HTTP headers.
    """

    headers: str
    text: str


def validate_endpoint(value: str) -> str:
    """Reject host changes and traversal before handing a path to the CLI.

    :param value: Caller-selected relative provider endpoint.
    :returns: The validated endpoint without changing its query.
    :raises APIError: If the endpoint is empty, malformed, absolute, option-like,
        contains a fragment, or traverses through dot path components.
    """
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise APIError("invalid_relative_endpoint") from None
    if (
        not value
        or value.startswith(("/", "-"))
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or any(part in (".", "..") for part in parsed.path.split("/"))
    ):
        raise APIError("invalid_relative_endpoint")
    return value


def _split_response(stdout: str) -> tuple[str, str]:
    """Separate native CLI HTTP headers from the response body.

    :param stdout: Native response, optionally beginning with HTTP header blocks.
    :returns: Last header block and remaining body, or empty headers and unchanged
        stdout when the response does not start with an HTTP status line.
    :raises APIError: If a leading HTTP header block has no terminating blank line.
    """
    if not stdout.startswith("HTTP/"):
        return "", stdout
    remaining = stdout
    headers = ""
    while remaining.startswith("HTTP/"):
        crlf = remaining.find("\r\n\r\n")
        lf = remaining.find("\n\n")
        choices = [(crlf, 4), (lf, 2)]
        available = [(offset, size) for offset, size in choices if offset >= 0]
        if not available:
            raise APIError("invalid_http_headers")
        offset, size = min(available)
        headers = remaining[:offset]
        remaining = remaining[offset + size :]
    return headers, remaining


def _status_code(headers: str) -> Optional[int]:
    """Extract the status from a native HTTP response header.

    :param headers: Header text beginning with the HTTP status line.
    :returns: Integer status code, or ``None`` when no status line is present.
    """
    status = re.match(r"HTTP/\S+\s+(\d{3})\b", headers)
    return int(status.group(1)) if status else None


def http_failure(code: Optional[int]) -> Optional[str]:
    """Classify an HTTP status for the shared provider retry policy.

    :param code: Provider status, or None when transport failed before a response.
    :returns: Classified HTTP failure, or None for a non-error response.
    """
    if code is None:
        return None
    if code == 401:
        return "authentication"
    if code == 403:
        return "authorization"
    if code == 408:
        return "provider_408"
    if code == 429:
        return "rate_limited"
    if 400 <= code <= 499:
        return f"provider_{code}"
    if 500 <= code <= 599:
        return "provider_5xx"
    return None


def _failure(result: CommandResult, headers: str) -> str:
    """Classify a failed native command without exposing response content.

    :param result: Native command exit status, stdout, and stderr.
    :param headers: Parsed HTTP response headers, if available.
    :returns: Stable HTTP, transport, timeout, or command failure reason.
    """
    reason = http_failure(_status_code(headers))
    if reason is not None:
        return reason
    known = error_class(result)
    if known != "command_failed":
        return known
    status = re.search(r"\b([45]\d\d)\b", result.stderr)
    return (http_failure(int(status.group(1))) if status else None) or known


def _delay(
    stderr: str, endpoint: str, attempt: int, maximum: int = MAX_RETRY_AFTER_SECONDS
) -> float:
    """Calculate a bounded delay before retrying a provider read.

    :param stderr: Header or diagnostic text that may contain ``Retry-After``.
    :param endpoint: Relative endpoint used to make retry jitter deterministic.
    :param attempt: Zero-based failed attempt used for exponential backoff.
    :param maximum: Largest accepted ``Retry-After`` delay in seconds.
    :returns: Delay in seconds from a valid header or bounded backoff with jitter.
    :raises APIError: If the provider requests a delay above the configured limit.
    """
    found = _RETRY_AFTER.search(stderr)
    if found:
        raw = found.group(1).strip()
        if raw.isdecimal():
            seconds = float(raw)
        else:
            try:
                date = parsedate_to_datetime(raw)
                seconds = max(
                    0.0,
                    (date.astimezone(UTC) - datetime.now(UTC)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                seconds = -1.0
        if seconds >= 0:
            if seconds > maximum:
                raise APIError("retry_after_exceeds_budget")
            return seconds
    base = 0.2 * 2**attempt
    digest = hashlib.sha256(f"{endpoint}:{attempt}".encode()).digest()
    return base + (digest[0] / 255) * base * 0.2


def read_retry_delay(
    method: str,
    reason: str,
    headers: str,
    endpoint: str,
    attempt: int,
    *,
    maximum: int = MAX_RETRY_AFTER_SECONDS,
) -> float:
    """Apply one shared bounded-read policy to CLI and direct HTTP transports.

    :param method: Requested HTTP method.
    :param reason: Sanitized transport or HTTP classification.
    :param headers: Response headers containing any Retry-After value.
    :param endpoint: Selected relative endpoint, used for bounded jitter.
    :param attempt: Zero-based failed attempt.
    :param maximum: Maximum accepted Retry-After delay in seconds.
    :returns: Delay before the next permitted GET attempt.
    :raises APIError: If the failure is terminal, exhausted, or exceeds the delay budget.
    """
    if method != "GET" or reason not in _RETRYABLE or attempt + 1 >= READ_ATTEMPTS:
        raise APIError(reason, attempts=attempt + 1)
    return _delay(headers, endpoint, attempt, maximum)


class CLIAPIClient:
    """Call provider using one bound session and an injectable command runner.

    JSON bodies travel in a mode-0600 temporary file rather than argv, where
    issue text and the one-time runner response must not be exposed. The
    transport never reports raw provider stderr or a response body on failure.
    """

    def __init__(
        self,
        command: CommandRunner = run_command_bounded,
        *,
        timeout: int = 25,
        sleep: Callable[[float], None] = time.sleep,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        max_retry_after: int = MAX_RETRY_AFTER_SECONDS,
    ):
        """Bind the provider transport to a command runner and timeout.

        :param command: Injectable command executor for provider requests.
        :param timeout: Positive per-request timeout in seconds.
        :param sleep: Delay function used for bounded retries.
        :param max_response_bytes: Maximum response size retained for a request.
        :param max_retry_after: Maximum accepted Retry-After delay in seconds.
        :raises ValueError: If a timeout or response bound is invalid.
        """
        if timeout <= 0 or max_response_bytes <= 0 or max_retry_after < 0:
            raise ValueError("invalid timeout or response bounds")
        self._command = command
        self._sleep = sleep
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_retry_after = max_retry_after

    def get_text(
        self, session: APISession, endpoint: str, *, paginate: bool = False
    ) -> str:
        """Read native response text through the same bounded GET transport.

        :param session: Exact-host session with resolved credential overrides.
        :param endpoint: Relative API path at the selected provider.
        :param paginate: Whether the native client collects response pages.
        :returns: Response text without included HTTP headers.
        :raises APIError: If transport, retry, or response limits fail.
        :raises OSError: If the native runner cannot start or read the selected process.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        return self._exchange(
            session, "GET", endpoint, raw=True, paginate=paginate
        ).text

    def pagination_arguments(self) -> Sequence[str]:
        """Supply native pagination switches for a collection read.

        :returns: Arguments supported by the provider CLI adapter.
        """
        return ("--paginate",)

    def argv(self, host: str, method: str, endpoint: str) -> MutableSequence[str]:
        """Build the provider CLI argument vector.

        :param host: Exact host of the bound credential.
        :param method: HTTP operation selected by the atomic owner.
        :param endpoint: Validated provider-relative API path.
        :returns: Mutable provider CLI arguments, extended with body and pagination
            options by the shared request transport.
        :raises NotImplementedError: If a provider has no CLI adapter.
        """
        raise NotImplementedError("provider_cli_required")

    def get_json(
        self,
        session: APISession,
        endpoint: str,
        *,
        deadline: Optional[RequestDeadline] = None,
    ) -> JSONDocument:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :param deadline: Optional shared absolute deadline for all GET attempts.
        :returns: Decoded response object or array with validated JSON values.
        :raises APIError: If the endpoint is invalid, a provider failure is terminal
            or exhausts GET retries, response bytes exceed the configured limit,
            or the response is not a valid JSON object or array.
        :raises OSError: If the native runner cannot start or read the selected process.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        return self._request(session, "GET", endpoint, deadline=deadline)

    def get_json_with_headers(
        self, session: APISession, endpoint: str
    ) -> tuple[JSONDocument, str]:
        """Read one JSON page and retain its HTTP pagination headers.

        :param session: Exact-host bound provider session.
        :param endpoint: Relative provider API endpoint.
        :returns: Decoded response object or array and HTTP headers from the same read.
        :raises APIError: If the endpoint is invalid, a terminal provider failure or
            exhausted retries prevents the read, the response exceeds its byte limit,
            pagination headers are missing or malformed, or JSON has an invalid shape.
        :raises OSError: If the native runner cannot start or read the selected process.
        """  # noqa: DOC503 - The native command runner may propagate OSError.
        response = self._exchange(session, "GET", endpoint)
        if not response.headers.startswith("HTTP/"):
            raise APIError("pagination_headers_missing")
        return self._decode_response(response.text, "GET"), response.headers

    def post_json(
        self, session: APISession, endpoint: str, body: JSONObject
    ) -> JSONDocument:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :param body: JSON fields passed through a private temporary file.
        :returns: Decoded response object or array with validated JSON values.
        :raises APIError: If the endpoint or body is invalid, the one POST attempt
            fails, response bytes exceed the limit, or JSON is not an object or array.
        :raises OSError: If private-body file I/O or the native runner fails.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        return self._request(session, "POST", endpoint, body)

    def put_json(
        self, session: APISession, endpoint: str, body: JSONObject
    ) -> JSONDocument:
        """Call the bound provider through the shared JSON transport.

        :param session: Exact-host authenticated provider session.
        :param endpoint: Relative provider API endpoint.
        :param body: JSON fields passed through a private temporary file.
        :returns: Decoded response object or array with validated JSON values.
        :raises APIError: If the endpoint or body is invalid, the one PUT attempt
            fails, response bytes exceed the limit, or JSON is not an object or array.
        :raises OSError: If private-body file I/O or the native runner fails.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        return self._request(session, "PUT", endpoint, body)

    def delete_json(self, session: APISession, endpoint: str) -> JSONDocument:
        """Issue a DELETE and accept provider's empty 204 response.

        :param session: Bound provider session.
        :param endpoint: Relative provider API endpoint to delete.
        :returns: Response object or array; an empty successful body returns an empty mapping.
        :raises APIError: If the endpoint is invalid, the one DELETE attempt fails,
            response bytes exceed the limit, or a nonempty response is not a valid
            JSON object or array.
        :raises OSError: If the native runner cannot start or read the selected process.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        return self._request(session, "DELETE", endpoint)

    def _request(
        self,
        session: APISession,
        method: str,
        endpoint: str,
        body: Optional[JSONObject] = None,
        *,
        deadline: Optional[RequestDeadline] = None,
    ) -> JSONDocument:
        """Request and decode one provider object or array.

        :param session: Provider hostname and selected credential environment.
        :param method: HTTP method; only GET requests may be retried.
        :param endpoint: Relative provider path validated before execution.
        :param body: Request fields; ``None`` sends no JSON body.
        :param deadline: Optional absolute deadline bounding retries and each request.
        :returns: Decoded object or array; an empty DELETE body returns an empty mapping.
        :raises APIError: If request validation, transport, retry limits, or response
            decoding fails.
        :raises OSError: If private-body file I/O or the native command runner fails.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        response = self._exchange(session, method, endpoint, body, deadline=deadline)
        return self._decode_response(response.text, method)

    def _exchange(
        self,
        session: APISession,
        method: str,
        endpoint: str,
        body: Optional[JSONObject] = None,
        *,
        raw: bool = False,
        paginate: bool = False,
        deadline: Optional[RequestDeadline] = None,
    ) -> NativeResponse:
        """Execute a bounded request while owning its private body file.

        :param session: Exact hostname and child credential overrides.
        :param method: HTTP operation supplied by the provider service.
        :param endpoint: Provider-relative request path.
        :param body: Request fields; ``None`` omits the input file.
        :param raw: Whether to retain stdout verbatim without included headers.
        :param paginate: Whether the native client should collect all pages.
        :param deadline: Optional absolute deadline shared by related reads.
        :returns: Successful response headers and text within the configured byte bound.
        :raises APIError: If the endpoint, body, transport, or response size is invalid.
        :raises OSError: If private-body file I/O or the native command runner fails.
        """  # noqa: DOC502 - Shared transport and request helpers propagate failures.
        selected = validate_endpoint(endpoint)
        argv = self.argv(session.host, method, selected)
        if raw:
            argv.remove("--include")
        if paginate:
            argv.extend(self.pagination_arguments())
        with self._body_arguments(body) as arguments:
            argv.extend(arguments)
            return self._read_response(
                session, method, selected, argv, raw=raw, deadline=deadline
            )

    @staticmethod
    @contextmanager
    def _body_arguments(body: Optional[JSONObject]) -> Iterator[Sequence[str]]:
        """Keep a private native-CLI input file alive for the request lifetime.

        :param body: JSON fields to serialize; ``None`` needs no temporary file.
        :yields: Native content-type and input-file arguments, or an empty tuple.
        :raises APIError: If the body contains unsupported values, nonfinite
            numbers, circular references, excessive nesting, or invalid UTF-8 text.
        :raises OSError: If the file cannot be created, flushed, or removed.
        :ytype: Sequence[str]
        """  # noqa: DOC503 - Temporary-file create, write, and removal can fail.
        if body is None:
            yield ()
            return
        try:
            encoded = json.dumps(
                copy_json_object(body),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError):
            raise APIError("invalid_request_body") from None
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix="ci-skills-api-", suffix=".json"
        ) as handle:
            handle.write(encoded)
            handle.flush()
            yield ("--header", "Content-Type: application/json", "--input", handle.name)

    def _execute(
        self,
        argv: Sequence[str],
        session: APISession,
        deadline: Optional[RequestDeadline],
    ) -> CommandResult:
        """Run a native request with the selected environment and output bound.

        :param argv: Complete provider CLI argument sequence.
        :param session: Credential environment selected for this exact provider.
        :param deadline: Optional absolute deadline restricting this request.
        :raises APIError: The shared request deadline has expired.
        :returns: Native exit status and bounded stdout/stderr.
        :raises OSError: If the injected runner cannot start the native process or read its pipes.
        """  # noqa: DOC502 - The injected native command runner may propagate OSError.
        timeout = min(self.timeout, deadline.remaining()) if deadline else self.timeout
        if self.max_response_bytes == MAX_RESPONSE_BYTES:
            return self._command(argv, timeout=timeout, env=dict(session.environment))
        return self._command(
            argv,
            timeout=timeout,
            env=dict(session.environment),
            max_stdout_bytes=self.max_response_bytes,
        )

    def _read_response(
        self,
        session: APISession,
        method: str,
        endpoint: str,
        argv: Sequence[str],
        *,
        raw: bool,
        deadline: Optional[RequestDeadline] = None,
    ) -> NativeResponse:
        """Apply the shared retry policy and retain the successful native response.

        :param session: Bound provider identity and credential environment.
        :param method: HTTP method, determining whether retries are permitted.
        :param endpoint: Validated relative path used in traces and retry jitter.
        :param argv: Native CLI request, reused unchanged across attempts.
        :param raw: Whether stdout is plain endpoint text without included headers.
        :param deadline: Optional absolute deadline shared by requests and retry delays.
        :returns: Successful response text and any included HTTP headers.
        :raises APIError: If a failure is terminal, retry limits are reached,
            included headers are malformed, or stdout exceeds the response bound.
        :raises OSError: If the injected native process runner fails before producing a result.
        """  # noqa: DOC503 - The native command runner may propagate OSError.
        attempt = 0
        while True:
            result = self._execute(argv, session, deadline)
            if result.returncode == 124:
                headers, text = "", ""
            elif raw and result.returncode == 0:
                headers, text = "", result.stdout
            else:
                headers, text = _split_response(result.stdout)
            trace_event(
                "http.completed",
                {
                    "method": method,
                    "host": session.host,
                    "path": endpoint,
                    "attempt": attempt + 1,
                    "status_code": _status_code(headers),
                    "returncode": result.returncode,
                },
            )
            if result.returncode == 0:
                if len(result.stdout.encode("utf-8")) > self.max_response_bytes:
                    raise APIError("response_too_large")
                return NativeResponse(headers=headers, text=text)
            if deadline is not None:
                deadline.remaining()
            delay = read_retry_delay(
                method,
                _failure(result, headers),
                headers or result.stderr,
                endpoint,
                attempt,
                maximum=self.max_retry_after,
            )
            self._sleep(min(delay, deadline.remaining()) if deadline else delay)
            attempt += 1

    @staticmethod
    def _decode_response(text: str, method: str) -> JSONDocument:
        """Decode a provider object or array after native transport succeeds.

        :param text: Response body without HTTP headers.
        :param method: HTTP operation; DELETE permits an empty successful body.
        :returns: Validated object or array, or an empty mapping for empty DELETE.
        :raises APIError: If JSON is malformed, nonfinite, too deeply nested,
            or contains a scalar instead of an object or array.
        """
        if method == "DELETE" and not text.strip():
            return {}
        try:
            payload = response_decoder().decode(text)
        except (ValueError, RecursionError) as exc:
            raise APIError("invalid_json") from exc
        if not isinstance(payload, (Mapping, list)):
            raise APIError("invalid_shape")
        return payload
