"""Share package-manager detection and installation between execution surfaces.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from enum import Enum


class PackageManager(str, Enum):
    """Native package managers supported by the dependency specification.

    .. attribute :: APT

        APT executable on Debian-family images.

    .. attribute :: DNF

        DNF executable on RPM-family images.

    .. attribute :: YUM

        Yum executable on RPM-family images.

    .. attribute :: UNAVAILABLE

        No supported native manager was observed.
    """

    APT = "apt-get"
    DNF = "dnf"
    YUM = "yum"
    UNAVAILABLE = "unavailable"


def package_manager_probe() -> str:
    """Render a read-only probe returning the first supported package manager.

    :returns: Shell command printing a PackageManager value, including unavailable.
    """
    managers = shlex.join(
        (PackageManager.APT.value, PackageManager.DNF.value, PackageManager.YUM.value)
    )
    return (
        f"for manager in {managers}; do "
        'if command -v "$manager" >/dev/null 2>&1; then '
        "printf '%s\\n' \"$manager\"; exit 0; fi; done; "
        f"printf '%s\\n' {shlex.quote(PackageManager.UNAVAILABLE.value)}"
    )


def package_install_operation(apt: Sequence[str], yum: Sequence[str]) -> str:
    """Render native package installation through the available declared manager.

    :param apt: Apt package names declared by dependencies.
    :param yum: DNF/Yum package names declared by dependencies.
    :returns: Shell command; fails when no declared package manager is available.
    """
    branches: list[str] = []
    if apt:
        packages = shlex.join(apt)
        branches.append(
            "if command -v apt-get >/dev/null 2>&1; then \\\n"
            "        apt-get update; \\\n"
            f"        DEBIAN_FRONTEND=noninteractive apt-get install -y "
            f"--no-install-recommends {packages}; \\\n"
            "        rm -rf /var/lib/apt/lists/*"
        )
    if yum:
        packages = shlex.join(yum)
        prefix = "elif" if branches else "if"
        branches.append(
            f"{prefix} command -v dnf >/dev/null 2>&1; then \\\n"
            f"        dnf install -y {packages}; \\\n"
            "        dnf clean all; \\\n"
            "    elif command -v yum >/dev/null 2>&1; then \\\n"
            f"        yum install -y {packages}; \\\n"
            "        yum clean all"
        )
    failure = "echo 'declared package manager unavailable' >&2; exit 1"
    if not branches:
        return failure
    branches.append(
        "else \\\n"
        "        echo 'declared package manager unavailable' >&2; \\\n"
        "        exit 1"
    )
    return "; \\\n    ".join(branches) + "; \\\n    fi"
