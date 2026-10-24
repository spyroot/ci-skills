"""CLI contract tests for every public diagnostics command.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import argparse
import json
import shlex
from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from conftest import (
    import_script_module,
    install_executable,
    parse_json_output,
    read_journal,
    run_script,
)

if TYPE_CHECKING:
    from lib.python.core.json_types import JSONValue
    from lib.python.core.target import Target

SCRIPT_CASES = (
    ("access_check.py", (), {"github", "gitlab", "kubernetes"}),
    (
        "gitlab_job.py",
        ("--job-url", "https://gitlab.example.test/unit/repo/-/jobs/123"),
        {"gitlab"},
    ),
    ("storage_report.py", (), {"kubernetes"}),
    ("event_trace.py", (), {"kubernetes"}),
    ("cilium_status.py", (), {"kubernetes"}),
)


@pytest.mark.parametrize(
    ("script_name", "specific_flags"),
    (
        ("access_check.py", ("--publication",)),
        ("gitlab_job.py", ("--job-url", "--search")),
        (
            "storage_report.py",
            ("--namespace", "--node", "--storage-class", "--phase", "--search"),
        ),
        (
            "event_trace.py",
            (
                "--from",
                "--to",
                "--namespace",
                "--kind",
                "--object",
                "--reason",
                "--search",
            ),
        ),
        ("cilium_status.py", ("--namespace", "--node", "--search")),
    ),
)
def test_help_lists_common_and_script_specific_flags(
    script_name: str, specific_flags: Sequence[str]
) -> None:
    """Require each public script's help to list its shared and own options.

    :param script_name: Parametrized diagnostics entrypoint.
    :param specific_flags: Options expected only for that entrypoint.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(script_name, "--help")

    assert result.returncode == 0
    for flag in ("--revision", "--json", "--yaml", "--dry-run", "--help"):
        assert flag in result.stdout
    for flag in specific_flags:
        assert flag in result.stdout


@pytest.mark.parametrize(("script_name", "extra_args", "authorities"), SCRIPT_CASES)
def test_dry_run_json_lists_access_probes_without_external_calls(
    fake_bin: Path,
    call_journal: Path,
    target_file: Path,
    script_name: str,
    extra_args: Sequence[str],
    authorities: Collection[str],
) -> None:
    """Require JSON dry runs to describe probes without invoking native tools.

    :param fake_bin: Directory containing fail-fast fake ``gh``, ``glab``, and
            ``kubectl`` commands.
    :param call_journal: Path that records accidental fake-command execution.
    :param target_file: Nonsecret target path selected for the command.
    :param script_name: Parametrized diagnostics entrypoint.
    :param extra_args: Entry-specific arguments needed to reach its dry run.
    :param authorities: Exact authority names expected in its dry-run report.

    :returns: None; assertions in the body validate the regression.
    """
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
        "--json",
        "--dry-run",
        *extra_args,
        fake_bin=fake_bin,
        env={"CI_SKILLS_TARGET": str(target_file), "FAKE_JOURNAL": str(call_journal)},
    )
    data = parse_json_output(result)

    assert result.returncode == 0
    assert data["status"] == "DRY_RUN"
    if script_name == "gitlab_job.py":
        assert data["access"]["status"] == "DRY_RUN"
        assert data["access"]["target"]["kind"] == "project"
    else:
        assert set(data["surfaces"]) == authorities
    assert read_journal(call_journal) == []


