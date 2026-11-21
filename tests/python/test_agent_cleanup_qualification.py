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
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from importlib import import_module, invalidate_caches
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, Final, Optional
from unittest.mock import Mock

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
CATALOG = import_script_module("lib.python.core.catalog")

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
PROFILE_OPTION: Final = CATALOG.GITLAB_PROFILE_OPTION
LEGACY_PROFILE: Final = "legacy-profile"
CONNECT_RECEIPT_OPTION: Final = "--connect-receipt"
OUT_OPTION: Final = "--out"
OTHER_ORIGIN: Final = "https://gitlab.other.example.test"
OTHER_RELEASE: Final = "other-agent-release"
SCOPE_MISMATCH: Final = "agent_cleanup_scope_mismatch"
RELEASE_MISMATCH: Final = "agent_cleanup_release_mismatch"
# Load guard reasons of tools/skillkit/installed_copy.py, before the module name.
LIB_UNAVAILABLE: Final = "installed_lib_unavailable"
LIB_SHADOWED: Final = "installed_lib_shadowed"
# A package written into two copies to test where installed_module loads from.
PROBE_PACKAGE: Final = "installed_copy_probe"
PROBE_FILE: Final = "marker.py"
PROBE_MODULE: Final = f"{PROBE_PACKAGE}.{Path(PROBE_FILE).stem}"
MISSING_MODULE: Final = f"{PROBE_PACKAGE}.absent"
# A directory without __init__.py, imported as a namespace package.
NAMESPACE_PROBE: Final = "installed_copy_namespace_probe"
COPY_DIR: Final = "copy"
OTHER_COPY_DIR: Final = "other-copy"
PACKAGE_INIT: Final = "__init__.py"
NOT_PYTHON_FILE: Final = "status.txt"
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


def _observe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    connect: Mapping[str, Any],
    authorities: SimpleNamespace,
    *selection: str,
) -> tuple[int, list[tuple[object, ...]], list[object], Path]:
    """Run the adapter's observe against fake installed reads and record the calls.

    :param monkeypatch: Replaces the installed resolver, authority and absence reads.
    :param tmp_path: Directory of the connect receipt and the stage file.
    :param connect: Connect APPLIED receipt written for ``--connect-receipt``.
    :param authorities: Value the installed ``observe_authorities`` returns.
    :param selection: Binding and GitLab selection arguments appended to argv.
    :returns: Exit code, resolver positional arguments, selections given to the
        logical absence read, and the stage file path.
    """
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
            CONNECT_RECEIPT_OPTION,
            str(receipt),
            OUT_OPTION,
            str(out),
            *selection,
        ]
    )
    return code, selected, logical, out


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

    code, selected, logical, out = _observe(
        monkeypatch,
        tmp_path,
        connect,
        authorities,
        CLI.BINDING_OPTION,
        BINDING,
        GITLAB_OPTION,
        PUBLIC,
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


@pytest.mark.parametrize(
    ("case", "reason"),
    (
        ("other-project", SCOPE_MISMATCH),
        ("other-origin", SCOPE_MISMATCH),
        ("other-release", RELEASE_MISMATCH),
    ),
)
def test_observe_refuses_another_project_origin_or_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
    reason: str,
) -> None:
    """Another bound project or origin, or another release name, stops observe.

    ``--binding`` and ``--gitlab`` can select another project or GitLab instance
    than the series used, and an edited receipt can record a release name the
    bound copy does not derive from the run scope. No Agent, Kubernetes or
    release read runs and no stage file is written.
    """
    connect = _fixture(CONNECT_FIXTURE)
    ownership = CLEANUP.connect_ownership(connect)
    authorities = _authorities(
        connect,
        cluster_uid=ownership["cluster_uid"],
        digest=connect["skill"][KEYS.digest],
    )
    if case == "other-project":
        authorities.project.id = ownership["project_id"] + 1
    elif case == "other-origin":
        authorities.gitlab.session.origin = OTHER_ORIGIN
    else:
        record = connect[CLEANUP.RECORDS_KEY][0]
        record[CLEANUP.OWNERSHIP_KEY][CLEANUP.RELEASE_KEY] = OTHER_RELEASE

    code, selected, logical, out = _observe(monkeypatch, tmp_path, connect, authorities)

    assert (code, _reason(capsys)) == (
        STATUS.exit_code(STATUS.BLOCKED),
        (STATUS.BLOCKED, reason),
    )
    assert len(selected) == 1
    assert (authorities.provider.calls, authorities.kubernetes.calls, logical) == (
        [],
        [],
        [],
    )
    assert not out.exists()


