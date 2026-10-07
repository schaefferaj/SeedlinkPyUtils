"""Configuration objects and presets for the real-time viewer."""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# Colour palettes for the matplotlib tools (viewer, mc-viewer, PPSD).
#
# Two independent axes: a *palette* (hue family) and a *mode* (light or
# dark). Every palette defines both modes, so `--palette warm --dark-mode`
# composes. Resolve with `resolve_theme()` rather than indexing directly —
# it validates the palette name and raises a useful error.
#
# Keys, all required in every variant:
#   bg          figure + axes ground (also tints the Tk window chrome)
#   fg          spines, ticks, axis labels, panel titles, NSLC captions
#   trace       the waveform itself — every mc-viewer panel shares it
#   grid        gridlines, paired with grid_alpha
#   grid_alpha  gridline alpha
#   accent      CFT line, active radio button, PPSD coverage strip
#   pick        STA/LTA trigger markers + the trigger-on threshold line
#   thresh_off  the trigger-off threshold line in the CFT strip
#   label_bg    ground of the NSLC tab drawn over each mc-viewer panel
#   label_fg    text colour of that tab
#
# `label_bg`/`label_fg` are a pair: the tab sits *on* the panel ground, so
# label_bg should read as a tint of that ground rather than a fourth
# colour, and label_fg must clear it comfortably. `contrast` inverts the
# ground outright, which is the point of that palette.
#
# `pick` and `thresh_off` must contrast with `trace`, not merely with `bg`:
# picks are drawn *on top of* the waveform. That's why the warm palettes
# flip them cool while everything else keeps them warm — a red pick marker
# disappears into a sepia or amber trace.
PALETTES = {
    # The historical light/dark pair, values unchanged. The matplotlib
    # shorthand is retained deliberately ("0.35" is the same grey as
    # "#595959", "C0" the same blue as "#1f77b4") so that rendering stays
    # bit-for-bit what it was before the palette axis existed.
    "neutral": {
        "light": {
            "bg":         "white",
            "fg":         "black",
            "trace":      "0.35",
            "grid":       "0.7",
            "grid_alpha": 0.4,
            "accent":     "C0",
            "pick":       "#e53935",
            "thresh_off": "#f9a825",
            "label_bg":   "#d6e8f5",
            "label_fg":   "#10303f",
        },
        "dark": {
            "bg":         "#1a1a1a",
            "fg":         "#e8e8e8",
            "trace":      "#cfcfcf",
            "grid":       "#555555",
            "grid_alpha": 0.5,
            "accent":     "#4fc3f7",
            "pick":       "#e53935",
            "thresh_off": "#f9a825",
            "label_bg":   "#2b3a45",
            "label_fg":   "#cfe6f5",
        },
    },
    # Sepia ink on aged paper / amber on ember — deliberately close to a
    # paper drum recorder. Picks go cool for contrast against the trace.
    "warm": {
        "light": {
            "bg":         "#fbf4e9",
            "fg":         "#33261c",
            "trace":      "#7a4f28",
            "grid":       "#dcc8a8",
            "grid_alpha": 0.45,
            "accent":     "#c05621",
            "pick":       "#1d6f8a",
            "thresh_off": "#2f8f4e",
            "label_bg":   "#f0dcc0",
            "label_fg":   "#3d2a18",
        },
        "dark": {
            "bg":         "#1b1410",
            "fg":         "#f2e3d0",
            "trace":      "#e8b06a",
            "grid":       "#4a382a",
            "grid_alpha": 0.5,
            "accent":     "#ff9d3c",
            "pick":       "#4fb3d9",
            "thresh_off": "#7fd4a0",
            "label_bg":   "#3a2a1c",
            "label_fg":   "#f5dfc0",
        },
    },
    # Steel blue on ice / pale blue on navy. The accent carries more
    # chroma than the trace so the CFT strip doesn't read as another
    # waveform, and the warm pick marker works here untouched.
    "cold": {
        "light": {
            "bg":         "#f3f7fa",
            "fg":         "#10212e",
            "trace":      "#2e5f80",
            "grid":       "#b9cfdd",
            "grid_alpha": 0.45,
            "accent":     "#0091d5",
            "pick":       "#d1341f",
            "thresh_off": "#e8a33d",
            "label_bg":   "#d4e6f2",
            "label_fg":   "#0d2534",
        },
        "dark": {
            "bg":         "#0d1822",
            "fg":         "#dbe9f2",
            "trace":      "#8ecae6",
            "grid":       "#294559",
            "grid_alpha": 0.5,
            "accent":     "#56cfe1",
            "pick":       "#ff6b5b",
            "thresh_off": "#ffc857",
            "label_bg":   "#1b3346",
            "label_fg":   "#cfe8f8",
        },
    },
    # A green middle ground for people who find warm too yellow and cold
    # too clinical. Lowest-chroma trace of the set, so it's the most
    # forgiving for watching a quiet station for an hour.
    "sage": {
        "light": {
            "bg":         "#f2f6f1",
            "fg":         "#1b2a1e",
            "trace":      "#3f6245",
            "grid":       "#c2d4c2",
            "grid_alpha": 0.45,
            "accent":     "#2f8f4e",
            "pick":       "#c0392b",
            "thresh_off": "#d98324",
            "label_bg":   "#d7e8d8",
            "label_fg":   "#1b2e1f",
        },
        "dark": {
            "bg":         "#101811",
            "fg":         "#dfeadf",
            "trace":      "#9ec9a4",
            "grid":       "#2b4130",
            "grid_alpha": 0.5,
            "accent":     "#5fd37f",
            "pick":       "#ff6b5b",
            "thresh_off": "#ffc857",
            "label_bg":   "#1e3324",
            "label_fg":   "#d8ecd9",
        },
    },
    # Maximum separation for a projector in a lit room, a poor laptop
    # panel, or colour-vision deficiency. The grid is pushed brighter than
    # elsewhere because it competes with ambient glare.
    "contrast": {
        "light": {
            "bg":         "#ffffff",
            "fg":         "#000000",
            "trace":      "#000000",
            "grid":       "#7a7a7a",
            "grid_alpha": 0.5,
            "accent":     "#0000c8",
            "pick":       "#d40000",
            "thresh_off": "#006e00",
            "label_bg":   "#000000",
            "label_fg":   "#ffffff",
        },
        "dark": {
            "bg":         "#000000",
            "fg":         "#ffffff",
            "trace":      "#ffffff",
            "grid":       "#6e6e6e",
            "grid_alpha": 0.55,
            "accent":     "#ffd600",
            "pick":       "#ff4d4d",
            "thresh_off": "#4dffa6",
            "label_bg":   "#ffffff",
            "label_fg":   "#000000",
        },
    },
    # Greyscale for figures that end up in a report or a PDF, where a hue
    # survives neither a photocopier nor a reviewer. Faintest grid in the
    # set, and the pick marker leans on linestyle rather than colour. This
    # is the palette the PPSD archiver should write — see
    # ppsd_archive._render_bucket_png, which forces the light variant.
    "print": {
        "light": {
            "bg":         "#ffffff",
            "fg":         "#1a1a1a",
            "trace":      "#262626",
            "grid":       "#a8a8a8",
            "grid_alpha": 0.35,
            "accent":     "#4d4d4d",
            "pick":       "#000000",
            "thresh_off": "#737373",
            "label_bg":   "#e6e6e6",
            "label_fg":   "#1a1a1a",
        },
        "dark": {
            "bg":         "#0a0a0a",
            "fg":         "#f5f5f5",
            "trace":      "#f0f0f0",
            "grid":       "#5a5a5a",
            "grid_alpha": 0.45,
            "accent":     "#bdbdbd",
            "pick":       "#ffffff",
            "thresh_off": "#8c8c8c",
            "label_bg":   "#2e2e2e",
            "label_fg":   "#f0f0f0",
        },
    },
}

