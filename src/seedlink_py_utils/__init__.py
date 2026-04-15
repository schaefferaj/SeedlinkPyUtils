"""SeedlinkPyUtils — Python tools for working with real-time SeedLink data streams."""

from .viewer import run_viewer
from .viewer_mc import run_viewer_mc
from .archiver import run_archiver
from .info import query_info
from .dashboard import DashboardConfig, run_dashboard
from .config import ViewerConfig, THEMES, FILTERS
from .picker import PICKER_PRESETS

__version__ = "0.5.0"
__all__ = [
    "run_viewer",
    "run_viewer_mc",
    "run_archiver",
    "query_info",
    "run_dashboard",
    "DashboardConfig",
    "ViewerConfig",
    "THEMES",
    "FILTERS",
    "PICKER_PRESETS",
    "__version__",
]
