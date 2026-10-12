"""The target-file contract and the gate that refuses keys outside it.

`core/endpoints.py` declares the allowed keys once; `core/target.py` accepts
exactly those, and `gates/gate-ci-skills-endpoints.py` (library:
`tools/skillkit/endpoint_gate.py`) refuses any other key in the tracked
documents of the format. Gate tests run the command against throwaway git
repositories built from this checkout's real documents.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import MutableSequence, MutableSet, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema
import pytest
import yaml
from conftest import REPO_ROOT, git, import_script_module, load_module

ENDPOINTS = import_script_module("lib.python.core.endpoints")
CATALOG = import_script_module("lib.python.core.catalog")
TARGET = import_script_module("lib.python.core.target")
GATE_LIB = load_module(
    "skillkit_endpoint_gate", REPO_ROOT / "tools" / "skillkit" / "endpoint_gate.py"
)
GATE = REPO_ROOT / "gates" / "gate-ci-skills-endpoints.py"
SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "ci_skills_endpoints.schema.json").read_text()
)
TEMPLATE = "target.toml.template"
UNKNOWN_AUTHORITY = "unregistered_authority"
# Commits in throwaway repositories must not depend on the caller's git config.
IDENTITY = (
    "-c",
    "user.email=gate@example.test",
    "-c",
    "user.name=gate",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
)


if TYPE_CHECKING:
    from lib.python.core.json_types import JSONObject


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Commit the real target template and Markdown examples in a temporary Git repo.

    :param tmp_path: Pytest directory receiving copied documents and Git history.
    :returns: Root of the isolated committed repository used by gate tests.
    """
    root = tmp_path / "repo"
    root.mkdir()
    documents: MutableSequence[Path] = [REPO_ROOT / TEMPLATE, REPO_ROOT / "README.md"]
    documents += sorted((REPO_ROOT / "ci-skills").glob("**/*.md"))
    for document in documents:
        copy = root / document.relative_to(REPO_ROOT)
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(document, copy)
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, *IDENTITY, "commit", "-qm", "base")
    return root


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the endpoint gate command against a selected document repository.

    :param root: Repository root passed through the gate's root option.
    :param *args: Positional arguments forwarded to the helper under test.
    :returns: Completed command with captured standard output, error, and exit code.

    """
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _gate(root: Path, *args: str) -> tuple[int, JSONObject]:
    """Run the gate in JSON mode and validate its result schema.

    :param root: Repository containing the target documents to inspect.
    :param *args: Positional arguments forwarded to the helper under test.
    :returns: Gate exit code and schema-valid JSON result mapping.

    """
    result = _run(root, "--json", *args)
    record = json.loads(result.stdout)
    jsonschema.validate(record, SCHEMA)
    return result.returncode, record


def _append(path: Path, text: str) -> None:
    """Append a case-specific target fragment to an existing fixture document.

    :param path: Document in the temporary repository to extend.
    :param text: Target fragment or comment appended to that document.

    :returns: None; the helper records behavior through its side effects.
    """
    path.write_text(path.read_text() + text)


def _rules(record: JSONObject) -> Sequence[tuple[str, str]]:
    """Extract gate finding rules with their target-contract paths.

    :param record: JSON gate result containing finding objects.
    :returns: Rule and target-path pairs in report order.
    """
    return [(item["rule"], item["path"]) for item in record["findings"]]


def _paths(text: str) -> MutableSet[str]:
    """Parse target text and collect every observed table or key path.

    :param text: TOML document or commented target example to scan.
    :returns: Set of target-contract paths found by the document parser.
    """
    return {use.path for use in GATE_LIB.TargetDocument("t.toml", text).uses()}


# --- the contract and the parser that reads it ---------------------------------


def test_the_contract_accepts_exactly_the_keys_the_parser_accepted() -> None:
    """Keep each GitHub, GitLab, Kubernetes, and diagnostics key set explicit.
    :returns: None; assertions in the body validate the regression.
    """
    contract = ENDPOINTS.TARGET_CONTRACT
    assert contract.names("github") == {
        "host",
        "repository",
        "token_file",
        "required_checks",
    }
    assert contract.names("gitlab") == {
        "url",
        "token_file",
        "project",
        "group",
        "runner_id",
    }
    assert contract.names("kubernetes") == {
        "context",
        "server",
        "kubeconfig",
        "kubeconfigs",
        "node_diagnostics",
    }
    assert contract.names("kubernetes.node_diagnostics") == {
        "node",
        "cilium",
        "journal",
    }
    assert contract.names("kubernetes.node_diagnostics.cilium") == {
        "namespace",
        "selector",
        "container",
    }
    assert contract.names("kubernetes.node_diagnostics.journal") == {
        "namespace",
        "selector",
        "container",
        "directory",
        "host_path",
    }


def test_the_authorities_come_from_the_contract() -> None:
    """Derive the public authority list from the target contract.
    :returns: None; assertions in the body validate the regression.
    """
    assert CATALOG.AUTHORITIES == ENDPOINTS.TARGET_CONTRACT.authorities
    assert CATALOG.AUTHORITIES == ("github", "gitlab", "harbor", "kubernetes")


def test_every_declared_key_is_a_pointer_or_a_listed_value() -> None:
    """Classify declared endpoint, access, and value keys without gaps.
    :returns: None; assertions in the body validate the regression.
    """
    contract = ENDPOINTS.TARGET_CONTRACT
    assert contract.unpaired() == ()
    assert contract.role("gitlab.token_file") is ENDPOINTS.Role.ACCESS
    assert contract.role("kubernetes.server") is ENDPOINTS.Role.ENDPOINT
    assert contract.role("kubernetes.node_diagnostics") is None
    assert "gitlab.runner_id" in contract.value_keys()
    assert "kubernetes.kubeconfig" not in contract.value_keys()


def test_an_unknown_table_path_is_refused_by_the_contract() -> None:
    """Raise for an authority table absent from the target contract.
    :returns: None; assertions in the body validate the regression.
    """
    with pytest.raises(KeyError):
        ENDPOINTS.TARGET_CONTRACT.names(UNKNOWN_AUTHORITY)


def test_the_parser_rejects_a_key_the_contract_does_not_declare(
    tmp_path: Path,
) -> None:
    """Reject an undeclared GitLab namespace key in a target file.

    :param tmp_path: Pytest directory holding the invalid target file.

    :returns: None; assertions in the body validate the regression.
    """
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nnamespace = "x"\n'
    )
    with pytest.raises(TARGET.TargetError, match="unsupported fields: namespace"):
        TARGET.load_gitlab_target(target)


def test_the_parser_keeps_the_journal_route_fields_and_refuses_them_for_cilium(
    tmp_path: Path,
) -> None:
    """Retain journal mount fields while rejecting them on the Cilium route.

    :param tmp_path: Pytest directory holding both target variants.

    :returns: None; assertions in the body validate the regression.
    """
    target = tmp_path / "target.toml"
    head = (
        "[kubernetes]\n"
        'context = "admin"\nserver = "https://api.cluster.example.test:6443"\n'
        '[kubernetes.node_diagnostics]\nnode = "n1"\n'
    )
    route = 'namespace = "ns"\nselector = "app=x"\ncontainer = "c"\n'
    target.write_text(
        head
        + "[kubernetes.node_diagnostics.journal]\n"
        + route
        + 'directory = "/host-journal"\nhost_path = "/var/log/journal"\n'
    )
    loaded = TARGET.load_target(target, required_surfaces=("kubernetes",))
    journal = loaded.kubernetes.node_diagnostics.journal
    assert (journal.directory, journal.host_path) == (
        "/host-journal",
        "/var/log/journal",
    )

    target.write_text(
        head + "[kubernetes.node_diagnostics.cilium]\n" + route + 'directory = "/x"\n'
    )
    with pytest.raises(TARGET.TargetError, match="unsupported fields: directory"):
        TARGET.load_target(target, required_surfaces=("kubernetes",))


# --- reading documents --------------------------------------------------------------


def test_commented_keys_belong_to_the_commented_table_and_active_keys_do_not() -> None:
    """Attribute commented and active keys to their respective table contexts.
    :returns: None; assertions in the body validate the regression.
    """
    document = GATE_LIB.TargetDocument(
        "t.toml",
        "[kubernetes]\n"
        'context = "c"\n'
        "# [kubernetes.node_diagnostics]\n"
        '# node = "n1"\n'
        'server = "https://api.example.test"\n',
    )
    lines = {use.path: use.line for use in document.uses()}
    assert lines["kubernetes.node_diagnostics.node"] == 4
    assert lines["kubernetes.server"] == 5
    assert "kubernetes.node_diagnostics.server" not in lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param(
            "# [harbor]  # optional registry\n# url = 1\n",
            "harbor.url",
            id="trailing-comment",
        ),
        pytest.param("## [harbor]\n## url = 1\n", "harbor.url", id="double-hash"),
        pytest.param('# [ "harbor" ]\n# url = 1\n', "harbor.url", id="quoted-spaced"),
        pytest.param("[gitlab]\n# 'evil' = 1\n", "gitlab.evil", id="single-quoted"),
        pytest.param('[gitlab]\n# "a b" = 1\n', "gitlab.a b", id="spaced-quoted"),
        pytest.param(
            '[kubernetes]\nextra = [{ pod = "x" }]\n',
            "kubernetes.extra.pod",
            id="table-in-array",
        ),
        pytest.param(
            '[[kubernetes.sources]]\nkind = "file"\n',
            "kubernetes.sources.kind",
            id="array-table",
        ),
    ],
)
def test_every_spelling_of_a_table_or_key_is_seen(text: str, expected: str) -> None:
    """Recognize commented, quoted, spaced, array, and array-table key paths.

    :param text: TOML spelling selected by the parametrized parser case.
    :param expected: Contract path expected from that spelling.

    :returns: None; assertions in the body validate the regression.
    """
    assert expected in _paths(text)


def test_a_binding_block_is_another_format_and_a_target_block_is_read() -> None:
    """Skip binding TOML while scanning target examples in Markdown fences.
    :returns: None; assertions in the body validate the regression.
    """
    markdown = (
        "```toml\n"
        'schema_version = "1.0"\n'
        'target = "./target.toml"\n'
        "[[kubernetes.sources]]\n"
        'kind = "file"\n'
        "```\n"
        "~~~TOML\n"
        'harbor_url = "x"\n'
        "[gitlab]\n"
        "~~~\n"
        "  ```toml title=example\n"
        "  [gitlab]\n"
        "  ```\n"
        "```toml\n"
        "# just a note\n"
        "```\n"
    )
    documents = GATE_LIB.MarkdownExamples("doc.md", markdown).documents()
    assert [document.first_line for document in documents] == [8, 12, 15]
    assert "harbor_url" in {use.path for use in documents[0].uses()}


# --- the gate, run as a command ------------------------------------------------------


def test_the_gate_passes_the_real_documents_and_lists_the_value_keys(
    repo: Path,
) -> None:
    """Pass tracked target documents and list all declared value keys.

    :param repo: Fixture repository committed with real target documents.

    :returns: None; assertions in the body validate the regression.
    """
    code, record = _gate(repo)
    assert code == 0
    assert record["status"] == "PASS"
    assert record["value_keys"] == list(ENDPOINTS.TARGET_CONTRACT.value_keys())
    assert f"{TEMPLATE}:1" in record["documents"]


def test_a_random_active_key_fails_with_its_line(repo: Path) -> None:
    """Report an undeclared active GitLab key at its exact template line.

    :param repo: Fixture repository whose target template receives the key.

    :returns: None; assertions in the body validate the regression.
    """
    template = repo / TEMPLATE
    lines = template.read_text().splitlines()
    index = lines.index('url = "https://gitlab.example.com"')
    lines.insert(index + 1, 'namespace = "ci"')
    template.write_text("\n".join(lines) + "\n")
    code, record = _gate(repo)
    assert code == 1
    assert record["findings"] == [
        {
            "rule": "undeclared",
            "path": "gitlab.namespace",
            "source": TEMPLATE,
            "line": index + 2,
            "detail": "not an endpoint or access key of the target contract",
        }
    ]


def test_a_commented_random_key_fails(repo: Path) -> None:
    """Reject an undeclared key even when it appears only as a comment.

    :param repo: Fixture repository whose target template receives the comment.

    :returns: None; assertions in the body validate the regression.
    """
    _append(repo / TEMPLATE, "# pipeline_id = 42\n")
    code, record = _gate(repo)
    assert code == 1
    assert [rule for rule, path in _rules(record) if path.endswith(".pipeline_id")] == [
        "undeclared"
    ]


def test_a_new_authority_without_a_declaration_fails(repo: Path) -> None:
    """Reject a commented authority table and key absent from the contract.

    :param repo: Fixture repository whose template receives the new authority.

    :returns: None; assertions in the body validate the regression.
    """
    _append(repo / TEMPLATE, f'\n# [{UNKNOWN_AUTHORITY}]\n# url = "x"\n')
    code, record = _gate(repo)
    assert code == 1
    assert ("undeclared", UNKNOWN_AUTHORITY) in _rules(record)
    assert ("undeclared", f"{UNKNOWN_AUTHORITY}.url") in _rules(record)


def test_a_random_key_in_a_documented_example_fails(repo: Path) -> None:
    """Report an undeclared Kubernetes key in a Markdown target example.

    :param repo: Fixture repository whose access example receives the key.

    :returns: None; assertions in the body validate the regression.
    """
    access = repo / "ci-skills" / "references" / "access.md"
    access.write_text(
        access.read_text().replace(
            'context = "admin-context"\n',
            'context = "admin-context"\nnamespace = "x"\n',
        )
    )
    code, record = _gate(repo)
    assert code == 1
    finding = record["findings"][0]
    assert (finding["rule"], finding["path"]) == ("undeclared", "kubernetes.namespace")
    assert finding["source"] == "ci-skills/references/access.md"


def test_a_top_level_key_in_an_example_is_not_skipped(repo: Path) -> None:
    """Inspect and reject top-level keys in a fenced target example.

    :param repo: Fixture repository whose access example receives the key.

    :returns: None; assertions in the body validate the regression.
    """
    access = repo / "ci-skills" / "references" / "access.md"
    access.write_text(
        access.read_text().replace(
            "```toml\n[github]\n", '```toml\nharbor_url = "x"\n[github]\n'
        )
    )
    code, record = _gate(repo)
    assert code == 1
    assert ("undeclared", "harbor_url") in _rules(record)


def test_an_example_that_does_not_parse_fails(repo: Path) -> None:
    """Fail the gate when a Markdown target example contains invalid TOML.

    :param repo: Fixture repository whose example gets a broken table header.

    :returns: None; assertions in the body validate the regression.
    """
    access = repo / "ci-skills" / "references" / "access.md"
    access.write_text(
        access.read_text().replace("```toml\n[github]\n", "```toml\n[github\n")
    )
    code, record = _gate(repo)
    assert code == 1
    assert "parse_error" in [rule for rule, _ in _rules(record)]


def test_vendored_upstream_text_is_not_checked(repo: Path) -> None:
    """Exclude vendored upstream Markdown from target-example validation.

    :param repo: Fixture repository receiving a vendored upstream page.

    :returns: None; assertions in the body validate the regression.
    """
    vendored = repo / "ci-skills" / "references" / "vendor" / "upstream" / "page.md"
    vendored.parent.mkdir(parents=True)
    vendored.write_text('```toml\n[runners.kubernetes]\nnamespace = "x"\n```\n')
    code, record = _gate(repo)
    assert code == 0
    assert not any("vendor" in document for document in record["documents"])


def test_a_value_key_new_since_the_base_fails(repo: Path) -> None:
    """Flag a target value key introduced after the chosen Git base.

    :param repo: Fixture repository with a base commit lacking runner_id.

    :returns: None; assertions in the body validate the regression.
    """
    template = repo / TEMPLATE
    current = template.read_text()
    template.write_text(current.replace("# runner_id = 123\n", ""))
    git(repo, *IDENTITY, "commit", "-qam", "base without runner_id")
    template.write_text(current)
    code, record = _gate(repo, "--base", "HEAD")
    assert code == 1
    assert _rules(record) == [("value_introduced", "gitlab.runner_id")]


def test_a_value_key_present_at_the_base_is_listed_not_failed(repo: Path) -> None:
    """List an existing value key without treating it as newly introduced.

    :param repo: Fixture repository whose base already declares runner_id.

    :returns: None; assertions in the body validate the regression.
    """
    code, record = _gate(repo, "--base", "HEAD")
    assert code == 0
    assert "gitlab.runner_id" in record["value_keys"]


def test_a_declared_key_missing_from_the_template_fails(repo: Path) -> None:
    """Report a contract key whose template documentation was removed.

    :param repo: Fixture repository whose template omits GitLab group.

    :returns: None; assertions in the body validate the regression.
    """
    template = repo / TEMPLATE
    template.write_text(template.read_text().replace('# group = "group"\n', ""))
    code, record = _gate(repo)
    assert code == 1
    assert ("undocumented", "gitlab.group") in _rules(record)


def test_a_declared_table_missing_from_the_template_fails(repo: Path) -> None:
    """Report a diagnostics table absent from the target template.

    :param repo: Fixture repository whose template omits the Cilium table header.

    :returns: None; assertions in the body validate the regression.
    """
    template = repo / TEMPLATE
    header = "# [kubernetes.node_diagnostics.cilium]\n"
    assert header in template.read_text()
    template.write_text(template.read_text().replace(header, ""))
    code, record = _gate(repo)
    assert code == 1
    assert ("undocumented", "kubernetes.node_diagnostics.cilium") in _rules(record)


def test_an_unreadable_base_fails_closed(repo: Path) -> None:
    """Fail closed when the selected Git base revision cannot be read.

    :param repo: Fixture repository queried with a nonexistent base revision.

    :returns: None; assertions in the body validate the regression.
    """
    code, record = _gate(repo, "--base", "no-such-revision")
    assert code == 1
    assert _rules(record) == [("base_unreadable", TEMPLATE)]


def test_a_base_shaped_like_an_option_is_not_passed_to_git_as_one(
    repo: Path, tmp_path: Path
) -> None:
    """Treat an option-shaped Git base as data without creating its output file.

    :param repo: Fixture repository used for the base comparison.
    :param tmp_path: Pytest directory holding the sentinel output path.

    :returns: None; assertions in the body validate the regression.
    """
    leak = tmp_path / "leak"
    code, record = _gate(repo, f"--base=--output={leak}")
    assert code == 1
    assert _rules(record) == [("base_unreadable", TEMPLATE)]
    assert not leak.exists()


def test_the_staged_index_is_checked_not_the_working_tree(repo: Path) -> None:
    """Inspect staged template bytes independently of clean working-tree bytes.

    :param repo: Fixture repository with an undeclared key staged in the index.

    :returns: None; assertions in the body validate the regression.
    """
    template = repo / TEMPLATE
    clean = template.read_text()
    _append(template, "# pipeline_id = 42\n")
    git(repo, "add", TEMPLATE)
    template.write_text(clean)
    staged_code, staged = _gate(repo, "--staged")
    working_code, _ = _gate(repo)
    assert (staged_code, working_code) == (1, 0)
    assert any(path.endswith(".pipeline_id") for _, path in _rules(staged))


def test_a_missing_template_means_the_gate_could_not_run(tmp_path: Path) -> None:
    """Exit with an execution error when no target template is present.

    :param tmp_path: Empty Pytest directory used as the gate root.

    :returns: None; assertions in the body validate the regression.
    """
    result = _run(tmp_path, "--json")
    assert result.returncode == 2
    assert result.stdout == ""


def test_an_authority_without_access_is_unpaired(repo: Path) -> None:
    """Report a contract authority declaring an endpoint but no access key.

    :param repo: Fixture repository supplied to the direct gate instance.

    :returns: None; assertions in the body validate the regression.
    """
    contract = ENDPOINTS.TargetContract(
        (
            ENDPOINTS.Table(
                "registry", ENDPOINTS.Field.of(ENDPOINTS.Role.ENDPOINT, "url")
            ),
        )
    )
    result = GATE_LIB.EndpointGate(GATE_LIB.WorkingTree(repo), None, contract).run()
    assert ("unpaired", "registry") in [(str(f.rule), f.path) for f in result.findings]


def test_yaml_output_is_the_same_record(repo: Path) -> None:
    """Render YAML and JSON gate modes as equivalent result records.

    :param repo: Fixture repository scanned in both output modes.

    :returns: None; assertions in the body validate the regression.
    """
    _, record = _gate(repo)
    assert yaml.safe_load(_run(repo, "--yaml").stdout) == record


# --- the result schema's own fixtures --------------------------------------------

VALID = {
    "kind": "ci_skills_endpoints",
    "schema_version": "1.0",
    "status": "FAIL",
    "base": "HEAD",
    "documents": ["target.toml.template:1"],
    "value_keys": ["gitlab.runner_id"],
    "findings": [
        {
            "rule": "undeclared",
            "path": "gitlab.namespace",
            "source": "target.toml.template",
            "line": 51,
            "detail": "not an endpoint or access key of the target contract",
        }
    ],
}


def test_the_schema_accepts_a_valid_record() -> None:
    """Accept the complete endpoint-gate result fixture under its JSON schema.
    :returns: None; assertions in the body validate the regression.
    """
    jsonschema.validate(VALID, SCHEMA)


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda r: r.pop("status"), id="missing-required"),
        pytest.param(lambda r: r.update(extra=1), id="unknown-field"),
        pytest.param(lambda r: r["findings"][0].update(line="51"), id="wrong-type"),
        pytest.param(
            lambda r: r["findings"][0].update(rule="maybe"), id="unknown-rule"
        ),
        pytest.param(lambda r: r.update(schema_version="2.0"), id="next-major"),
        pytest.param(
            lambda r: r.update(documents=["target.toml.template"]), id="no-line"
        ),
    ],
)
def test_the_schema_rejects_an_invalid_record(change: str) -> None:
    """Reject missing, extra, mistyped, or unsupported result fields.

    :param change: Fixture mutation for one invalid endpoint-gate record case.

    :returns: None; assertions in the body validate the regression.
    """
    record = json.loads(json.dumps(VALID))
    change(record)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, SCHEMA)
