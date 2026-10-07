"""The target-file contract and the gate that refuses keys outside it.

`core/endpoints.py` declares the allowed keys once; `core/target.py` accepts
exactly those, and `gates/gate-ci-skills-endpoints.py` (library:
`tools/skillkit/endpoint_gate.py`) refuses any other key in the tracked
documents of the format. Gate tests run the command against throwaway git
repositories built from this checkout's real documents.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml
from conftest import REPO_ROOT, git, import_script_module, load_module

ENDPOINTS = import_script_module("core.endpoints")
CATALOG = import_script_module("core.catalog")
TARGET = import_script_module("core.target")
GATE_LIB = load_module(
    "skillkit_endpoint_gate", REPO_ROOT / "tools" / "skillkit" / "endpoint_gate.py"
)
GATE = REPO_ROOT / "gates" / "gate-ci-skills-endpoints.py"
SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "ci_skills_endpoints.schema.json").read_text()
)
TEMPLATE = "target.toml.template"
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


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A git repository holding the real target documents, committed once."""
    root = tmp_path / "repo"
    root.mkdir()
    documents = [REPO_ROOT / TEMPLATE, REPO_ROOT / "README.md"]
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
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _gate(root: Path, *args: str) -> tuple[int, dict]:
    result = _run(root, "--json", *args)
    record = json.loads(result.stdout)
    jsonschema.validate(record, SCHEMA)
    return result.returncode, record


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text() + text)


def _rules(record: dict) -> list[tuple[str, str]]:
    return [(item["rule"], item["path"]) for item in record["findings"]]


def _paths(text: str) -> set[str]:
    return {use.path for use in GATE_LIB.TargetDocument("t.toml", text).uses()}


# --- the contract and the parser that reads it ---------------------------------


def test_the_contract_accepts_exactly_the_keys_the_parser_accepted() -> None:
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
    assert CATALOG.AUTHORITIES == ENDPOINTS.TARGET_CONTRACT.authorities
    assert CATALOG.AUTHORITIES == ("github", "gitlab", "kubernetes")


def test_every_declared_key_is_a_pointer_or_a_listed_value() -> None:
    contract = ENDPOINTS.TARGET_CONTRACT
    assert contract.unpaired() == ()
    assert contract.role("gitlab.token_file") is ENDPOINTS.Role.ACCESS
    assert contract.role("kubernetes.server") is ENDPOINTS.Role.ENDPOINT
    assert contract.role("kubernetes.node_diagnostics") is None
    assert "gitlab.runner_id" in contract.value_keys()
    assert "kubernetes.kubeconfig" not in contract.value_keys()


def test_an_unknown_table_path_is_refused_by_the_contract() -> None:
    with pytest.raises(KeyError):
        ENDPOINTS.TARGET_CONTRACT.names("harbor")


def test_the_parser_rejects_a_key_the_contract_does_not_declare(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.toml"
    target.write_text(
        '[gitlab]\nurl = "https://gitlab.example.test"\nnamespace = "x"\n'
    )
    with pytest.raises(TARGET.TargetError, match="unsupported fields: namespace"):
        TARGET.load_gitlab_target(target)


def test_the_parser_keeps_the_journal_route_fields_and_refuses_them_for_cilium(
    tmp_path: Path,
) -> None:
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
    assert expected in _paths(text)


def test_a_binding_block_is_another_format_and_a_target_block_is_read() -> None:
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
    code, record = _gate(repo)
    assert code == 0
    assert record["status"] == "PASS"
    assert record["value_keys"] == list(ENDPOINTS.TARGET_CONTRACT.value_keys())
    assert f"{TEMPLATE}:1" in record["documents"]


def test_a_random_active_key_fails_with_its_line(repo: Path) -> None:
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
    _append(repo / TEMPLATE, "# pipeline_id = 42\n")
    code, record = _gate(repo)
    assert code == 1
    assert [rule for rule, path in _rules(record) if path.endswith(".pipeline_id")] == [
        "undeclared"
    ]


def test_a_new_authority_without_a_declaration_fails(repo: Path) -> None:
    _append(repo / TEMPLATE, '\n# [harbor]  # a registry\n# url = "x"\n')
    code, record = _gate(repo)
    assert code == 1
    assert ("undeclared", "harbor") in _rules(record)
    assert ("undeclared", "harbor.url") in _rules(record)


def test_a_random_key_in_a_documented_example_fails(repo: Path) -> None:
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
    access = repo / "ci-skills" / "references" / "access.md"
    access.write_text(
        access.read_text().replace("```toml\n[github]\n", "```toml\n[github\n")
    )
    code, record = _gate(repo)
    assert code == 1
    assert "parse_error" in [rule for rule, _ in _rules(record)]


def test_vendored_upstream_text_is_not_checked(repo: Path) -> None:
    vendored = repo / "ci-skills" / "references" / "vendor" / "upstream" / "page.md"
    vendored.parent.mkdir(parents=True)
    vendored.write_text('```toml\n[runners.kubernetes]\nnamespace = "x"\n```\n')
    code, record = _gate(repo)
    assert code == 0
    assert not any("vendor" in document for document in record["documents"])


def test_a_value_key_new_since_the_base_fails(repo: Path) -> None:
    template = repo / TEMPLATE
    current = template.read_text()
    template.write_text(current.replace("# runner_id = 123\n", ""))
    git(repo, *IDENTITY, "commit", "-qam", "base without runner_id")
    template.write_text(current)
    code, record = _gate(repo, "--base", "HEAD")
    assert code == 1
    assert _rules(record) == [("value_introduced", "gitlab.runner_id")]


def test_a_value_key_present_at_the_base_is_listed_not_failed(repo: Path) -> None:
    code, record = _gate(repo, "--base", "HEAD")
    assert code == 0
    assert "gitlab.runner_id" in record["value_keys"]


def test_a_declared_key_missing_from_the_template_fails(repo: Path) -> None:
    template = repo / TEMPLATE
    template.write_text(template.read_text().replace('# group = "group"\n', ""))
    code, record = _gate(repo)
    assert code == 1
    assert ("undocumented", "gitlab.group") in _rules(record)


def test_a_declared_table_missing_from_the_template_fails(repo: Path) -> None:
    template = repo / TEMPLATE
    header = "# [kubernetes.node_diagnostics.cilium]\n"
    assert header in template.read_text()
    template.write_text(template.read_text().replace(header, ""))
    code, record = _gate(repo)
    assert code == 1
    assert ("undocumented", "kubernetes.node_diagnostics.cilium") in _rules(record)


def test_an_unreadable_base_fails_closed(repo: Path) -> None:
    code, record = _gate(repo, "--base", "no-such-revision")
    assert code == 1
    assert _rules(record) == [("base_unreadable", TEMPLATE)]


def test_a_base_shaped_like_an_option_is_not_passed_to_git_as_one(
    repo: Path, tmp_path: Path
) -> None:
    leak = tmp_path / "leak"
    code, record = _gate(repo, f"--base=--output={leak}")
    assert code == 1
    assert _rules(record) == [("base_unreadable", TEMPLATE)]
    assert not leak.exists()


def test_the_staged_index_is_checked_not_the_working_tree(repo: Path) -> None:
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
    result = _run(tmp_path, "--json")
    assert result.returncode == 2
    assert result.stdout == ""


def test_an_authority_without_access_is_unpaired(repo: Path) -> None:
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
def test_the_schema_rejects_an_invalid_record(change) -> None:
    record = json.loads(json.dumps(VALID))
    change(record)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(record, SCHEMA)