def test_observe_passes_the_legacy_gitlab_profile_to_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--gitlab-profile`` reaches the installed resolver as the selection profile.

    A series run against a legacy ``[gitlab.NAME]`` target selects it with this
    option, so the cleanup read must resolve the same table.
    """
    connect = _fixture(CONNECT_FIXTURE)
    ownership = CLEANUP.connect_ownership(connect)
    authorities = _authorities(
        connect,
        cluster_uid=ownership["cluster_uid"],
        digest=connect["skill"][KEYS.digest],
    )

    code, selected, _, out = _observe(
        monkeypatch, tmp_path, connect, authorities, PROFILE_OPTION, LEGACY_PROFILE
    )

    ((_, binding, project, gitlab),) = selected
    assert (binding, project, gitlab.selector, gitlab.profile) == (
        None,
        ownership["project_path"],
        None,
        LEGACY_PROFILE,
    )
    assert (code, _reason(capsys)[0]) == (STATUS.exit_code(STATUS.PASS), STATUS.PASS)
    assert out.exists()


def test_observe_without_the_connect_receipt_is_blocked_before_resolving(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A connect receipt path that does not exist returns the installed BLOCKED code.

    The path is typed by hand and can name a receipt that was never copied from
    the run. No target is resolved and no stage file is written.
    """
    resolver = Mock()
    monkeypatch.setattr(AUTHORITY, "resolve_agent_target", resolver)
    missing = tmp_path / FILES["connect"]
    out = tmp_path / FILES["stage"]

    code = CLI.main(
        [
            CLI.OBSERVE,
            CLI.SKILL_OPTION,
            str(SKILL_ROOT),
            CONNECT_RECEIPT_OPTION,
            str(missing),
            OUT_OPTION,
            str(out),
        ]
    )

    status, reason = _reason(capsys)
    assert (code, status) == (STATUS.exit_code(STATUS.BLOCKED), STATUS.BLOCKED)
    assert str(missing) in reason
    resolver.assert_not_called()
    assert not out.exists()


