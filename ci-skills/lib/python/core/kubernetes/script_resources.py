"""Render installed scripts through one shared immutable ConfigMap mechanism.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from lib.python.core.json_types import JSONObject
from lib.python.core.kubernetes.run_scope import RunScope


def installed_scripts(sources: Mapping[str, Path]) -> JSONObject:
    """Read a checked script bundle from the installed skill.

    :param sources: ConfigMap filenames mapped to installed source paths.
    :returns: Filenames mapped to their exact UTF-8 source text.
    :raises OSError: If an installed script is absent or unreadable.
    :raises UnicodeError: If a script is not valid UTF-8.
    """  # noqa: DOC502 - Path.read_text propagates file and decoding errors.
    return {
        name: source.read_text(encoding="utf-8") for name, source in sources.items()
    }


def immutable_script_configmap(
    scope: RunScope,
    role: str,
    sources: Mapping[str, Path],
) -> JSONObject:
    """Render one run-owned immutable script ConfigMap.

    :param scope: Run scope supplying the concrete namespace and labels.
    :param role: Resource role used to derive the ConfigMap name.
    :param sources: ConfigMap filenames mapped to installed source paths.
    :returns: Native immutable ConfigMap containing exact installed bytes.
    :raises OSError: If an installed script is absent or unreadable.
    :raises UnicodeError: If a script is not valid UTF-8.
    """  # noqa: DOC502 - installed_scripts propagates its read failures.
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": scope.name(role),
            "namespace": scope.namespace,
            "labels": dict(scope.labels),
        },
        "immutable": True,
        "data": installed_scripts(sources),
    }
