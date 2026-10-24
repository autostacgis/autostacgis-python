"""AutoSTACGIS 1.0 reference implementation."""

from .app import Application
from .artifacts import Artifact
from .checkpoint import Checkpoint, CheckpointReference
from .contracts import module
from .model import Record
from .registry import Registry

__version__ = "1.2.0"
__all__ = ["Application", "Artifact", "Checkpoint", "CheckpointReference", "Record", "Registry", "module"]
