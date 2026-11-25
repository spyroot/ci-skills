"""Import modules of an installed ci-skills copy and require that they load from it.

A qualification tool reads live state through the installed copy whose receipts it
completes. `tools/skillkit/mirror_qualification.py` and
`tools/qualify_agent_cleanup.py` put that copy first on ``sys.path`` and import its
``lib`` modules through ``installed_module``, which refuses a module that loaded
from another directory, for example a ``lib`` package imported earlier.
``file_module`` loads one stdlib-only repository module by path without changing
``sys.modules``, for a reason reported before any copy is bound.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import sys
from importlib import import_module, util
from pathlib import Path
from types import ModuleType
from typing import Final

INSTALLED_LIB_UNAVAILABLE: Final = "installed_lib_unavailable:{name}"
INSTALLED_LIB_SHADOWED: Final = "installed_lib_shadowed:{name}"


class InstalledCopyError(RuntimeError):
    """An installed module cannot be imported or loads from another directory."""


def bind_skill(skill: Path) -> None:
    """Put ``skill`` first on ``sys.path`` once.

    :param skill: Installed skill directory holding the ``lib`` package.
    """
    root = str(skill)
    if root not in sys.path:
        sys.path.insert(0, root)


def installed_module(skill: Path, name: str) -> ModuleType:
    """Import one installed module and require that it loads from ``skill``.

    :param skill: Installed skill directory.
    :param name: Dotted module name under ``lib``.
    :returns: The module.
    :raises InstalledCopyError: The module cannot be imported or loads from
        another directory.
    """
    try:
        module = import_module(name)
    except ImportError as exc:
        raise InstalledCopyError(INSTALLED_LIB_UNAVAILABLE.format(name=name)) from exc
    location = getattr(module, "__file__", None)
    if location is None or not Path(location).resolve().is_relative_to(skill.resolve()):
        raise InstalledCopyError(INSTALLED_LIB_SHADOWED.format(name=name))
    return module


def file_module(name: str, path: Path) -> ModuleType:
    """Load one module from ``path`` under ``name`` without adding it to ``sys.modules``.

    :param name: Module name to give the loaded module.
    :param path: Python source file that imports nothing outside the standard library.
    :returns: The loaded module.
    :raises ImportError: ``importlib.util`` returns no spec or loader for the file.
    """
    spec = util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(str(path), name=name)
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
