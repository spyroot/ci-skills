"""Agent cleanup record: stage reads, refusals, and acceptance by the series check.

`tools/qualify_agent_cleanup.py` reads one stage after the confirmed delete and one
after the repeat delete, then attaches the ``gitlab_agent_smoke_cleanup`` record to
the delete NO_OP receipt. These tests drive it with the sanitized live receipts of
run ``9bf3d911…`` under ``tests/fixtures/gitlab-agent/`` and give its output to the
same ``check_agent_series`` that `tools/check_live_acceptance.py` runs.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Final, Optional

import pytest
from conftest import REPO_ROOT, import_script_module, load_module

CLI = load_module(
    "qualify_agent_cleanup_cli", REPO_ROOT / "tools" / "qualify_agent_cleanup.py"
)
CLEANUP = sys.modules[CLI.observe_stage.__module__]
EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
AUTHORITY = import_script_module("lib.python.core.gitlab.cluster_agent.authority")
ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
SCHEMA = import_script_module("lib.python.core.schema")

FIXTURES: Final = REPO_ROOT / "tests" / "fixtures" / "gitlab-agent"
CONNECT_FIXTURE: Final = FIXTURES / "connect-applied.json"
REPEAT_FIXTURE: Final = FIXTURES / "delete-no-op.json"
RESULT_SCHEMA: Final = "results/gitlab-agent.schema.json"
PRESENT: Final = object()
OTHER_UID: Final = "00000000-0000-4000-8000-0000000000ff"
OTHER_CLUSTER: Final = "other-cluster-uid"
CONNECT: Final = "connect"
VERIFY: Final = "verify"
DELETE: Final = "delete"
APPLIED: Final = "APPLIED"
NO_OP: Final = "NO_OP"
VERIFIED: Final = "VERIFIED"
SERIES: Final = (
    (CONNECT, APPLIED),
    (CONNECT, NO_OP),
    (VERIFY, VERIFIED),
    (DELETE, APPLIED),
    (DELETE, NO_OP),
)
NAMESPACE_KIND: Final = "Namespace"
FILES: Final = MappingProxyType(
    {
        "connect": "connect.json",
        "after": "after.json",
        "repeat": "repeat.json",
        "receipt": "delete-no-op.json",
        "out": "delete-no-op-with-cleanup.json",
    }
)


def _fixture(path: Path) -> dict[str, Any]:
    """Load one sanitized live receipt."""
    return json.loads(path.read_text(encoding="utf-8"))


def _ownership() -> dict[str, Any]:
    """Return the run's original ownership record."""
    return deepcopy(CLEANUP.connect_ownership(_fixture(CONNECT_FIXTURE)))


def _borrowed(ownership: Mapping[str, Any]) -> dict[str, Any]:
    """Mark the namespace row as borrowed, as a run in an existing namespace records it."""
    changed = deepcopy(dict(ownership))
    for row in changed["resources"]:
        if row["kind"] == NAMESPACE_KIND:
            row["created"] = False
    return changed


class Agents:
    """Answer the installed provider reads with a native 404 unless told otherwise."""

    def __init__(self, agent: Optional[object] = None, token: Optional[object] = None):
        """Hold the two answers.

        :param agent: Registration returned by ``read_agent``, or None for 404.
        :param token: Token returned by ``read_token``, or None for 404.
        """
        self.agent = agent
        self.token = token
        self.calls: list[tuple[object, ...]] = []

    def read_agent(self, agent_id: int) -> Optional[object]:
        """Record and answer one registration read."""
        self.calls.append(("agent", agent_id))
        return self.agent

    def read_token(self, agent_id: int, token_id: int) -> Optional[object]:
        """Record and answer one token read."""
        self.calls.append(("token", agent_id, token_id))
        return self.token


class Objects:
    """Answer Kubernetes reads from a fixed set of live objects."""

    def __init__(self, live: Mapping[tuple[str, str, str], str]):
        """Hold the live objects by kind, name and namespace with their UID.

        :param live: UID of each object that still exists.
        """
        self.live = dict(live)
        self.calls: list[tuple[str, str, str]] = []

    def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
        """Return the object with its UID, or None when it does not exist."""
        self.calls.append((kind, name, namespace))
        uid = self.live.get((kind, name, namespace))
        return None if uid is None else {"metadata": {"name": name, "uid": uid}}


def _live_borrowed(ownership: Mapping[str, Any]) -> dict[tuple[str, str, str], str]:
    """Keep only the rows the run did not create, with their recorded UID."""
    return {
        (row["kind"], row["name"], row["namespace"]): row["uid"]
        for row in ownership["resources"]
        if not row["created"]
    }


