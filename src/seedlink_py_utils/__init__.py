"""SeedlinkPyUtils — Python tools for working with real-time SeedLink data streams."""

from .viewer import run_viewer
from .archiver import run_archiver
from .config import ViewerConfig, THEMES, FILTERS

__version__ = "0.1.0"
__all__ = [
    "run_viewer",
    "run_archiver",
    "ViewerConfig",
    "THEMES",
    "FILTERS",
    "__version__",
]
