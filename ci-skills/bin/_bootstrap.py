"""Put the skill's Python library on the import path for its commands.

Every command in this directory imports this module before ``lib.python``, so a
command runs from any working directory, in a checkout or an installed copy,
with no PYTHONPATH.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path

# The one declaration of where the Python library sits relative to bin/.
LIBRARY = Path(__file__).resolve().parents[1]

if str(LIBRARY) not in sys.path:
    sys.path.insert(0, str(LIBRARY))
