"""Where the skill lives, declared once for every library module that needs it."""

from __future__ import annotations

from pathlib import Path

# core/ -> python/ -> lib/ -> the skill root: ci-skills/ in a checkout, the
# installed skill directory elsewhere.
SKILL_ROOT = Path(__file__).resolve().parents[3]
