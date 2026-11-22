"""Regression coverage for the CI16 OLM operator command (M1 reads).

The resolve logic is tested against a fake Kubernetes service; no test reaches
a cluster. The builders, the digest binding, the needs inference and the mode
policy are the behaviors pinned here.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from argparse import Namespace

import pytest
from conftest import REPO_ROOT, load_module

olm_models = load_module(
    "ci_skills_test_olm_models",
    REPO_ROOT
    / "ci-skills"
    / "lib"
    / "python"
    / "core"
    / "kubernetes"
    / "olm_models.py",
)
olm_callbacks = load_module(
    "ci_skills_test_olm_callbacks",
    REPO_ROOT
    / "ci-skills"
    / "lib"
    / "python"
    / "core"
    / "kubernetes"
    / "olm_callbacks.py",
)
olm_resources = load_module(
    "ci_skills_test_olm_resources",
    REPO_ROOT
    / "ci-skills"
    / "lib"
    / "python"
    / "core"
    / "kubernetes"
    / "olm_resources.py",
)
olm_cli = load_module(
    "ci_skills_test_olm_cli",
    REPO_ROOT / "ci-skills" / "lib" / "python" / "core" / "kubernetes" / "olm_cli.py",
)

PACKAGE = "nvidia-network-operator"
NAMESPACE = "nvidia-network-operator"
CERTIFIED = olm_callbacks.CatalogSourceRef(
    "certified-operators", "openshift-marketplace"
)
CHANNEL = "v26.7"
DEFAULT_CHANNEL = "stable"
INSTALLED_CSV = f"{PACKAGE}.v26.7.0-1"


class FakeResult:
    """Minimal command result stand-in."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        """Pin code and streams."""
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeService:
    """In-memory Kubernetes service answering the OLM resolve reads."""

    def __init__(self, *, olm_available: bool = True) -> None:
        """Start with one catalog source and no subscription."""
        self.olm_available = olm_available
        self.manifests: list[dict] = []
        self.subscription: dict | None = None
        self.csv: dict | None = None
        self.package = PACKAGE

    def seed_manifest(
        self,
        channel: str = CHANNEL,
        default_channel: str = DEFAULT_CHANNEL,
        modes: tuple[str, ...] = ("OwnNamespace", "AllNamespaces"),
    ) -> None:
        """Add one PackageManifest for the package on the certified source."""
        self.manifests.append(
            {
                "metadata": {"name": self.package},
                "status": {
                    "catalogSource": CERTIFIED.name,
                    "catalogSourceNamespace": CERTIFIED.namespace,
                    "defaultChannel": default_channel,
                    "channels": [
                        {
                            "name": channel,
                            "installModes": [{"type": mode} for mode in modes],
                            "entries": [{"name": INSTALLED_CSV}],
                        }
                    ],
                },
            }
        )

    def seed_subscription(
        self, channel: str = CHANNEL, csv_name: str | None = None
    ) -> None:
        """Add an existing Subscription with its installed CSV."""
        installed = csv_name or INSTALLED_CSV
        self.subscription = {
            "metadata": {"name": self.package, "namespace": NAMESPACE},
            "spec": {"channel": channel},
            "status": {"installedCSV": installed},
        }
        self.csv = {
            "metadata": {"name": installed, "namespace": NAMESPACE},
            "status": {"phase": "Succeeded"},
        }

    def command(self, *args: str, **kwargs: object) -> FakeResult:
        """Answer only the raw OLM discovery call."""
        if args[:2] == ("get", "--raw=/apis/operators.coreos.com/v1alpha1"):
            if self.olm_available:
                return FakeResult(0, '{"kind": "APIResourceList"}')
            return FakeResult(1, "", "Error from server (NotFound): 404")
        raise AssertionError(f"unexpected command {args}")

    def json(self, *args: str, **kwargs: object) -> object:
        """Answer the cluster-wide PackageManifest list."""
        assert args[0] == "get"
        return {"items": list(self.manifests)}

    def read(self, kind: str, name: str, namespace: str, **kwargs: object):
        """Answer Subscription and CSV reads; None for anything absent."""
        if kind == olm_models.SUBSCRIPTION_RESOURCE:
            return self.subscription if self.subscription is not None else None
        if kind == olm_models.CLUSTERSERVICEVERSION_RESOURCE:
            return self.csv if self.csv is not None else None
        return None


def make_action(**overrides: object) -> olm_models.OLMAction:
    """Build one OLM action with the given override fields."""
    values = {
        "channel": None,
        "source": None,
        "source_namespace": None,
        "install_mode": None,
        "approval": None,
        "starting_csv": None,
    }
    values.update(overrides)
    return olm_models.OLMAction(
        args=Namespace(
            action="install",
            confirm_plan=None,
            kind=None,
            shipped_default=False,
            spec=None,
        ),
        state=olm_models.OLMState(),
        target=None,
        package=PACKAGE,
        namespace=NAMESPACE,
        overrides=olm_models.OperatorOverrides(**values),
    )


def test_namespace_document_has_labels_and_no_namespace() -> None:
    """The Namespace document never carries a namespace field."""
    document = olm_models.namespace_document(NAMESPACE)
    assert document["kind"] == "Namespace"
    assert document["metadata"]["name"] == NAMESPACE
    assert "namespace" not in document["metadata"]
    assert document["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "ci-skills"


def test_operatorgroup_document_install_modes() -> None:
    """OwnNamespace pins the target; AllNamespaces omits it."""
    own = olm_models.operatorgroup_document(NAMESPACE, "OwnNamespace")
    assert own["spec"]["targetNamespaces"] == [NAMESPACE]
    all_namespaces = olm_models.operatorgroup_document(NAMESPACE, "AllNamespaces")
    assert "targetNamespaces" not in all_namespaces["spec"]


def test_subscription_document_overrides() -> None:
    """startingCSV is present only when the caller pinned one."""
    base = olm_models.subscription_document(
        PACKAGE, NAMESPACE, CHANNEL, CERTIFIED, "Automatic"
    )
    assert base["spec"]["source"] == "certified-operators"
    assert "startingCSV" not in base["spec"]
    pinned = olm_models.subscription_document(
        PACKAGE,
        NAMESPACE,
        CHANNEL,
        CERTIFIED,
        "Manual",
        INSTALLED_CSV,
    )
    assert pinned["spec"]["startingCSV"] == INSTALLED_CSV
    assert pinned["spec"]["installPlanApproval"] == "Manual"


def test_resolve_needs_olm_unavailable() -> None:
    """A cluster without OLM produces the olm_unavailable need."""
    service = FakeService(olm_available=False)
    plan, needs = olm_callbacks._resolve_plan(make_action(), service)
    assert [item.need for item in needs] == ["olm_unavailable"]
    assert not plan.enabled


def test_resolve_needs_package_unknown() -> None:
    """An unknown package names the PackageManifest evidence."""
    service = FakeService()
    _, needs = olm_callbacks._resolve_plan(make_action(), service)
    assert [item.need for item in needs] == ["package_unknown"]
    assert "no PackageManifest" in needs[0].evidence


def test_resolve_picks_default_channel_and_reports_enabled() -> None:
    """An existing Subscription counts as enabled whatever its channel."""
    service = FakeService()
    service.seed_manifest(channel=CHANNEL, default_channel=DEFAULT_CHANNEL)
    service.seed_subscription(channel=CHANNEL)
    plan, needs = olm_callbacks._resolve_plan(make_action(), service)
    assert needs == []
    assert plan.channel == CHANNEL
    assert plan.catalog == CERTIFIED
    assert plan.enabled
    assert plan.installed_csv == INSTALLED_CSV
    assert plan.csv_phase == "Succeeded"


def test_resolve_channel_override_conflict_with_existing() -> None:
    """A conflicting --channel against the existing Subscription stops with a need."""
    service = FakeService()
    service.seed_manifest(channel=DEFAULT_CHANNEL, default_channel=DEFAULT_CHANNEL)
    service.seed_subscription(channel=CHANNEL)
    _, needs = olm_callbacks._resolve_plan(
        make_action(channel=DEFAULT_CHANNEL), service
    )
    assert any(item.need == "override_conflict" for item in needs)


def test_resolve_channel_override_matching_existing_has_no_conflict() -> None:
    """A --channel equal to the existing Subscription's channel is not a conflict."""
    service = FakeService()
    service.seed_manifest(channel=CHANNEL, default_channel=DEFAULT_CHANNEL)
    service.seed_subscription(channel=CHANNEL)
    _, needs = olm_callbacks._resolve_plan(make_action(channel=CHANNEL), service)
    assert needs == []