DEFAULT_PALETTE = "neutral"

# Backward-compatible alias. ``THEMES`` predates the palette axis and is
# exported in ``__init__.__all__``, so ``THEMES["light"]`` /
# ``THEMES["dark"]`` must keep resolving for external callers.
THEMES = PALETTES[DEFAULT_PALETTE]


def resolve_theme(palette: str = DEFAULT_PALETTE, dark: bool = False) -> dict:
    """Return one theme dict for ``palette`` in light or dark mode.

    Parameters
    ----------
    palette : str
        Key into :data:`PALETTES` (``"neutral"``, ``"warm"``, ``"cold"``,
        ``"sage"``, ``"contrast"``, ``"print"``).
    dark : bool
        Select the dark variant instead of the light one.

    Raises
    ------
    ValueError
        If ``palette`` is not a known palette name.
    """
    try:
        variants = PALETTES[palette]
    except KeyError:
        raise ValueError(
            f"unknown palette {palette!r}; choose from "
            f"{', '.join(sorted(PALETTES))}"
        ) from None
    return variants["dark" if dark else "light"]

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
    seedlink_server: str = "rtserve.iris.washington.edu:18000"
    fdsn_server: Optional[str] = "https://service.earthscope.org"
    inventory_path: Optional[str] = None
    no_cache: bool = False

    buffer_seconds: int = 300
    redraw_ms: int = 1000

    nperseg: int = 512
    noverlap: int = 400
    fmin: float = 0.5
    fmax: float = 50.0
    db_clip: Tuple[float, float] = (-180.0, -100.0)
    # Was db_clip explicitly supplied by the user? If not, the viewer
    # auto-switches to counts-appropriate clip values when no inventory is
    # available (so the spectrogram doesn't saturate to a single colour).
    db_clip_set: bool = False
    cmap: str = "magma"

    water_level: float = 60.0
    pre_filt: Tuple[float, float, float, float] = (0.05, 0.1, 45.0, 50.0)

    fullscreen: bool = False
    # Appearance is two axes: `palette` picks the hue family, `dark_mode`
    # picks the variant within it. Resolve both at once via
    # config.resolve_theme(cfg.palette, cfg.dark_mode).
    palette: str = DEFAULT_PALETTE
    dark_mode: bool = False
    no_clock: bool = False

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

    # Multi-channel viewer: upper bound on how many channels matching the
    # NSLC pattern to display as stacked panels. Unused by the single-channel
    # viewer. Default 3 matches the typical 3-component station layout.
    max_channels: int = 3

    # Multi-channel viewer: explicit list of NSLC tuples for the mc-viewer
    # when users subscribe to multiple stations (e.g. all-verticals from a
    # selection). Each element is (net, sta, loc, cha). LOC/CHA may contain
    # SeedLink wildcards; NET/STA wildcards should be pre-expanded via
    # info.expand_stream_wildcards before being placed here. Unused by the
    # single-channel viewer.
    nslcs: List[Tuple[str, str, str, str]] = field(default_factory=list)
    # Cap on the total number of panels the mc-viewer will draw. If
    # `nslcs` has more entries than this, the mc-viewer truncates with a
    # warning.
    max_panels: int = 8

    def __post_init__(self):
        if self.noverlap >= self.nperseg:
            self.noverlap = max(0, self.nperseg - 1)