def _stage(ownership: Mapping[str, Any]) -> dict[str, object]:
    """Read one clean stage through the fakes."""
    return CLEANUP.observe_stage(
        Agents(), Objects(_live_borrowed(ownership)), ownership, lambda: None
    )


@pytest.mark.parametrize("borrowed", (False, True), ids=("owned-namespace", "borrowed"))
def test_stage_reads_both_404s_and_every_owned_row(borrowed: bool) -> None:
    """A clean stage carries one row per ownership resource and both 404 reads."""
    ownership = _borrowed(_ownership()) if borrowed else _ownership()
    agents = Agents()
    objects = Objects(_live_borrowed(ownership))
    logical: list[bool] = []

    stage = CLEANUP.observe_stage(
        agents, objects, ownership, lambda: logical.append(True)
    )

    rows = ownership["resources"]
    assert agents.calls == [
        ("agent", ownership["agent_id"]),
        ("token", ownership["agent_id"], ownership["registration_credential_id"]),
    ]
    assert objects.calls == [(r["kind"], r["name"], r["namespace"]) for r in rows]
    assert logical == [True]
    assert (
        stage["agent"]["http_status"]
        == stage["registration_credential"]["http_status"]
        == 404
    )
    assert [(r["absent"], r["preserved"]) for r in stage["resources"]] == [
        (row["created"], not row["created"]) for row in rows
    ]
    assert [r["observed_uid"] for r in stage["resources"]] == [
        None if row["created"] else row["uid"] for row in rows
    ]


def _present_agent(ownership: Mapping[str, Any]) -> tuple[Agents, Objects]:
    return Agents(agent=PRESENT), Objects({})


def _present_token(ownership: Mapping[str, Any]) -> tuple[Agents, Objects]:
    return Agents(token=PRESENT), Objects({})


def _present_resource(ownership: Mapping[str, Any]) -> tuple[Agents, Objects]:
    row = ownership["resources"][-1]
    return Agents(), Objects({(row["kind"], row["name"], row["namespace"]): row["uid"]})


@pytest.mark.parametrize(
    ("answers", "reason"),
    (
        (_present_agent, "agent_cleanup_registration_present"),
        (_present_token, "agent_cleanup_credential_present"),
        (_present_resource, "agent_cleanup_resource_present"),
    ),
    ids=("registration", "token", "run-created-object"),
)
def test_stage_refuses_a_present_object_before_the_logical_read(
    answers: Callable[[Mapping[str, Any]], tuple[Agents, Objects]], reason: str
) -> None:
    """Any surviving run object stops the stage without a record."""
    ownership = _ownership()
    agents, objects = answers(ownership)
    logical: list[bool] = []

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.observe_stage(agents, objects, ownership, lambda: logical.append(True))

    assert str(refused.value).split(":", 1)[0] == reason
    assert logical == []


def test_stage_refuses_a_borrowed_object_replaced_under_the_same_name() -> None:
    """A borrowed row must still hold its recorded UID."""
    ownership = _borrowed(_ownership())
    live = {key: OTHER_UID for key in _live_borrowed(ownership)}

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.observe_stage(Agents(), Objects(live), ownership, lambda: None)

    assert str(refused.value).startswith("agent_cleanup_borrowed_resource_changed")


def _series(start: datetime) -> dict[str, dict[str, Any]]:
    """Build the five receipts of run 9bf3d911 from its connect and repeat receipts."""
    connect = _fixture(CONNECT_FIXTURE)
    repeat = _fixture(REPEAT_FIXTURE)
    candidate = {
        key: connect[key]
        for key in ("execution_host", "tested_revision", "target", "skill")
    }
    receipts: dict[str, dict[str, Any]] = {}
    for offset, (operation, action) in enumerate(SERIES):
        receipt = deepcopy(
            repeat if (operation, action) == (DELETE, NO_OP) else connect
        )
        receipt.update(deepcopy(candidate))
        receipt["operation"] = operation
        receipt["captured_at"] = (start + timedelta(seconds=offset)).isoformat()
        record = receipt["records"][0]
        record["action"] = action
        if operation == VERIFY:
            record["ci_access"] = {"proof": {"captured_at": start.isoformat()}}
        if operation == DELETE:
            receipt["filters"] = deepcopy(repeat["filters"])
        receipts[f"{operation}-{action}"] = receipt
    return receipts


