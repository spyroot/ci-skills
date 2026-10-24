"""Collect and filter Kubernetes events within a time window.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from datetime import UTC, datetime
from typing import Optional

from lib.python.access.kubernetes import kubectl_argv, kubernetes_env
from lib.python.core.collection import CommandReadError, _read_json, _search
from lib.python.core.delivery_state import DeliveryState
from lib.python.core.json_types import JSONObject, JSONValue
from lib.python.core.kubernetes.collect import _items, _meta
from lib.python.core.kubernetes.kubernetes import KubernetesService
from lib.python.core.kubernetes.objects import KubernetesObject
from lib.python.core.report import report
from lib.python.core.runtime import sanitize
from lib.python.core.target import Target, kubernetes_label
from lib.python.core.time_window import DEFAULT_WINDOW, _timestamp, parse_window


def _event_data(
    target: Target,
) -> tuple[
    Mapping[str, Sequence[JSONObject]],
    MutableSequence[Mapping[str, str]],
    Optional[str],
]:
    """Read Events with a core/v1 fallback only when the preferred API is absent.

    :param target: Bound Kubernetes target for both event list queries.
    :returns: Event lists keyed by API source, classified read errors, and the
        fallback reason when the preferred resource was unavailable.
    :raises TargetError: If pinned kubeconfig sources changed or are unreadable.
    :raises OSError: If a native process cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    preferred_key = "events_v1"
    try:
        preferred = _read_json(
            kubectl_argv(target, "get", "events.events.k8s.io", "-A", "-o", "json"),
            env=kubernetes_env(target),
        )
        return {preferred_key: _items(preferred)}, [], None
    except (CommandReadError, TypeError) as exc:
        preferred_reason = str(exc)
    if preferred_reason != "resource_unavailable":
        return (
            {preferred_key: []},
            [{"source": preferred_key, "reason": preferred_reason}],
            None,
        )
    core_key = "core_events"
    try:
        core = _read_json(
            kubectl_argv(target, "get", "events", "-A", "-o", "json"),
            env=kubernetes_env(target),
        )
        return (
            {core_key: _items(core)},
            [{"source": preferred_key, "reason": "compatibility_fallback"}],
            preferred_reason,
        )
    except (CommandReadError, TypeError) as exc:
        return (
            {preferred_key: [], core_key: []},
            [
                {"source": preferred_key, "reason": preferred_reason},
                {"source": core_key, "reason": str(exc)},
            ],
            preferred_reason,
        )


def _event_parts(
    event: JSONObject,
) -> tuple[JSONObject, JSONObject, JSONObject]:
    """Validate the nested metadata, object reference, and series of an Event.

    :param event: Decoded Kubernetes Event object.
    :returns: Metadata, involved object, and series mappings.
    :raises TypeError: The object name, timestamps, message fields, or count
        fields have an invalid shape.
    """
    meta = _meta(event)
    obj = event["regarding"] if "regarding" in event else event.get("involvedObject")
    series = event.get("series", {})
    if series is None:
        series = {}
    if (
        not isinstance(obj, dict)
        or not isinstance(series, dict)
        or not isinstance(obj.get("name"), str)
        or not obj["name"]
    ):
        raise TypeError("invalid_event_record")
    # ObjectReference.kind is optional in the Kubernetes Event API. A name
    # still identifies the record for the filters and report below.
    for container, fields in (
        (meta, ("namespace", "creationTimestamp")),
        (obj, ("kind", "namespace", "name", "uid")),
        (series, ("lastObservedTime",)),
        (
            event,
            (
                "eventTime",
                "firstTimestamp",
                "lastTimestamp",
                "deprecatedLastTimestamp",
                "reason",
                "type",
                "note",
                "message",
            ),
        ),
    ):
        if any(
            container.get(field) is not None and not isinstance(container[field], str)
            for field in fields
        ):
            raise TypeError("invalid_event_record")
    for container, field in (
        (series, "count"),
        (event, "deprecatedCount"),
        (event, "count"),
    ):
        value = container.get(field)
        if value is not None and (type(value) is not int or value < 0):
            raise TypeError("invalid_event_record")
    return meta, obj, series


