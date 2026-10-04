"""The source gate rejects missing or detached function contract fields."""

from __future__ import annotations

import subprocess
import sys

from conftest import REPO_ROOT, load_module

sys.path.insert(0, str(REPO_ROOT / "tools"))
DOCS = load_module(
    "check_function_docs", REPO_ROOT / "tools" / "check_function_docs.py"
)


def _source(tmp_path, content, *, add_rest_doc=True):
    if add_rest_doc:
        content = content.replace(
            "def validate(value):\n",
            "def validate(value):\n"
            '    """Validate one value.\n\n'
            "    :param value: Value to inspect.\n"
            "    :returns: Whether the value is accepted.\n"
            '    """\n',
        )
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


def test_three_compact_comment_lines_cover_all_nine_fields(tmp_path):
    root = _source(
        tmp_path,
        "# Summary: select a target; Arguments: selector; Environment inputs: target file\n"
        "# Stdout: selected ID; Stderr: none; Exit classes: ValueError on bad selector\n"
        "# Side effects: none; Idempotency: stable inputs; Cleanup: none\n"
        "def validate(value):\n"
        "    if value:\n"
        "        return True\n"
        "    return False\n",
    )
    assert DOCS.check(root) == []


def test_empty_compact_field_is_not_accepted(tmp_path):
    root = _source(
        tmp_path,
        "# Summary: ; Arguments: selector; Environment inputs: target file\n"
        "# Stdout: selected ID; Stderr: none; Exit classes: ValueError\n"
        "# Side effects: none; Idempotency: stable; Cleanup: none\n"
        "def validate(value):\n"
        "    if value:\n"
        "        return True\n"
        "    return False\n",
    )
    assert any("summary" in item for item in DOCS.check(root))


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


def test_long_alias_only_function_is_trivial(tmp_path):
    root = _source(
        tmp_path,
        "def aliases(value):\n"
        "    first = value\n"
        "    second = first\n"
        "    third = second\n"
        "    return third\n",
    )
    assert DOCS.check(root) == []


def test_multiple_calls_are_nontrivial_without_a_branch(tmp_path):
    root = _source(
        tmp_path,
        "def build(parser):\n"
        "    parser.add_argument('--left')\n"
        "    parser.add_argument('--right')\n"
        "    return parser\n",
    )
    assert any(":build:missing:" in item for item in DOCS.check(root))


def test_nested_decision_belongs_to_nested_function(tmp_path):
    root = _source(
        tmp_path,
        "def outer():\n"
        "    def inner(value):\n"
        "        if value:\n"
        "            return 1\n"
        "        return 0\n"
        "    return inner\n",
    )
    problems = DOCS.check(root)
    assert any(":inner:missing:" in item for item in problems)
    assert not any(":outer:" in item for item in problems)


def test_nontrivial_function_requires_rest_docstring(tmp_path):
    comments = "\n".join(f"# {field}: described" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\ndef validate(value):\n"
        "    if value:\n        return True\n    return False\n",
        add_rest_doc=False,
    )
    assert any("docstring_missing" in item for item in DOCS.check(root))


def test_rest_parameters_match_signature_without_duplicates(tmp_path):
    comments = "\n".join(f"# {field}: described" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\ndef validate(value):\n"
        '    """Check a value.\n\n'
        "    :param other: Undeclared.\n"
        "    :param other: Duplicate.\n"
        "    :returns: Result.\n"
        '    """\n'
        "    if value:\n        return True\n    return False\n",
        add_rest_doc=False,
    )
    problems = DOCS.check(root)
    assert any("param_missing:value" in item for item in problems)
    assert any("param_unknown:other" in item for item in problems)
    assert any("param_duplicate:other" in item for item in problems)


def test_empty_rest_field_on_next_line_does_not_satisfy_parameter(tmp_path):
    comments = "\n".join(f"# {field}: described" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\ndef validate(value):\n"
        '    """Check a value.\n\n'
        "    :param value:\n"
        "    :returns: Result.\n"
        '    """\n'
        "    if value:\n        return True\n    return False\n",
        add_rest_doc=False,
    )
    assert any("param_missing:value" in item for item in DOCS.check(root))


def test_rest_return_and_explicit_raise_are_required(tmp_path):
    comments = "\n".join(f"# {field}: described" for field in DOCS.REQUIRED_FIELDS)
    root = _source(
        tmp_path,
        comments + "\ndef validate(value):\n"
        '    """Check a value.\n\n'
        "    :param value: Candidate.\n"
        '    """\n'
        "    if not value:\n        raise ValueError('empty')\n"
        "    return True\n",
        add_rest_doc=False,
    )
    problems = DOCS.check(root)
    assert any("returns_missing" in item for item in problems)
    assert any("raises_missing:ValueError" in item for item in problems)


def test_rest_scope_follows_changed_function_lines(tmp_path):
    comments = "\n".join(f"# {field}: described" for field in DOCS.REQUIRED_FIELDS)
    body = (
        comments + "\ndef validate(value):\n"
        "    if value:\n        return True\n    return False\n"
    )
    root = _source(tmp_path, body, add_rest_doc=False)
    for arguments in (
        ("init", "-q"),
        ("config", "user.name", "Unit"),
        ("config", "user.email", "unit@example.test"),
        ("add", "."),
        ("commit", "-qm", "baseline"),
    ):
        subprocess.run(["git", "-C", str(root), *arguments], check=True)
    base = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert DOCS.check(root, base=base) == []
    path = root / "skills" / "ci-skills" / "scripts" / "sample.py"
    revised = body.replace("# summary: described", "# summary: updated")
    assert revised != body
    path.write_text(revised, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "change adjacent API docs"],
        check=True,
    )
    assert DOCS.check(root, base=base) == []
    path.write_text(
        revised.replace("return True", "return bool(value)"),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "change function behavior"],
        check=True,
    )
    assert any("docstring_missing" in item for item in DOCS.check(root, base=base))
