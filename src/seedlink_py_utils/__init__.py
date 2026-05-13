"""SeedlinkPyUtils — Python tools for working with real-time SeedLink data streams.

Public attributes (``run_viewer``, ``PPSDConfig``, etc.) are lazy-loaded via
PEP 562 ``__getattr__`` so that importing the package doesn't eagerly pull
in matplotlib. This matters for the headless PPSD archiver:
``seedlink-py-ppsd-archive`` wants ``matplotlib.use("Agg")`` to take effect
before any pyplot import, and with eager imports the viewer module would
lock in the auto-selected backend before the archiver CLI could set Agg.
"""

__version__ = "0.10.0"

__all__ = [
    "run_viewer",
    "run_viewer_mc",
    "run_archiver",
    "query_info",
    "run_dashboard",
    "run_ppsd",
    "run_ppsd_archive",
    "run_web",
    "DashboardConfig",
    "PPSDConfig",
    "PPSDArchiveConfig",
    "ViewerConfig",
    "MonitorConfig",
    "WebConfig",
    "StaleWatcher",
    "THEMES",
    "FILTERS",
    "PICKER_PRESETS",
    "__version__",
]


# Mapping: public name → (submodule, attribute). Submodules are imported
# lazily on first access via ``__getattr__`` below.
_LAZY_ATTRS = {
    "run_viewer":         ("viewer",       "run_viewer"),
    "run_viewer_mc":      ("viewer_mc",    "run_viewer_mc"),
    "run_archiver":       ("archiver",     "run_archiver"),
    "query_info":         ("info",         "query_info"),
    "run_dashboard":      ("dashboard",    "run_dashboard"),
    "DashboardConfig":    ("dashboard",    "DashboardConfig"),
    "run_ppsd":           ("ppsd",         "run_ppsd"),
    "PPSDConfig":         ("ppsd",         "PPSDConfig"),
    "run_ppsd_archive":   ("ppsd_archive", "run_ppsd_archive"),
    "PPSDArchiveConfig":  ("ppsd_archive", "PPSDArchiveConfig"),
    "ViewerConfig":       ("config",       "ViewerConfig"),
    "MonitorConfig":      ("monitor",      "MonitorConfig"),
    "StaleWatcher":       ("monitor",      "StaleWatcher"),
    "run_web":            ("web",          "run_web"),
    "WebConfig":          ("web",          "WebConfig"),
    "THEMES":             ("config",       "THEMES"),
    "FILTERS":            ("config",       "FILTERS"),
    "PICKER_PRESETS":     ("picker",       "PICKER_PRESETS"),
}


def __getattr__(name):
    try:
        submodule, attr = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
    from importlib import import_module
    mod = import_module(f".{submodule}", __name__)
    value = getattr(mod, attr)
    # Cache on the package module so subsequent accesses skip the lookup.
    globals()[name] = value
    return value


def __dir__():
    # Make ``dir(seedlink_py_utils)`` surface the lazy attributes.
    return sorted(list(globals().keys()) + list(_LAZY_ATTRS.keys()))