def _probe_copy(root: Path) -> Path:
    """Write the probe package into one copy; its module records the copy path.

    :param root: Copy directory to create.
    :returns: ``root``.
    """
    package = root / PROBE_PACKAGE
    package.mkdir(parents=True)
    (package / PACKAGE_INIT).write_text("", encoding="utf-8")
    (package / PROBE_FILE).write_text(
        f"ROOT = {json.dumps(str(root))}\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def probe_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Path, Path]]:
    """Write the probe package into two copies and drop it from sys.modules after.

    ``sys.path`` is a copy for the test, restored by ``monkeypatch``.

    :param tmp_path: Pytest temporary directory holding both copies.
    :param monkeypatch: Restores ``sys.path``.
    :yields: The copy under test and another copy.
    :ytype: tuple[Path, Path]
    """
    monkeypatch.setattr(sys, "path", list(sys.path))
    copy = _probe_copy(tmp_path / COPY_DIR)
    other = _probe_copy(tmp_path / OTHER_COPY_DIR)
    invalidate_caches()
    yield copy, other
    for name in (PROBE_MODULE, PROBE_PACKAGE):
        sys.modules.pop(name, None)


def test_installed_module_loads_the_module_from_the_bound_copy(
    probe_copies: tuple[Path, Path],
) -> None:
    """A module found first in the bound copy is returned and registered."""
    copy, _ = probe_copies
    INSTALLED_COPY.bind_skill(copy)

    module = INSTALLED_COPY.installed_module(copy, PROBE_MODULE)

    assert module.ROOT == str(copy)
    assert sys.modules[PROBE_MODULE] is module


def test_installed_module_refuses_a_module_loaded_from_another_copy(
    probe_copies: tuple[Path, Path],
) -> None:
    """A package imported earlier from another copy is not read through.

    A process that imported ``lib`` from one copy keeps it in ``sys.modules``,
    so binding a second copy afterwards still returns the first copy's module.
    """
    copy, other = probe_copies
    INSTALLED_COPY.bind_skill(other)
    import_module(PROBE_MODULE)
    INSTALLED_COPY.bind_skill(copy)

    with pytest.raises(INSTALLED_COPY.InstalledCopyError) as refused:
        INSTALLED_COPY.installed_module(copy, PROBE_MODULE)

    assert str(refused.value) == f"{LIB_SHADOWED}:{PROBE_MODULE}"


def test_installed_module_refuses_a_namespace_package_without_a_file(
    probe_copies: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A namespace package has no ``__file__``; it never counts as the bound copy.

    The working directory sits inside the bound copy, so a check that resolved a
    missing ``__file__`` to the working directory would accept the other copy.
    """
    copy, other = probe_copies
    (other / NAMESPACE_PROBE).mkdir()
    monkeypatch.chdir(copy)
    INSTALLED_COPY.bind_skill(other)
    try:
        assert getattr(import_module(NAMESPACE_PROBE), "__file__", None) is None

        with pytest.raises(INSTALLED_COPY.InstalledCopyError) as refused:
            INSTALLED_COPY.installed_module(copy, NAMESPACE_PROBE)
    finally:
        sys.modules.pop(NAMESPACE_PROBE, None)

    assert str(refused.value) == f"{LIB_SHADOWED}:{NAMESPACE_PROBE}"


def test_installed_module_reports_a_module_missing_from_the_copy(
    probe_copies: tuple[Path, Path],
) -> None:
    """A copy without the module reports it unavailable by name.

    An older install lacks a module a newer tool imports.
    """
    copy, _ = probe_copies
    INSTALLED_COPY.bind_skill(copy)

    with pytest.raises(INSTALLED_COPY.InstalledCopyError) as refused:
        INSTALLED_COPY.installed_module(copy, MISSING_MODULE)

    assert str(refused.value) == f"{LIB_UNAVAILABLE}:{MISSING_MODULE}"


def test_bind_skill_puts_the_copy_first_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Binding the same copy twice leaves one entry, at the front of sys.path."""
    monkeypatch.setattr(sys, "path", list(sys.path))
    before = list(sys.path)

    INSTALLED_COPY.bind_skill(tmp_path)
    INSTALLED_COPY.bind_skill(tmp_path)

    assert sys.path == [str(tmp_path), *before]


def test_file_module_loads_the_status_file_outside_sys_modules() -> None:
    """The repository status file loads by path and is not registered.

    The tool reports BLOCKED with it before any copy is bound, so the loaded
    name stays out of ``sys.modules``.
    """
    status = INSTALLED_COPY.file_module(
        CLI.REPOSITORY_STATUS_NAME, CLI.REPOSITORY_STATUS_FILE
    )

    assert status is not STATUS
    assert status.__name__ == CLI.REPOSITORY_STATUS_NAME
    assert (status.BLOCKED, status.exit_code(status.BLOCKED)) == (
        STATUS.BLOCKED,
        STATUS.exit_code(STATUS.BLOCKED),
    )
    assert CLI.REPOSITORY_STATUS_NAME not in sys.modules


def test_file_module_refuses_a_file_without_a_python_loader(tmp_path: Path) -> None:
    """A path whose suffix has no import loader raises ImportError naming the module.

    ``file_module`` accepts any path, and ``importlib`` has no source loader for
    a path without a Python suffix.
    """
    source = tmp_path / NOT_PYTHON_FILE
    source.write_text(
        CLI.REPOSITORY_STATUS_FILE.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(ImportError) as refused:
        INSTALLED_COPY.file_module(CLI.REPOSITORY_STATUS_NAME, source)

    assert refused.value.name == CLI.REPOSITORY_STATUS_NAME
    assert CLI.REPOSITORY_STATUS_NAME not in sys.modules