def test_output_modes_are_mutually_exclusive(target_file: Path) -> None:
    """Require combined JSON/YAML flags to produce an argument error report.

    :param target_file: Nonsecret target injected to keep selection deterministic.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(
        "access_check.py",
        "--json",
        "--yaml",
        "--dry-run",
        env={"CI_SKILLS_TARGET": str(target_file)},
    )

    assert result.returncode == 2
    data = parse_json_output(result)
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "access_check"
    assert data["errors"][0]["source"] == "arguments"
    assert data["errors"][0]["reason"] == "invalid_arguments"


@pytest.mark.parametrize(
    ("script_name", "args"),
    (
        ("gitlab_access.py", ("check", "--unexpected")),
        ("gitlab_job.py", ("--job-url",)),
        ("gitlab_issue.py", ("open-bug", "--title")),
        ("access_check.py", ("--unexpected",)),
    ),
)
@pytest.mark.parametrize(
    ("mode", "loader"),
    (("--json", json.loads), ("--yaml", yaml.safe_load)),
)
def test_argparse_failures_keep_machine_output_contract(
    script_name: str,
    args: Sequence[str],
    mode: str,
    loader: Callable[[str], JSONValue],
) -> None:
    """Require invalid CLI inputs to emit structured machine errors.

    :param script_name: Parametrized command with an invalid option or value.
    :param args: Invalid argument sequence for that command.
    :param mode: JSON or YAML output flag appended to the invalid input.
    :param loader: Decoder corresponding to the selected machine format.

    :returns: None; assertions in the body validate the regression.
    """
    result = run_script(script_name, *args, mode)
    data = loader(result.stdout)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["errors"][0]["source"] == "arguments"
    assert data["errors"][0]["reason"]
    assert "usage:" not in result.stdout


@pytest.mark.parametrize(
    ("flag", "loader"),
    (("--json", json.loads), ("--yaml", yaml.safe_load)),
)
def test_machine_readable_target_failures_emit_blocked_envelope(
    tmp_path: Path,
    flag: str,
    loader: Callable[[str], JSONValue],
) -> None:
    """Require a missing selected target to emit a blocked machine report.

    :param tmp_path: Directory for a deliberately nonexistent target path.
    :param flag: JSON or YAML mode selected for the command.
    :param loader: Decoder for the selected mode.

    :returns: None; assertions in the body validate the regression.
    """
    missing_target = tmp_path / "missing-target.toml"

    result = run_script(
        "access_check.py", flag, env={"CI_SKILLS_TARGET": str(missing_target)}
    )
    data = loader(result.stdout)

    assert result.returncode == 2
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "access_check"
    assert data["errors"][0]["source"] == "target"
    assert data["errors"][0]["reason"]
    assert "BLOCKED:" not in result.stderr


@pytest.mark.parametrize(
    ("json_mode", "yaml_mode", "loader"),
    ((True, False, json.loads), (False, True, yaml.safe_load)),
)
def test_cli_execute_validation_errors_emit_machine_readable_envelope(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    json_mode: bool,
    yaml_mode: bool,
    loader: Callable[[str], JSONValue],
) -> None:
    """Keep collector validation failures in JSON or YAML output.

    :param monkeypatch: Fixture injecting target selection and fake access result.
    :param capsys: Fixture capturing direct CLI output.
    :param tmp_path: Directory for a minimal local kubeconfig and target.
    :param json_mode: Whether JSON output is selected in this case.
    :param yaml_mode: Whether YAML output is selected in this case.
    :param loader: Decoder for the selected output mode.

    :returns: None; assertions in the body validate the regression.
    """
    cli = import_script_module("lib.python.core.cli")
    status = import_script_module("lib.python.sys.status")
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("apiVersion: v1\n", encoding="utf-8")
    target_file = tmp_path / "target.toml"
    target_file.write_text(
        (
            "[github]\n"
            'host = "github.example.test"\n'
            'repository = "unit/repo"\n'
            "\n"
            "[gitlab]\n"
            'url = "https://gitlab.example.test"\n'
            "\n"
            "[kubernetes]\n"
            'context = "unit-context"\n'
            'server = "https://api.cluster.example.test:6443"\n'
            f'kubeconfig = "{kubeconfig}"\n'
        ),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        target=str(target_file),
        json=json_mode,
        yaml=yaml_mode,
        dry_run=False,
        output_dir=None,
        publication=False,
        revision="a" * 40,
    )

    monkeypatch.setattr(
        cli,
        "check_access",
        lambda _target, publication=False, cilium=False, state=None: {
            "kind": "access_check",
            "status": status.PASS,
        },
    )

    def collect_storage(
        _target: Target, _args: argparse.Namespace, *, state=None
    ) -> None:
        """Simulate malformed provider data reaching the collector boundary.

        :param _target: Selected target, unused by this failure fake.
        :param _args: Parsed collector options, unused by this failure fake.
        :param state: Shared invocation accepted by the collector double.
        :returns: None; the helper records behavior through its side effects.
        :raises ValueError: Always reports the nested response failure.

        """
        raise ValueError("malformed nested response")

    exit_status = cli.execute(args, collect_storage)
    captured = capsys.readouterr()
    data = loader(captured.out)

    assert exit_status == 2
    assert data["status"] == "BLOCKED"
    assert data["kind"] == "storage_report"
    assert data["errors"] == [
        {"source": "storage_report", "reason": "malformed nested response"}
    ]
    assert "BLOCKED:" not in captured.err


@pytest.mark.parametrize(
    ("script_name", "required"),
    (
        ("ceph_cluster.py", "--namespace"),
        ("ci-binary-build", "--spec"),
        ("ci-api", "--provider"),
    ),
)
def test_pre_use_help_renders_top_level_required_inputs(
    script_name: str,
    required: str,
) -> None:
    """Expose contract-root requirements even when argparse flags remain optional.

    :param script_name: Installed entrypoint declaring a root required option.
    :param required: Root-level input that must be distinguished from optional flags.
    :returns: None; assertions prove human help includes the declared prerequisite.
    """
    result = run_script(script_name, "--help")
    description = run_script(script_name, "--describe")
    assert result.returncode == description.returncode == 0
    contract = parse_json_output(description)
    assert required in contract["required_options"]
    normalized = " ".join(result.stdout.split())
    assert f"Requires: {', '.join(contract['required_options'])}." in normalized


def test_pre_use_help_preserves_per_operation_required_inputs() -> None:
    """Root prerequisite rendering preserves existing operation requirements.

    :returns: None; assertions prove each declared subcommand requirement is visible.
    """
    result = run_script("gitlab_runner.py", "--help")
    description = run_script("gitlab_runner.py", "--describe")
    assert result.returncode == description.returncode == 0
    contract = parse_json_output(description)
    normalized = " ".join(result.stdout.split())
    requirements = {
        operation: fields["required_options"]
        for operation, fields in contract["subcommands"].items()
        if fields.get("required_options")
    }
    assert requirements
    for operation, required in requirements.items():
        assert f"{operation} requires: {', '.join(required)}." in normalized


@pytest.mark.parametrize(
    "script_name", ("gitlab_runner.py", "gitlab_repository.py", "gitlab_mr.py")
)
def test_help_offers_same_reference_pointers_as_command_proposition(
    monkeypatch: pytest.MonkeyPatch, script_name: str
) -> None:
    """Show task reference names, summaries, and expansion commands without loading documents.

    :param monkeypatch: Fixture preventing hidden reference-document reads.
    :param script_name: Installed command whose help and proposition must agree.
    """
    catalogue = import_script_module("lib.python.core.catalog")
    command = import_script_module(script_name.removesuffix(".py"))
    references = catalogue.proposition(script_name)["reference"]
    assert references

    def forbid_document_read(_path: Path, *args: str, **kwargs: str) -> str:
        """Fail if rendering help attempts to fetch any reference document.

        :param _path: Unexpected document path requested by help rendering.
        :param args: Positional file-decoding arguments accepted by Path.read_text.
        :param kwargs: Keyword file-decoding arguments accepted by Path.read_text.
        :returns: No text; document fetching is forbidden in this test.
        :raises AssertionError: Help attempts to expand document content.
        """
        raise AssertionError(
            "command help must offer pointers without loading documents"
        )

    monkeypatch.setattr(Path, "read_text", forbid_document_read)
    parser = command.build_parser()
    parser.prog = script_name
    help_text = " ".join(parser.format_help().split())

    for reference in references:
        assert " ".join(reference["name"].split()) in help_text
        assert " ".join(reference["summary"].split()) in help_text
        assert f"reference.py {reference['pointer']}" in help_text


@pytest.mark.parametrize(
    "script_name",
    (
        "gitlab_repository.py",
        "gitlab_milestone.py",
        "gitlab_runner.py",
        "prepare_tool_pod.py",
    ),
)
def test_pre_use_help_explains_receipt_modes_before_invocation(
    script_name: str,
) -> None:
    """Expose accepted receipt modes in human help and the same command contract.

    :param script_name: Command with existing live receipt persistence rules.
    """
    result = run_script(script_name, "--help")
    description = run_script(script_name, "--describe")
    assert result.returncode == description.returncode == 0
    contract = parse_json_output(description)
    receipt = contract["options"]["--receipt-out"]
    help_text = " ".join(result.stdout.split())

    assert " ".join(receipt.split()) in help_text
    assert "--apply --confirm-plan" in receipt
    assert "offline" in receipt and "live-plan" in receipt
    assert any(
        phrase in receipt.lower()
        for phrase in ("not allowed", "forbidden", "unavailable", "rejected")
    )
    if script_name == "gitlab_runner.py":
        assert "get/list" in receipt and "read" in receipt.lower()
    else:
        assert "get/list" not in receipt


@pytest.mark.parametrize("option", ("--output-dir", "--log-file"))
@pytest.mark.parametrize(
    ("script_name", "offline_allowed"),
    (("gitlab_milestone.py", False), ("prepare_tool_pod.py", True)),
)
def test_pre_use_help_preserves_command_specific_offline_output_rules(
    script_name: str, offline_allowed: bool, option: str
) -> None:
    """Distinguish GitLab's offline refusal from the tool Pod's permitted outputs.

    :param script_name: Command whose existing persistence policy is described.
    :param offline_allowed: Whether that command permits ordinary offline output.
    :param option: Caller-selected report directory or diagnostic log destination.
    """
    result = run_script(script_name, "--help")
    description = run_script(script_name, "--describe")
    assert result.returncode == description.returncode == 0
    contract = parse_json_output(description)
    meaning = contract["options"][option]
    assert " ".join(meaning.split()) in " ".join(result.stdout.split())
    assert "offline" in meaning.lower()
    assert "path" in meaning.lower()
    if offline_allowed:
        assert "allowed" in meaning.lower()
        assert "not allowed" not in meaning.lower()
    else:
        assert any(
            phrase in meaning.lower()
            for phrase in ("not allowed", "forbidden", "unavailable", "rejected")
        )
        assert "--apply" in meaning


@pytest.mark.parametrize("script_name", ("gitlab_repository.py", "gitlab_milestone.py"))
def test_pre_use_examples_plan_without_files_and_apply_with_live_evidence(
    script_name: str,
) -> None:
    """Show usable plan/apply pairs with persistence confined to live execution.

    :param script_name: GitLab operation whose examples must work before first use.
    """
    catalogue = import_script_module("lib.python.core.catalog")
    workflow = import_script_module("lib.python.core.gitlab.gitlab_action")
    command = import_script_module(script_name.removesuffix(".py"))
    parser = command.build_parser()
    examples = catalogue.describe(script_name)["examples"]
    parsed = [
        parser.parse_args(shlex.split(example["command"])[1:]) for example in examples
    ]
    offline = [args for args in parsed if args.dry_run]
    confirmed = [args for args in parsed if args.apply and args.confirm_plan]
    assert offline and confirmed

    for args in offline:
        assert not (args.receipt_out or args.output_dir or args.log_file)
        assert workflow._workflow_mode(args) == "offline"
    for args in confirmed:
        assert args.receipt_out and args.output_dir and args.log_file
        assert workflow._workflow_mode(args) == "apply"
        if script_name == "gitlab_repository.py":
            assert args.action == "commit"
            assert (
                args.project
                and args.branch
                and args.commit_message
                and args.actions_file
            )
        else:
            assert args.action == "create"
            assert args.project and args.title
