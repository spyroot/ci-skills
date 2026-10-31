"""Define the single native state eligible for confirmed credential replacement.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from typing import Optional

from lib.python.core.git.mirroring import MirrorStatus, PushMirror


def requires_replacement(mirror: Optional[PushMirror]) -> bool:
    """Identify failed native configuration without replacing healthy mirrors.

    :param mirror: Selected independently observed native mirror, if present.
    :returns: Whether a live plan must explicitly confirm failed-mirror replacement.
    """
    return mirror is not None and mirror.update_status == MirrorStatus.FAILED
