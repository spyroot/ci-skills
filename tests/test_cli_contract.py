"""CLI contract tests for every public diagnostics command."""

from __future__ import annotations

import pytest
from conftest import install_executable, parse_json_output, read_journal, run_script

SCRIPT_CASES = (
    ("access_check.py", ()),
    ("gitlab_job.py", ("--job-url", "https://gitlab.example.test/unit/repo/-/jobs/123")),
    ("storage_report.py", ()),
    ("event_trace.py", ()),
    ("cilium_status.py", ()),
)


@pytest.mark.parametrize(
    ("script_name", "specific_flags"),
    (
        ("access_check.py", ()),
        ("gitlab_job.py", ("--job-url", "--search")),
        ("storage_report.py", ("--namespace", "--node", "--storage-class", "--phase", "--search")),
        (
            "event_trace.py",
            ("--from", "--to", "--namespace", "--kind", "--object", "--reason", "--search"),
        ),
        ("cilium_status.py", ("--namespace", "--node", "--search")),
    ),
)
def test_help_lists_common_and_script_specific_flags(script_name, specific_flags):
    """Help output is dependency-free and documents every supported argument."""
    result = run_script(script_name, "--help")

    assert result.returncode == 0
    for flag in ("--target", "--json", "--yaml", "--dry-run", "--help"):
        assert flag in result.stdout
    for flag in specific_flags:
        assert flag in result.stdout


@pytest.mark.parametrize(("script_name", "extra_args"), SCRIPT_CASES)
def test_dry_run_json_lists_access_probes_without_external_calls(
    fake_bin,
    call_journal,
    target_file,
    script_name,
    extra_args,
):
    """Dry run is a local explanation mode and cannot contact live tools."""
    body = (
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "journal = pathlib.Path(os.environ['FAKE_JOURNAL'])\n"
        "with journal.open('a', encoding='utf-8') as handle:\n"
        "    handle.write(json.dumps({'tool': pathlib.Path(sys.argv[0]).name, 'argv': sys.argv[1:]}) + '\\n')\n"
        "raise SystemExit(99)\n"
    )
    for tool in ("gh", "glab", "kubectl"):
        install_executable(fake_bin, tool, body)

    result = run_script(
        script_name,
        "--target",
        target_file,
        "--json",
        "--dry-run",
        *extra_args,
        fake_bin=fake_bin,
        env={"FAKE_JOURNAL": str(call_journal)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["status"] == "DRY_RUN"
    assert set(data["surfaces"]) == {"github", "gitlab", "kubernetes"}
    assert read_journal(call_journal) == []


def test_output_modes_are_mutually_exclusive(target_file):
    """argparse rejects ambiguous machine-readable output selection."""
    result = run_script(
        "access_check.py",
        "--target",
        target_file,
        "--json",
        "--yaml",
        "--dry-run",
    )

    assert result.returncode == 2
    assert "not allowed with argument" in result.stderr
