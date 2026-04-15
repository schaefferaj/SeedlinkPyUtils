"""Configuration objects and presets for the real-time viewer."""

from dataclasses import dataclass
from typing import Optional, Tuple


THEMES = {
    "light": {
        "bg":         "white",
        "fg":         "black",
        "trace":      "0.35",
        "grid":       "0.7",
        "grid_alpha": 0.4,
        "accent":     "C0",
    },
    "dark": {
        "bg":         "#1a1a1a",
        "fg":         "#e8e8e8",
        "trace":      "#cfcfcf",
        "grid":       "#555555",
        "grid_alpha": 0.5,
        "accent":     "#4fc3f7",
    },
}

# Presets ordered low-frequency → high-frequency so the radio-button row
# reads left-to-right from teleseismic long-period to local high-freq.
# Names for `surface`, `tele-p`, `regional`, and `local` match the picker
# preset names in picker.py, and each such filter's band matches the
# picker's detection band — so the viewer category and the picker category
# always mean the same thing.
FILTERS = {
    "None":           None,
    "BP 0.02–0.1 Hz": ("bandpass", {"freqmin": 0.02, "freqmax": 0.1,  "corners": 4, "zerophase": True}),
    "BP 0.5–2 Hz":    ("bandpass", {"freqmin": 0.5,  "freqmax": 2.0,  "corners": 4, "zerophase": True}),
    "BP 1–10 Hz":     ("bandpass", {"freqmin": 1.0,  "freqmax": 10.0, "corners": 4, "zerophase": True}),
    "BP 1–25 Hz":     ("bandpass", {"freqmin": 1.0,  "freqmax": 25.0, "corners": 4, "zerophase": True}),
    "BP 2–10 Hz":     ("bandpass", {"freqmin": 2.0,  "freqmax": 10.0, "corners": 4, "zerophase": True}),
    "BP 3–25 Hz":     ("bandpass", {"freqmin": 3.0,  "freqmax": 25.0, "corners": 4, "zerophase": True}),
    "HP 1 Hz":        ("highpass", {"freq": 1.0, "corners": 4, "zerophase": True}),
    "HP 3 Hz":        ("highpass", {"freq": 3.0, "corners": 4, "zerophase": True}),
    "HP 5 Hz":        ("highpass", {"freq": 5.0, "corners": 4, "zerophase": True}),
}

# ASCII, shell-friendly aliases for the CLI's --filter option. Each maps to a
# canonical FILTERS key. Keep in sync with FILTERS when adding presets. The
# `surface`, `tele-p`, `regional`, and `local` aliases line up with the
# picker preset names so one word means one band in both contexts.
FILTER_CLI_ALIASES = {
    "none":     "None",
    "surface":  "BP 0.02–0.1 Hz",
    "tele-p":   "BP 0.5–2 Hz",
    "regional": "BP 1–10 Hz",
    "bp1-25":   "BP 1–25 Hz",
    "local":    "BP 2–10 Hz",
    "bp3-25":   "BP 3–25 Hz",
    "hp1":      "HP 1 Hz",
    "hp3":      "HP 3 Hz",
    "hp5":      "HP 5 Hz",
}


@dataclass
class ViewerConfig:
    """Runtime configuration for the real-time SeedLink viewer."""

    nslc: Tuple[str, str, str, str]
    seedlink_server: str = "seiscomp.hakai.org:18000"
    fdsn_server: Optional[str] = "http://seiscomp.hakai.org/fdsnws"
    inventory_path: Optional[str] = None
    no_cache: bool = False

    buffer_seconds: int = 300
    redraw_ms: int = 1000

    nperseg: int = 512
    noverlap: int = 400
    fmin: float = 0.5
    fmax: float = 50.0
    db_clip: Tuple[float, float] = (-180.0, -100.0)
    cmap: str = "magma"

    water_level: float = 60.0
    pre_filt: Tuple[float, float, float, float] = (0.05, 0.1, 45.0, 50.0)

    fullscreen: bool = False
    dark_mode: bool = False

    # On startup, ask the server to replay buffer_seconds of history so the
    # display opens pre-populated. The server's ring buffer typically covers
    # hours to a day, so this is usually within reach; if not, the backfill
    # is silently partial. Set to False for live-only (empty-at-start) mode.
    backfill_on_start: bool = True

    # When set to a key in FILTERS, the viewer locks the waveform filter to
    # that preset and hides the radio-button strip. When None (default), the
    # viewer shows the radio buttons for interactive switching.
    filter_name: Optional[str] = None

    # STA/LTA picker configuration. When picker_preset is None the picker is
    # disabled and no CFT strip is drawn. When set, picker_{sta,lta,thr_on,
    # thr_off} individually override the preset's values if non-None.
    picker_preset: Optional[str] = None
    picker_sta: Optional[float] = None
    picker_lta: Optional[float] = None
    picker_thr_on: Optional[float] = None
    picker_thr_off: Optional[float] = None

    def __post_init__(self):
        if self.noverlap >= self.nperseg:
            self.noverlap = max(0, self.nperseg - 1)
