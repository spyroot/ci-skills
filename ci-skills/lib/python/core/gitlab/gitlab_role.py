"""Name GitLab roles observed from the native current-user response.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from enum import StrEnum


class GitLabRole(StrEnum):
    """Represent the native ``is_admin`` observation without declaring required access.

    .. attribute :: ADMIN

        The current-user response reports ``is_admin`` as true.

    .. attribute :: USER

        The current-user response does not report ``is_admin`` as true.
    """

    ADMIN = "admin"
    USER = "user"
