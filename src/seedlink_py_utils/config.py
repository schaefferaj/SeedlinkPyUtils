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

FILTERS = {
    "None":       None,
    "BP 1–25 Hz": ("bandpass", {"freqmin": 1.0, "freqmax": 25.0, "corners": 4, "zerophase": True}),
    "BP 3–25 Hz": ("bandpass", {"freqmin": 3.0, "freqmax": 25.0, "corners": 4, "zerophase": True}),
    "HP 1 Hz":    ("highpass", {"freq": 1.0, "corners": 4, "zerophase": True}),
    "HP 3 Hz":    ("highpass", {"freq": 3.0, "corners": 4, "zerophase": True}),
    "HP 5 Hz":    ("highpass", {"freq": 5.0, "corners": 4, "zerophase": True}),
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

    def __post_init__(self):
        if self.noverlap >= self.nperseg:
            self.noverlap = max(0, self.nperseg - 1)
