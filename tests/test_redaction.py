"""Tests for credential redaction and its output boundary.

The owner's cases are the floor: `CI_JOB_TOKEN=unit-value`,
`GITLAB_TOKEN=unit-value` and `{"password":"unit-value"}` all passed through the
old generic pattern untouched, and the JSON, YAML and `--output-dir` paths had
no redaction boundary at all. The second half of this file is the other
direction: the gate's own evidence about WHERE a credential came from must
survive, or the receipt is blinded.
"""

from __future__ import annotations

import json

import pytest
from conftest import import_script_module

SECRET = "unit-value-should-not-survive"


def _runtime():
    return import_script_module("core.runtime")


@pytest.mark.parametrize(
    "template",
    (
        "CI_JOB_TOKEN={}",
        "GITLAB_TOKEN={}",
        "GH_TOKEN={}",
        "GH_ENTERPRISE_TOKEN={}",
        "KUBE_TOKEN={}",
        "DB_PASSWORD={}",
        "MY_API_KEY={}",
        "MY_APIKEY={}",
        "SOME_SECRET={}",
        "PRIVATE_KEY={}",
        "GH_TOKEN: {}",
        "password={}",
        "passwd: {}",
        "access_token={}",
    ),
)
def test_environment_style_token_names_are_redacted(template):
    """A name CLASS, so the next variable nobody listed is still covered."""
    result = _runtime().redact(template.format(SECRET))

    assert SECRET not in result
    assert "[REDACTED]" in result


@pytest.mark.parametrize(
    "template",
    (
        '{{"password":"{}"}}',
        '{{"token": "{}"}}',
        '{{"access_token":"{}"}}',
        '{{"api_key" : "{}"}}',
        '{{"MY_SECRET": "{}"}}',
        'password: "{}"',
        "token = '{}'",
    ),
)
def test_quoted_structured_fields_are_redacted(template):
    """A quoted value is the shape the old assignment pattern could not reach."""
    result = _runtime().redact(template.format(SECRET))

    assert SECRET not in result
    assert "[REDACTED]" in result


@pytest.mark.parametrize(
    "value",
    (
        "glpat-ABCDEFGHIJKLMNOP1234",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
        "github_pat_ABCDEFGHIJKLMNOPQRSTUV",
        "xoxb-ABCDEFGHIJKL",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    ),
)
def test_bare_provider_tokens_are_redacted(value):
    """A pasted token carries no surrounding key to match on."""
    result = _runtime().redact(f"trace line containing {value} here")

    assert value not in result
    assert "[REDACTED]" in result


def test_a_private_key_block_spanning_lines_is_redacted():
    """Per-line redaction can never match a multi-line PEM."""
    block = f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n-----END PRIVATE KEY-----"

    assert SECRET not in _runtime().redact(f"before\n{block}\nafter")


def test_redact_does_not_truncate_but_sanitize_does():
    """A whole report must be redactable without a 1000-character bound."""
    runtime = _runtime()
    body = "x" * 5000

    assert len(runtime.redact(body)) == 5000
    assert len(runtime.sanitize(body)) == 1000


@pytest.mark.parametrize(
    "preserved",
    (
        '"credential_source": "env:GITLAB_TOKEN"',
        '"credential_sources": {"gitlab": "file", "github": "env:GH_TOKEN"}',
        '"auth_mechanism": {"type": "embedded-token", "source": "kubeconfig"}',
        '"kubeconfig_files": ["/home/operator/.kube/config"]',
        '"observed_capability": ["instance_admin_identity"]',
    ),
)
def test_evidence_about_where_a_credential_came_from_survives(preserved):
    """The gate must still be able to report its own credential sources.

    `credential` is deliberately absent from the name class: these fields name
    a LOCATION, never a value, and redacting them would defeat the receipt.
    """
    assert _runtime().redact(preserved) == preserved


def test_redact_tree_covers_keys_values_and_nesting():
    """Redaction at the boundary must reach every string, keys included."""
    runtime = _runtime()
    tree = {
        "outer": [{"GITLAB_TOKEN": SECRET}, f"GH_TOKEN={SECRET}"],
        f"CI_JOB_TOKEN={SECRET}": "key position",
        "count": 3,
        "flag": True,
        "nothing": None,
    }

    result = runtime.redact_tree(tree)

    assert SECRET not in json.dumps(result)
    assert result["count"] == 3
    assert result["flag"] is True
    assert result["nothing"] is None


@pytest.mark.parametrize("mode", ("json", "yaml", "human"))
def test_every_output_path_redacts(tmp_path, mode):
    """The returned text AND both persisted files must be covered."""
    report = import_script_module("core.report")
    data = {
        "schema_version": "1.0",
        "kind": "gitlab_job",
        "status": "PASS",
        "target": "https://gitlab.example.test",
        "filters": {"search": None},
        "errors": [],
        "summary": {"record_count": 1, "error_count": 0},
        "records": [
            {
                "name": "unit",
                "trace_tail": f"CI_JOB_TOKEN={SECRET}",
                "pipeline": {"variables": {"GITLAB_TOKEN": SECRET}},
            }
        ],
    }

    rendered = report.emit(data, mode, str(tmp_path))

    assert SECRET not in rendered
    assert SECRET not in (tmp_path / "gitlab_job.json").read_text(encoding="utf-8")
    assert SECRET not in (tmp_path / "gitlab_job.txt").read_text(encoding="utf-8")


def test_emit_leaves_no_partial_file_behind(tmp_path):
    """Each report is replaced atomically, so a reader never sees half of one."""
    report = import_script_module("core.report")
    data = {
        "schema_version": "1.0",
        "kind": "storage_report",
        "status": "PASS",
        "target": "unit",
        "filters": {},
        "records": [],
        "errors": [],
        "summary": {"record_count": 0, "error_count": 0},
    }

    report.emit(data, "json", str(tmp_path))

    assert sorted(item.name for item in tmp_path.iterdir()) == [
        "storage_report.json",
        "storage_report.txt",
    ]
