"""Tests for credential redaction and its output boundary.

The owner's cases are the floor: `CI_JOB_TOKEN=unit-value`,
`GITLAB_TOKEN=unit-value` and `{"password":"unit-value"}` all passed through the
old generic pattern untouched, and the JSON, YAML and `--output-dir` paths had
no redaction boundary at all. The second half of this file is the other
direction: the gate's own evidence about WHERE a credential came from must
survive, or the receipt is blinded.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Union

import pytest
from conftest import import_script_module

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject, JSONValue

SECRET = "unit-value-should-not-survive"


def _runtime() -> ModuleType:
    """Load the command runtime and report-boundary redactor.

    :returns: Imported runtime module under test.
    """
    return import_script_module("lib.python.core.runtime")


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
def test_environment_style_token_names_are_redacted(template: str) -> None:
    """Redact values assigned to token, password, API key, or secret names.

    :param template: Assignment or colon form surrounding the synthetic value.

    :returns: None; assertions in the body validate the regression.
    """
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
def test_quoted_structured_fields_are_redacted(template: str) -> None:
    """Redact quoted secret values in JSON, YAML, and assignment forms.

    :param template: Structured field containing the synthetic secret value.

    :returns: None; assertions in the body validate the regression.
    """
    result = _runtime().redact(template.format(SECRET))

    assert SECRET not in result
    assert "[REDACTED]" in result


# Assembled at run time, never written as a literal: a secret-shaped string in
# the source would be flagged by the repository secret scan, which is correct of
# it. The redactor sees the assembled value, so the test is unaffected.
PROVIDER_TOKENS = (
    "glpat-" + "A" * 20,
    "ghp_" + "B" * 30,
    "github_pat_" + "C" * 22,
    "xoxb-" + "D" * 12,
    "eyJ" + "E" * 12 + "." + "F" * 12 + "." + "G" * 10,
)


@pytest.mark.parametrize("value", PROVIDER_TOKENS)
def test_bare_provider_tokens_are_redacted(value: str) -> None:
    """Redact provider-shaped tokens even without a named key.

    :param value: Synthetic GitLab, GitHub, Slack, or JWT-like token.

    :returns: None; assertions in the body validate the regression.
    """
    result = _runtime().redact(f"trace line containing {value} here")

    assert value not in result
    assert "[REDACTED]" in result


def test_a_private_key_block_spanning_lines_is_redacted() -> None:
    """Per-line redaction can never match a multi-line PEM.
    :returns: None; assertions in the body validate the regression.
    """
    block = f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n-----END PRIVATE KEY-----"

    assert SECRET not in _runtime().redact(f"before\n{block}\nafter")


def test_url_userinfo_and_terminal_controls_are_removed() -> None:
    """A trace URL can carry a credential without a token-named field.
    :returns: None; assertions in the body validate the regression.
    """
    value = "callback=https://unit-user:unit-secret@gitlab.example.test/path \x1b[31mred\x1b[0m"

    result = _runtime().redact(value)

    assert "unit-secret" not in result
    assert "unit-user" not in result
    assert "https://[REDACTED]@gitlab.example.test/path" in result
    assert "\x1b" not in result


def test_redact_does_not_truncate_but_sanitize_does() -> None:
    """A whole report must be redactable without a 1000-character bound.
    :returns: None; assertions in the body validate the regression.
    """
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
def test_evidence_about_where_a_credential_came_from_survives(preserved: str) -> None:
    """Preserve source-location and capability evidence without masking it.

    :param preserved: Nonsecret credential source or access receipt fragment.

    :returns: None; assertions in the body validate the regression.
    """
    assert _runtime().redact(preserved) == preserved


def test_redact_tree_covers_keys_values_and_nesting() -> None:
    """Redaction at the boundary must reach every string, keys included.
    :returns: None; assertions in the body validate the regression.
    """
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
def test_every_output_path_redacts(tmp_path: Path, mode: str) -> None:
    """Redact emitted JSON/YAML/human text and persisted paired files.

    :param tmp_path: Directory receiving the report's run directory.
    :param mode: Output renderer selected for the returned text.

    :returns: None; assertions in the body validate the regression.
    """
    report = import_script_module("lib.python.core.report")
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
    run_directory = next(tmp_path.iterdir())
    assert str(run_directory / "gitlab_job.json") in rendered
    assert str(run_directory / "gitlab_job.txt") in rendered
    assert SECRET not in (run_directory / "gitlab_job.json").read_text(encoding="utf-8")
    assert SECRET not in (run_directory / "gitlab_job.txt").read_text(encoding="utf-8")


def test_emit_leaves_no_partial_file_behind(tmp_path: Path) -> None:
    """Publish complete JSON/text report pair in one run directory.

    :param tmp_path: Scratch output root for the atomic report pair.

    :returns: None; assertions in the body validate the regression.
    """
    report = import_script_module("lib.python.core.report")
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

    returned = json.loads(report.emit(data, "json", str(tmp_path)))
    files = returned["report_files"]
    assert (
        Path(files["json"]).read_text(encoding="utf-8")
        == json.dumps(returned, indent=2, sort_keys=True) + "\n"
    )
    assert "Report JSON: " + files["json"] in Path(files["text"]).read_text(
        encoding="utf-8"
    )
    assert Path(files["json"]).parent == Path(files["text"]).parent
    assert list(tmp_path.iterdir()) == [Path(files["json"]).parent]


def test_concurrent_same_kind_reports_publish_distinct_matching_pairs(
    tmp_path: Path,
) -> None:
    """Keep concurrent same-kind report pairs isolated by run directory.

    :param tmp_path: Scratch output root shared by 20 emit workers.

    :returns: None; assertions in the body validate the regression.
    """
    report = import_script_module("lib.python.core.report")

    def emit_one(index: int) -> JSONObject:
        """Publish a report with a unique claim name for one worker.

        :param index: Worker number encoded in the report record.
        :returns: Parsed JSON report with its persisted file paths.
        """
        data = {
            "schema_version": "1.0",
            "kind": "storage_report",
            "status": "PASS",
            "target": "unit",
            "filters": {},
            "records": [{"name": f"claim-{index}"}],
            "errors": [],
            "summary": {"record_count": 1, "error_count": 0},
        }
        return json.loads(report.emit(data, "json", str(tmp_path)))

    with ThreadPoolExecutor(max_workers=8) as pool:
        returned = list(pool.map(emit_one, range(20)))

    run_directories = {Path(item["report_files"]["json"]).parent for item in returned}
    assert len(run_directories) == 20
    assert set(tmp_path.iterdir()) == run_directories
    for index, item in enumerate(returned):
        files = item["report_files"]
        persisted = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
        text = Path(files["text"]).read_text(encoding="utf-8")
        assert persisted == item
        assert persisted["records"][0]["name"] == f"claim-{index}"
        assert f"claim-{index}" in text
        assert files["json"] in text
        assert files["text"] in text
        assert sorted(path.name for path in Path(files["json"]).parent.iterdir()) == [
            "storage_report.json",
            "storage_report.txt",
        ]


def test_failed_pair_write_does_not_publish_partial_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove a report run directory if its text half cannot be written.

    :param tmp_path: Scratch output root that must remain empty.
    :param monkeypatch: Pytest fixture replacing the text-file writer.

    :returns: None; assertions in the body validate the regression.
    """
    report = import_script_module("lib.python.core.report")
    original_write_text = Path.write_text

    def fail_text(
        path: Path,
        content: str,
        *args: str,
        **kwargs: Union[str, int, bool, Path, Sequence[str], Mapping[str, str], None],
    ) -> int:
        """Fail only the text report write while forwarding other writes.

        :param path: Destination Path whose filename determines failure.
        :param content: Text submitted to the original writer.
        :param *args: Positional arguments forwarded to the helper under test.
        :param **kwargs: Keyword arguments forwarded to the fake command boundary.
        :returns: Original writer's character count for other files.
        :raises OSError: Destination is the storage report text file.

        """
        if path.name == "storage_report.txt":
            raise OSError("unit text failure")
        return original_write_text(path, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_text)
    with pytest.raises(OSError, match="unit text failure"):
        report.emit(
            {
                "kind": "storage_report",
                "status": "PASS",
                "target": "unit",
                "records": [],
                "errors": [],
            },
            "json",
            str(tmp_path),
        )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value",
    ([SECRET], {"primary": SECRET}, (SECRET,), [[SECRET]], {"a": {"b": SECRET}}),
)
def test_a_container_under_a_secret_named_key_is_replaced_whole(
    value: JSONValue,
) -> None:
    """Replace a secret-key value even when it is a nested container.

    :param value: List, tuple, or mapping containing a synthetic secret.

    :returns: None; assertions in the body validate the regression.
    """
    result = _runtime().redact_tree({"GITLAB_TOKEN": value})

    assert SECRET not in json.dumps(result)
    assert result == {"GITLAB_TOKEN": "[REDACTED]"}


@pytest.mark.parametrize(
    "document",
    (
        "token: |\n  {}\nnext: ok\n",
        "api_key: >\n  {}\n",
        "  password: |-\n    {}\n",
        "secret: |2\n  {}\n",
    ),
)
def test_a_yaml_block_scalar_value_is_redacted(document: str) -> None:
    """Redact multiline YAML scalar values without leaving secret text.

    :param document: YAML block-scalar form containing the synthetic value.

    :returns: None; assertions in the body validate the regression.
    """
    result = _runtime().redact(document.format(SECRET))

    assert SECRET not in result
    assert "[REDACTED]" in result


def test_a_block_scalar_redaction_stops_at_the_next_key() -> None:
    """Redacting the block must not swallow the rest of the document.
    :returns: None; assertions in the body validate the regression.
    """
    result = _runtime().redact(f"token: |\n  {SECRET}\nnext: ok\n")

    assert "next: ok" in result