def test_resolve_channel_override_without_existing_has_no_conflict() -> None:
    """A --channel on a fresh install selects that channel and is not a conflict."""
    service = FakeService()
    service.seed_manifest(channel=CHANNEL, default_channel=DEFAULT_CHANNEL)
    plan, needs = olm_callbacks._resolve_plan(make_action(channel=CHANNEL), service)
    assert needs == []
    assert plan.channel == CHANNEL
    assert not plan.enabled


def test_resolve_install_mode_and_approval_checked() -> None:
    """Unsupported install mode and bad approval each produce their need."""
    service = FakeService()
    service.seed_manifest(
        channel=DEFAULT_CHANNEL,
        default_channel=DEFAULT_CHANNEL,
        modes=("OwnNamespace",),
    )
    _, needs = olm_callbacks._resolve_plan(
        make_action(install_mode="AllNamespaces"), service
    )
    assert [item.need for item in needs] == ["mode_unsupported"]
    _, needs = olm_callbacks._resolve_plan(make_action(approval="Sometimes"), service)
    assert [item.need for item in needs] == ["approval_invalid"]


def test_plan_evidence_binds_digest() -> None:
    """The digest changes when any bound plan field changes."""
    service = FakeService()
    service.seed_manifest(channel=DEFAULT_CHANNEL, default_channel=DEFAULT_CHANNEL)
    first, _ = olm_callbacks._resolve_plan(make_action(), service)
    second, _ = olm_callbacks._resolve_plan(make_action(approval="Manual"), service)
    digest_one = olm_callbacks.plan_digest(olm_callbacks.plan_evidence(first))
    digest_two = olm_callbacks.plan_digest(olm_callbacks.plan_evidence(second))
    assert digest_one != digest_two
    assert len(digest_one) == 64


