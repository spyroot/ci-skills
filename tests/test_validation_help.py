"""Help contracts for the changed first-party validation CLIs."""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
from conftest import REPO_ROOT


TOOLS = (
    "check_delivery_policy.py",
    "check_function_docs.py",
    "check_standards_binding.py",
)
HEADINGS = ("Summary:", "Examples:", "Options:", "Output modes:", "Usage:")


@pytest.mark.parametrize("script", TOOLS)
def test_changed_validation_cli_help_is_complete_and_machine_readable(script):
    path = REPO_ROOT / "tools" / script
    human = subprocess.run(
        [sys.executable, str(path), "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    machine = subprocess.run(
        [sys.executable, str(path), "--describe"],
        capture_output=True,
        text=True,
        check=True,
    )
    contract = json.loads(machine.stdout)
    assert set(contract) == {
        "summary", "description", "examples", "options", "output_modes", "usage"
    }
    assert contract["summary"] and contract["usage"]
    assert contract["examples"] == [
        {
            "purpose": "Inspect this command's machine-readable interface",
            "command": f"{script} --describe",
        }
    ]
    assert contract["output_modes"] == {"json": "status JSON on stdout"}
    positions = [
        re.search(r"(?m)^" + re.escape(heading), human).start()
        for heading in HEADINGS
    ]
    assert positions == sorted(positions)
    assert re.search(r"(?m)^  # .+\n  \S+ --describe$", human)
    assert all(option in human for option in contract["options"])
    assert {
        "--help", "--describe", "--root", "--dry-run", "--log-format",
        "--log-level", "--log-file", "--run-id",
    } <= set(contract["options"])
    extra = {
        "check_function_docs.py": "--base",
        "check_standards_binding.py": "--standards",
    }
    if script in extra:
        assert extra[script] in contract["options"]
