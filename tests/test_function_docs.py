"""The source gate rejects missing or detached function contract fields."""

from __future__ import annotations

import sys

from conftest import REPO_ROOT, load_module

sys.path.insert(0, str(REPO_ROOT / "tools"))
DOCS = load_module(
    "check_function_docs", REPO_ROOT / "tools" / "check_function_docs.py"
)


def _source(tmp_path, content):
    scripts = tmp_path / "skills" / "ci-skills" / "scripts"
    scripts.mkdir(parents=True)
    (tmp_path / "tools").mkdir()
    (scripts / "sample.py").write_text(content, encoding="utf-8")
    return tmp_path


def test_complex_function_needs_adjacent_contract_fields(tmp_path):
    root = _source(
        tmp_path,
        "def validate(value):\n    if value:\n        return True\n    return False\n",
    )
    problems = DOCS.check(root)
    assert len(problems) == 1
    assert ":validate:missing:" in problems[0]
    assert "cleanup" in problems[0]


def test_directly_above_complete_contract_passes(tmp_path):
    comments = "\n".join(f"# {field}: documented" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\ndef validate(value):\n"
        "    if value:\n"
        "        return True\n"
        "    return False\n",
    )
    assert DOCS.check(root) == []


def test_detached_comment_does_not_count(tmp_path):
    comments = "\n".join(f"# {field}: documented" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\n\ndef validate(value):\n"
        "    if value:\n"
        "        return True\n"
        "    return False\n",
    )
    assert DOCS.check(root)


def test_repository_tool_functions_are_in_source_gate_scope(tmp_path):
    root = _source(tmp_path, "")
    (root / "tools" / "script.py").write_text(
        "def command(value):\n    if value:\n        return True\n    return False\n",
        encoding="utf-8",
    )
    assert any("tools/script.py" in problem for problem in DOCS.check(root))
