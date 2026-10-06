"""Connect installed command adapters to their sibling library.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path


def add_skill_lib() -> None:
    """Make the installed library importable from any working directory.

    :returns: None.
    """
    library = str(Path(__file__).resolve().parents[1] / "lib")
    if library not in sys.path:
        sys.path.insert(0, library)


# Installed entrypoints import this module before importing ``core``.
add_skill_lib()