class EventInventory:
    """Filter one event inventory using a validated time window and object filters."""

    def __init__(self, args: Namespace):
        """Resolve the requested event window before any provider query.

        :param args: Relative or absolute time window and event selection filters.
        :raises ValueError: If time options conflict, timestamps are invalid, or
            the start occurs after the end.
        :raises OverflowError: If duration arithmetic exceeds datetime limits.
        """  # noqa: DOC503 - Duration and datetime arithmetic can overflow.
        self.args = args
        last = getattr(args, "last", None)
        if last is not None and (args.from_time or args.to_time):
            raise ValueError("--last cannot be combined with --from or --to")
        self.end = _timestamp(args.to_time) if args.to_time else datetime.now(UTC)
        if last is not None:
            self.start = self.end - parse_window(last)
        elif args.from_time:
            self.start = _timestamp(args.from_time)
        else:
            self.start = self.end - DEFAULT_WINDOW
        if self.start > self.end:
            raise ValueError("--from must not be after --to")

    def record(self, event: JSONObject, source: str) -> JSONObject:
        """Validate one Event and apply window, object, and reason filters.

        :param event: Native Event from the preferred or compatibility API.
        :param source: Resource-list key retained as report provenance.
        :returns: Matching record, or an empty object when the event is filtered.
        :raises TypeError: If event fields needed for identity or reporting are malformed.
        :raises ValueError: If observed timestamps are absent or invalid.
        """  # noqa: DOC503 - KubernetesObject field readers raise TypeError.
        meta, obj, series = _event_parts(event)
        event_view, meta_view = KubernetesObject(event), KubernetesObject(meta)
        object_view, series_view = KubernetesObject(obj), KubernetesObject(series)
        first_source = (
            event_view.text("eventTime")
            or event_view.text("firstTimestamp")
            or meta_view.text("creationTimestamp")
        )
        last_source = (
            series_view.text("lastObservedTime")
            or event_view.text("lastTimestamp")
            or event_view.text("deprecatedLastTimestamp")
            or first_source
        )
        if not last_source:
            raise ValueError("invalid_event_timestamp")
        try:
            event_time = _timestamp(last_source)
            first_time = _timestamp(first_source) if first_source else event_time
        except ValueError as exc:
            raise ValueError("invalid_event_timestamp") from exc
        if self.start <= event_time <= self.end:
            matching_time = event_time
        elif self.start <= first_time <= self.end:
            matching_time = first_time
        else:
            return {}
        namespace = meta_view.text("namespace") or object_view.text("namespace")
        kind = object_view.text("kind")
        name = object_view.text("name")
        reason = event_view.text("reason")
        if (
            self.args.namespace != "all"
            and namespace != self.args.namespace
            or self.args.kind
            and (kind or "").casefold() != self.args.kind.casefold()
            or self.args.object
            and name != self.args.object
            or self.args.reason
            and self.args.reason.casefold() not in (reason or "").casefold()
        ):
            return {}
        return {
            "timestamp": matching_time.isoformat(),
            "namespace": namespace,
            "first_timestamp": first_time.isoformat(),
            "last_timestamp": event_time.isoformat(),
            "kind": kind,
            "name": name,
            "object_uid": object_view.text("uid"),
            "reason": reason,
            "type": event_view.text("type"),
            "message": sanitize(
                event_view.text("note") or event_view.text("message") or "", 1000
            ),
            "count": series.get("count")
            or event.get("deprecatedCount")
            or event.get("count")
            or 1,
            "source": source,
        }

    def rows(
        self,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
    ) -> Sequence[JSONObject]:
        """Select and deduplicate records while retaining every malformed event error.

        :param data: Event lists keyed by their API source.
        :param errors: Read errors extended with invalid record or timestamp failures.
        :returns: Matching unique rows sorted by timestamp, namespace, and name.
        """
        rows: MutableSequence[JSONObject] = []
        seen: set[tuple[str, Optional[str], Optional[str], str]] = set()
        for source in ("core_events", "events_v1"):
            for event in data.get(source, []):
                try:
                    row = self.record(event, source)
                except (TypeError, ValueError) as exc:
                    errors.append({"source": source, "reason": str(exc)})
                    continue
                if not row:
                    continue
                view = KubernetesObject(row)
                key = (
                    view.required_text("timestamp"),
                    view.text("object_uid"),
                    view.text("reason"),
                    view.text("message") or "",
                )
                if key not in seen and _search(row, self.args.search):
                    rows.append(row)
                    seen.add(key)
        return sorted(
            rows,
            key=lambda item: (
                str(item["timestamp"]),
                str(item["namespace"] or ""),
                str(item["name"] or ""),
            ),
        )

    def report(
        self,
        target: Target,
        data: Mapping[str, Sequence[JSONObject]],
        errors: MutableSequence[Mapping[str, str]],
        fallback_reason: Optional[str] = None,
    ) -> MutableMapping[str, JSONValue]:
        """Build the event report and disclose any compatibility API fallback.

        :param target: Kubernetes authority named in the report.
        :param data: Event lists from the preferred or fallback API.
        :param errors: Read failures extended while validating event records.
        :param fallback_reason: Native failure that justified using core/v1, if any.
        :returns: JSON report with selected rows, window, filters, and source failures.
        :raises ValueError: If the target has no Kubernetes authority.
        """  # noqa: DOC502 - kubernetes_label requires a selected authority.
        rows = self.rows(data, errors)
        result = report(
            "event_trace",
            kubernetes_label(target),
            {
                "from": self.start.isoformat(),
                "to": self.end.isoformat(),
                "namespace": self.args.namespace,
                "kind": self.args.kind,
                "object": self.args.object,
                "reason": self.args.reason,
                "search": self.args.search,
            },
            rows,
            errors,
        )
        if fallback_reason:
            result["compatibility_fallback"] = {
                "preferred_api": "events.k8s.io/v1",
                "reason": fallback_reason,
            }
        return result


def collect_events(
    target: Target, args: Namespace, *, state: DeliveryState | None = None
) -> MutableMapping[str, JSONValue]:
    """Read cluster Events for a validated time window and object selection.

    :param target: Bound Kubernetes authority used for Event list queries.
    :param args: Relative/absolute time window and namespace, object, reason, text filters.
    :param state: Shared declaration and previously observed Kubernetes access.
    :returns: Event report with unique sorted rows and independent source errors.
    :raises ValueError: If time options conflict, a timestamp is invalid, or the
        target has no Kubernetes authority.
    :raises OverflowError: If the requested duration exceeds datetime limits.
    :raises TargetError: If pinned Kubernetes credentials changed before a read.
    :raises OSError: If a native Event query cannot be started.
    """  # noqa: DOC502 - Target/query helpers and worker reads propagate failures.
    KubernetesService(target, state=state).require_access()
    inventory = EventInventory(args)
    data, errors, fallback_reason = _event_data(target)
    return inventory.report(target, data, errors, fallback_reason)
