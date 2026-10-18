"""Public artifact-storage API for composed Kubernetes workflows.

Author Mustafa Bayramov
mbayramo@cisco.com
spyroot@gmail.com
"""

from lib.python.core.kubernetes.artifact_storage_callbacks import (
    CheckDestinationStorage,
    CopyArtifact,
    VerifyStoredArtifact,
    retain_verified_source,
)
from lib.python.core.kubernetes.artifact_storage_models import (
    ArtifactStorageAction,
    ArtifactStorageState,
    StorageDestination,
    StorageHandle,
)

__all__ = [
    "ArtifactStorageAction",
    "ArtifactStorageState",
    "CheckDestinationStorage",
    "CopyArtifact",
    "StorageDestination",
    "StorageHandle",
    "VerifyStoredArtifact",
    "retain_verified_source",
]
