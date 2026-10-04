"""Portable operation receipts retain source identity without leaking secrets."""

from __future__ import annotations

import json

from conftest import import_script_module


def test_write_portable_receipt_redacts_and_replaces_atomically(tmp_path):
    portable = import_script_module("core.portable")
    destination = tmp_path / "receipt.json"
    destination.write_text("stale", encoding="utf-8")

    portable.write_portable_receipt(
        {
            "status": "PASS",
            "credential_source": "file:/private/operator/token",
            "target_file": "/private/operator/target.toml",
            "token": "sensitive-value",
        },
        str(destination),
    )

    report = json.loads(destination.read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["credential_source"].startswith("file:path:")
    assert report["target_file"].startswith("path:")
    assert report["token"] == "[REDACTED]"
    assert destination.stat().st_mode & 0o777 == 0o600
    assert sorted(tmp_path.iterdir()) == [destination]