def test_attach_writes_a_record_the_series_check_accepts(tmp_path: Path) -> None:
    """The adapter's attach output completes the five-receipt series."""
    now = datetime.now(UTC)
    receipts = _series(now - timedelta(hours=1))
    ownership = CLEANUP.connect_ownership(receipts[f"{CONNECT}-{APPLIED}"])
    paths = {key: tmp_path / name for key, name in FILES.items()}
    documents = {
        "connect": receipts[f"{CONNECT}-{APPLIED}"],
        "after": _stage(ownership),
        "repeat": _stage(ownership),
        "receipt": receipts[f"{DELETE}-{NO_OP}"],
    }
    for key, document in documents.items():
        paths[key].write_text(json.dumps(document), encoding="utf-8")

    code = CLI.main(
        [
            CLI.ATTACH,
            "--connect-receipt",
            str(paths["connect"]),
            "--after",
            str(paths["after"]),
            "--repeat",
            str(paths["repeat"]),
            "--receipt",
            str(paths["receipt"]),
            "--out",
            str(paths["out"]),
        ]
    )

    assert code == 0
    attached = json.loads(paths["out"].read_text(encoding="utf-8"))
    SCHEMA.validate_specification(attached, RESULT_SCHEMA)
    series = [*(r for k, r in receipts.items() if k != f"{DELETE}-{NO_OP}"), attached]
    assert EVIDENCE.check_agent_series(series, now=datetime.now(UTC)) == ()
    without = [*(r for k, r in receipts.items())]
    assert EVIDENCE.check_agent_series(without, now=datetime.now(UTC)) == (
        "agent_series_cleanup_unverified",
    )


def test_attach_refuses_a_record_for_another_run(tmp_path: Path) -> None:
    """The record attaches only to the repeat delete of its own run."""
    receipts = _series(datetime.now(UTC) - timedelta(hours=1))
    connect = receipts[f"{CONNECT}-{APPLIED}"]
    ownership = CLEANUP.connect_ownership(connect)
    record = CLEANUP.cleanup_record(
        connect, _stage(ownership), _stage(ownership), datetime.now(UTC)
    )
    repeat = deepcopy(receipts[f"{DELETE}-{NO_OP}"])
    repeat["filters"]["run_id"] = "another-run"

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.attach_record(repeat, record)

    assert str(refused.value) == "agent_cleanup_run_mismatch"


def _authorities(ownership: Mapping[str, Any], cluster_uid: str) -> SimpleNamespace:
    """Stand in for the installed authorities bound to the recorded scope."""
    return SimpleNamespace(
        project=SimpleNamespace(
            id=ownership["project_id"], path=ownership["project_path"]
        ),
        cluster_uid=cluster_uid,
        gitlab=SimpleNamespace(
            session=SimpleNamespace(origin=ownership["origin"] + "/")
        ),
        provider=Agents(),
        kubernetes=Objects(_live_borrowed(ownership)),
    )


@pytest.mark.parametrize("cluster", ("recorded", "other"))
def test_observe_binds_the_recorded_scope_through_the_installed_lib(
    monkeypatch: pytest.MonkeyPatch, cluster: str
) -> None:
    """Observe selects the recorded project and release and refuses another cluster."""
    connect = _fixture(CONNECT_FIXTURE)
    ownership = CLEANUP.connect_ownership(connect)
    uid = ownership["cluster_uid"] if cluster == "recorded" else OTHER_CLUSTER
    authorities = _authorities(ownership, uid)
    selected: list[object] = []
    logical: list[object] = []
    monkeypatch.setattr(
        AUTHORITY,
        "resolve_agent_target",
        lambda *args, **kwargs: selected.append(args) or SimpleNamespace(),
    )
    monkeypatch.setattr(AUTHORITY, "observe_authorities", lambda *_a, **_k: authorities)
    monkeypatch.setattr(
        ABSENCE,
        "verify_agent_logical_absence",
        lambda _gitlab, _kubernetes, selection, **_kwargs: logical.append(selection),
    )

    if cluster == "other":
        with pytest.raises(CLEANUP.AgentCleanupError) as refused:
            CLI.observe(REPO_ROOT / "ci-skills", connect, CLI.DEFAULT_TIMEOUT)
        assert str(refused.value) == "agent_cleanup_scope_mismatch"
        assert logical == []
        return

    stage = CLI.observe(REPO_ROOT / "ci-skills", connect, CLI.DEFAULT_TIMEOUT)

    assert selected == [(None, None, ownership["project_path"], None)]
    assert [item.release for item in logical] == [ownership["release"]]
    assert logical[0].scope.run_id == ownership["run_id"]
    assert len(stage["resources"]) == len(ownership["resources"])