def test_alm_examples_decodes_csv_annotation() -> None:
    """The shipped examples decode from the alm-examples annotation."""
    examples = [
        {"kind": "NicClusterPolicy", "metadata": {"name": "nic-cluster-policy"}}
    ]
    csv = {
        "metadata": {
            "annotations": {"alm-examples": json.dumps(examples)},
        }
    }
    assert olm_resources.alm_examples(csv) == examples
    assert olm_resources.alm_examples({"metadata": {"annotations": {}}}) == []


def test_mode_policy_defaults_and_conflicts() -> None:
    """Default mode is live-plan; confirm without apply is rejected."""
    args = olm_cli.build_parser().parse_args(
        ["install", "--package", PACKAGE, "--namespace", NAMESPACE]
    )
    assert olm_cli.resolve_operator_mode(args).value == "live-plan"
    conflicting = olm_cli.build_parser().parse_args(
        [
            "install",
            "--package",
            PACKAGE,
            "--namespace",
            NAMESPACE,
            "--confirm-plan",
            "a" * 64,
        ]
    )
    with pytest.raises(ValueError):
        olm_cli.resolve_operator_mode(conflicting)


def test_prepare_rejects_apply_until_m2() -> None:
    """M1 refuses confirmed apply with a stable reason instead of a false write."""
    args = olm_cli.build_parser().parse_args(
        [
            "install",
            "--package",
            PACKAGE,
            "--namespace",
            NAMESPACE,
            "--apply",
            "--confirm-plan",
            "a" * 64,
        ]
    )
    command = olm_cli.OCPOperator(args)
    with pytest.raises(ValueError) as excinfo:
        command._prepare(olm_cli.ActionMode.APPLY)
    assert "olm_apply_not_delivered" in str(excinfo.value)
