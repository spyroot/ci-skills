"""Read authenticated Git refs without cloning repository objects.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from lib.python.core.git.refs import GitRef, RefSnapshot

__all__ = ("GitRef", "RefSnapshot")
