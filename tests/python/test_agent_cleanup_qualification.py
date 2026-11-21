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
from conftest import REPO_ROOT, SKILL_ROOT, import_script_module, load_module

CLI = load_module(
    "qualify_agent_cleanup_cli", REPO_ROOT / "tools" / "qualify_agent_cleanup.py"
)
CLEANUP = sys.modules[CLI.observe_stage.__module__]
INSTALLED_COPY = sys.modules[CLI.installed_module.__module__]
EVIDENCE = import_script_module("lib.python.core.gitlab.cluster_agent.evidence")
AUTHORITY = import_script_module("lib.python.core.gitlab.cluster_agent.authority")
ABSENCE = import_script_module("lib.python.core.gitlab.cluster_agent.absence")
RECOVERY = import_script_module("lib.python.core.gitlab.cluster_agent.recovery")
PROVENANCE = import_script_module("lib.python.core.provenance")
STATUS = import_script_module("lib.python.sys.status")
SCHEMA = import_script_module("lib.python.core.schema")
ENDPOINTS = import_script_module("lib.python.core.endpoints")

KEYS: Final = CLEANUP.AgentKeys(
    RECOVERY.CREDENTIAL, RECOVERY.CREDENTIAL_ID, PROVENANCE.DIGEST_VALUE_KEY
)
FIXTURES: Final = REPO_ROOT / "tests" / "fixtures" / "gitlab-agent"
CONNECT_FIXTURE: Final = FIXTURES / "connect-applied.json"
REPEAT_FIXTURE: Final = FIXTURES / "delete-no-op.json"
RESULT_SCHEMA: Final = "results/gitlab-agent.schema.json"
PRESENT: Final = object()
OTHER_UID: Final = "00000000-0000-4000-8000-0000000000ff"
OTHER_CLUSTER: Final = "other-cluster-uid"
OTHER_DIGEST: Final = "f" * 64
OTHER_RUN: Final = "another-run"
BINDING: Final = "/consumer/.ci-skills/binding.toml"
PUBLIC: Final = ENDPOINTS.GITLAB_PUBLIC_SELECTOR
GITLAB_OPTION: Final = ENDPOINTS.GITLAB_TARGET_OPTION
CONNECT: Final = "connect"
VERIFY: Final = "verify"
DELETE: Final = "delete"
SERIES: Final = (
    (CONNECT, STATUS.APPLIED),
    (CONNECT, STATUS.NO_OP),
    (VERIFY, STATUS.VERIFIED),
    (DELETE, STATUS.APPLIED),
    (DELETE, STATUS.NO_OP),
)
CONNECT_KEY: Final = f"{CONNECT}-{STATUS.APPLIED}"
REPEAT_KEY: Final = f"{DELETE}-{STATUS.NO_OP}"
NAMESPACE_KIND: Final = "Namespace"
FILES: Final = MappingProxyType(
    {
        "connect": "connect.json",
        "after": "after.json",
        "repeat": "repeat.json",
        "receipt": "delete-no-op.json",
        "out": "delete-no-op-with-cleanup.json",
        "stage": "stage.json",
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

    def __init__(self, live: Mapping[tuple[str, str, str], Optional[str]]):
        """Hold the live objects by kind, name and namespace with their UID.

        :param live: UID of each object that still exists; None for an object
            returned without ``metadata.uid``.
        """
        self.live = dict(live)
        self.calls: list[tuple[str, str, str]] = []

    def read(self, kind: str, name: str, namespace: str) -> Optional[dict]:
        """Return the object with its UID, or None when it does not exist."""
        key = (kind, name, namespace)
        self.calls.append(key)
        if key not in self.live:
            return None
        uid = self.live[key]
        return {"metadata": {"name": name} | ({} if uid is None else {"uid": uid})}


def _live_borrowed(ownership: Mapping[str, Any]) -> dict[tuple[str, str, str], str]:
    """Keep only the rows the run did not create, with their recorded UID."""
    return {
        (row["kind"], row["name"], row["namespace"]): row["uid"]
        for row in ownership["resources"]
        if not row["created"]
    }


def _observation(ownership: Mapping[str, Any]) -> dict[str, object]:
    """Read one clean stage through the fakes."""
    return CLEANUP.observe_stage(
        Agents(), Objects(_live_borrowed(ownership)), ownership, lambda: None, KEYS
    )


def _stage(connect: Mapping[str, Any], at: datetime) -> dict[str, object]:
    """Wrap a clean stage as the copy that wrote ``connect`` reads it at ``at``."""
    ownership = CLEANUP.connect_ownership(connect)
    return CLEANUP.stage_file(
        ownership, _observation(ownership), connect["skill"], at, KEYS
    )


@pytest.mark.parametrize("borrowed", (False, True), ids=("owned-namespace", "borrowed"))
def test_stage_reads_both_404s_and_every_owned_row(borrowed: bool) -> None:
    """A clean stage carries one row per ownership resource and both 404 reads."""
    ownership = _borrowed(_ownership()) if borrowed else _ownership()
    agents = Agents()
    objects = Objects(_live_borrowed(ownership))
    logical: list[bool] = []

    stage = CLEANUP.observe_stage(
        agents, objects, ownership, lambda: logical.append(True), KEYS
    )

    rows = ownership["resources"]
    assert agents.calls == [
        ("agent", ownership["agent_id"]),
        ("token", ownership["agent_id"], ownership[KEYS.credential_id]),
    ]
    assert objects.calls == [(r["kind"], r["name"], r["namespace"]) for r in rows]
    assert logical == [True]
    assert stage["agent"]["http_status"] == stage[KEYS.credential]["http_status"] == 404
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


def _present_without_uid(ownership: Mapping[str, Any]) -> tuple[Agents, Objects]:
    row = ownership["resources"][-1]
    return Agents(), Objects({(row["kind"], row["name"], row["namespace"]): None})


@pytest.mark.parametrize(
    ("answers", "reason"),
    (
        (_present_agent, "agent_cleanup_registration_present"),
        (_present_token, "agent_cleanup_credential_present"),
        (_present_resource, "agent_cleanup_resource_present"),
        (_present_without_uid, "agent_cleanup_resource_present"),
    ),
    ids=("registration", "token", "run-created-object", "object-without-uid"),
)
def test_stage_refuses_a_present_object_before_the_logical_read(
    answers: Callable[[Mapping[str, Any]], tuple[Agents, Objects]], reason: str
) -> None:
    """Any surviving run object stops the stage without a record."""
    ownership = _ownership()
    agents, objects = answers(ownership)
    logical: list[bool] = []

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.observe_stage(
            agents, objects, ownership, lambda: logical.append(True), KEYS
        )

    assert str(refused.value).split(":", 1)[0] == reason
    assert logical == []


def test_stage_refuses_a_borrowed_object_replaced_under_the_same_name() -> None:
    """A borrowed row must still hold its recorded UID."""
    ownership = _borrowed(_ownership())
    live = {key: OTHER_UID for key in _live_borrowed(ownership)}

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.observe_stage(Agents(), Objects(live), ownership, lambda: None, KEYS)

    assert str(refused.value).startswith("agent_cleanup_borrowed_resource_changed")


def test_stage_refuses_an_ownership_row_without_a_created_flag() -> None:
    """A row whose ``created`` is not a boolean is not guessed."""
    ownership = _ownership()
    ownership["resources"][0]["created"] = "true"

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        _observation(ownership)

    assert str(refused.value) == "agent_cleanup_ownership_invalid"


def test_stage_propagates_a_present_release_or_run_resource() -> None:
    """The installed logical absence read decides release and run-resource absence."""
    ownership = _ownership()

    def present() -> None:
        raise ABSENCE.KubernetesError("agent_absence_release_present")

    with pytest.raises(ABSENCE.KubernetesError):
        CLEANUP.observe_stage(
            Agents(), Objects(_live_borrowed(ownership)), ownership, present, KEYS
        )


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
        name = f"{operation}-{action}"
        receipt = deepcopy(repeat if name == REPEAT_KEY else connect)
        receipt.update(deepcopy(candidate))
        receipt["operation"] = operation
        receipt["captured_at"] = (start + timedelta(minutes=offset)).isoformat()
        record = receipt["records"][0]
        record["action"] = action
        if operation == VERIFY:
            record["ci_access"] = {"proof": {"captured_at": start.isoformat()}}
        if operation == DELETE:
            receipt["filters"] = deepcopy(repeat["filters"])
        receipts[name] = receipt
    return receipts


def _attach_files(
    tmp_path: Path,
    receipts: Mapping[str, dict[str, Any]],
    after: object,
    repeat: object,
) -> dict[str, Path]:
    """Write the attach inputs and return every file path."""
    paths = {key: tmp_path / name for key, name in FILES.items()}
    for key, document in (
        ("connect", receipts[CONNECT_KEY]),
        ("after", after),
        ("repeat", repeat),
        ("receipt", receipts[REPEAT_KEY]),
    ):
        paths[key].write_text(json.dumps(document), encoding="utf-8")
    return paths


def _attach(paths: Mapping[str, Path]) -> int:
    """Run the adapter's attach with the repository copy as the bound copy."""
    return CLI.main(
        [
            CLI.ATTACH,
            CLI.SKILL_OPTION,
            str(SKILL_ROOT),
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


def _reason(capsys: pytest.CaptureFixture[str]) -> tuple[str, str]:
    """Read the adapter's status line."""
    line = json.loads(capsys.readouterr().out)
    return line[CLI.STATUS_FIELD], line.get(CLI.REASON_FIELD, "")


def test_attach_writes_a_record_the_series_check_accepts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The adapter's attach output completes the five-receipt series."""
    start = datetime.now(UTC) - timedelta(hours=1)
    receipts = _series(start)
    connect = receipts[CONNECT_KEY]
    paths = _attach_files(
        tmp_path,
        receipts,
        _stage(connect, start + timedelta(minutes=3, seconds=30)),
        _stage(connect, start + timedelta(minutes=4, seconds=30)),
    )

    code = _attach(paths)

    assert (code, _reason(capsys)[0]) == (STATUS.exit_code(STATUS.PASS), STATUS.PASS)
    attached = json.loads(paths["out"].read_text(encoding="utf-8"))
    SCHEMA.validate_specification(attached, RESULT_SCHEMA)
    complete = [*(r for k, r in receipts.items() if k != REPEAT_KEY), attached]
    assert EVIDENCE.check_agent_series(complete, now=datetime.now(UTC)) == ()
    assert EVIDENCE.check_agent_series(
        list(receipts.values()), now=datetime.now(UTC)
    ) == ("agent_series_cleanup_unverified",)


@pytest.mark.parametrize(
    ("after_minutes", "repeat_minutes", "reason"),
    (
        (4.5, 4.5, "agent_cleanup_stage_order_invalid"),
        (3.5, 3.5, "agent_cleanup_stage_order_invalid"),
        (4.5, 5.5, "agent_cleanup_stage_order_invalid"),
    ),
    ids=("after-read-after-repeat", "repeat-read-before-repeat", "both-after-repeat"),
)
def test_attach_refuses_stages_out_of_order(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    after_minutes: float,
    repeat_minutes: float,
    reason: str,
) -> None:
    """The after read must precede the repeat receipt and the repeat read follow it."""
    start = datetime.now(UTC) - timedelta(hours=1)
    receipts = _series(start)
    connect = receipts[CONNECT_KEY]
    paths = _attach_files(
        tmp_path,
        receipts,
        _stage(connect, start + timedelta(minutes=after_minutes)),
        _stage(connect, start + timedelta(minutes=repeat_minutes)),
    )

    code = _attach(paths)

    assert (code, _reason(capsys)) == (
        STATUS.exit_code(STATUS.BLOCKED),
        (STATUS.BLOCKED, reason),
    )
    assert not paths["out"].exists()


@pytest.mark.parametrize("change", ("run", "digest", "present", "out-exists"))
def test_attach_refuses_foreign_or_repeated_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    """Stages of another run or copy, a second record, and an existing output stop attach."""
    start = datetime.now(UTC) - timedelta(hours=1)
    receipts = _series(start)
    connect = receipts[CONNECT_KEY]
    after = _stage(connect, start + timedelta(minutes=3, seconds=30))
    repeat = _stage(connect, start + timedelta(minutes=4, seconds=30))
    expected = {
        "run": "agent_cleanup_stage_identity_mismatch",
        "digest": "agent_cleanup_stage_identity_mismatch",
        "present": "agent_cleanup_record_present",
    }
    if change == "run":
        after["run_id"] = OTHER_RUN
    elif change == "digest":
        repeat["skill_digest"] = OTHER_DIGEST
    elif change == "present":
        receipts[REPEAT_KEY]["smoke_cleanup"] = {}
    paths = _attach_files(tmp_path, receipts, after, repeat)
    if change == "out-exists":
        paths["out"].write_text("{}", encoding="utf-8")

    code = _attach(paths)

    status, reason = _reason(capsys)
    assert (code, status) == (STATUS.exit_code(STATUS.BLOCKED), STATUS.BLOCKED)
    if change in expected:
        assert reason == expected[change]
        assert not paths["out"].exists()
    else:
        assert paths["out"].read_text(encoding="utf-8") == "{}"


def test_attach_refuses_a_record_for_another_run() -> None:
    """The record attaches only to the repeat delete of its own run."""
    start = datetime.now(UTC) - timedelta(hours=1)
    receipts = _series(start)
    connect = receipts[CONNECT_KEY]
    record = CLEANUP.cleanup_record(
        connect,
        _stage(connect, start + timedelta(minutes=3, seconds=30)),
        _stage(connect, start + timedelta(minutes=4, seconds=30)),
        receipts[REPEAT_KEY],
        datetime.now(UTC),
        KEYS,
    )
    repeat = deepcopy(receipts[REPEAT_KEY])
    repeat["filters"]["run_id"] = OTHER_RUN

    with pytest.raises(CLEANUP.AgentCleanupError) as refused:
        CLEANUP.attach_record(repeat, record)

    assert str(refused.value) == "agent_cleanup_run_mismatch"


def _authorities(
    connect: Mapping[str, Any], *, cluster_uid: str, digest: str
) -> SimpleNamespace:
    """Stand in for the installed authorities bound to the recorded scope."""
    ownership = CLEANUP.connect_ownership(connect)
    skill = {**connect["skill"], KEYS.digest: digest}
    return SimpleNamespace(
        project=SimpleNamespace(
            id=ownership["project_id"], path=ownership["project_path"]
        ),
        cluster_uid=cluster_uid,
        gitlab=SimpleNamespace(
            session=SimpleNamespace(origin=ownership["origin"] + "/", skill=skill)
        ),
        provider=Agents(),
        kubernetes=Objects(_live_borrowed(ownership)),
    )


@pytest.mark.parametrize("case", ("recorded", "other-cluster", "other-copy"))
def test_observe_binds_the_series_target_and_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    """Observe passes the series' selection through and refuses another cluster or copy."""
    connect = _fixture(CONNECT_FIXTURE)
    ownership = CLEANUP.connect_ownership(connect)
    authorities = _authorities(
        connect,
        cluster_uid=OTHER_CLUSTER
        if case == "other-cluster"
        else ownership["cluster_uid"],
        digest=OTHER_DIGEST if case == "other-copy" else connect["skill"][KEYS.digest],
    )
    selected: list[tuple[object, ...]] = []
    logical: list[object] = []
    monkeypatch.setattr(
        AUTHORITY,
        "resolve_agent_target",
        lambda *args, **_kwargs: selected.append(args) or SimpleNamespace(),
    )
    monkeypatch.setattr(AUTHORITY, "observe_authorities", lambda *_a, **_k: authorities)
    monkeypatch.setattr(
        ABSENCE,
        "verify_agent_logical_absence",
        lambda _gitlab, _kubernetes, selection, **_kwargs: logical.append(selection),
    )
    receipt = tmp_path / FILES["connect"]
    receipt.write_text(json.dumps(connect), encoding="utf-8")
    out = tmp_path / FILES["stage"]

    code = CLI.main(
        [
            CLI.OBSERVE,
            CLI.SKILL_OPTION,
            str(SKILL_ROOT),
            "--connect-receipt",
            str(receipt),
            "--out",
            str(out),
            CLI.BINDING_OPTION,
            BINDING,
            GITLAB_OPTION,
            PUBLIC,
        ]
    )

    status, reason = _reason(capsys)
    ((_, binding, project, gitlab),) = selected
    assert (binding, project, gitlab.selector) == (
        BINDING,
        ownership["project_path"],
        PUBLIC,
    )
    if case != "recorded":
        assert (code, status) == (STATUS.exit_code(STATUS.BLOCKED), STATUS.BLOCKED)
        assert (
            reason
            == {
                "other-cluster": "agent_cleanup_scope_mismatch",
                "other-copy": "agent_cleanup_skill_mismatch",
            }[case]
        )
        assert logical == []
        assert not out.exists()
        return
    assert (code, status) == (STATUS.exit_code(STATUS.PASS), STATUS.PASS)
    stage = json.loads(out.read_text(encoding="utf-8"))
    assert [item.release for item in logical] == [ownership["release"]]
    assert logical[0].scope.run_id == ownership["run_id"]
    assert stage["run_id"] == ownership["run_id"]
    assert stage["skill_digest"] == connect["skill"][KEYS.digest]
    assert len(stage["observation"]["resources"]) == len(ownership["resources"])


def test_observe_refuses_a_skill_whose_lib_is_already_loaded_elsewhere(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --skill whose lib did not load is BLOCKED instead of reading through another copy."""
    receipt = tmp_path / FILES["connect"]
    receipt.write_text(json.dumps(_fixture(CONNECT_FIXTURE)), encoding="utf-8")
    other = tmp_path / "other-copy"
    other.mkdir()

    code = CLI.main(
        [
            CLI.OBSERVE,
            CLI.SKILL_OPTION,
            str(other),
            "--connect-receipt",
            str(receipt),
            "--out",
            str(tmp_path / FILES["stage"]),
        ]
    )

    assert (code, _reason(capsys)) == (
        STATUS.exit_code(STATUS.BLOCKED),
        (
            STATUS.BLOCKED,
            INSTALLED_COPY.INSTALLED_LIB_SHADOWED.format(name=CLI.STATUS_MODULE),
        ),
    )
