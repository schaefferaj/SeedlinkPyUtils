"""STA/LTA event picker for the real-time viewer.

Provides three presets (local / regional / teleseismic), each of which bundles
an STA/LTA window pair, trigger thresholds, and a detection bandpass filter.
The detection filter is intentionally independent of the viewer's display
filter so the picker behaves consistently regardless of what the user is
looking at on the waveform panel.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from obspy import Trace
from obspy.signal.trigger import recursive_sta_lta, trigger_onset


# Each preset: STA, LTA, trigger thresholds, detection filter as an ObsPy
# Trace.filter() (type, kwargs) pair.
# Preset names match one of the --filter aliases in FILTER_CLI_ALIASES, and
# each preset's detection band matches the band of the filter by the same
# name — so e.g. `--picker regional` and `--filter regional` both operate
# on BP 1–10 Hz. This avoids the "regional means two different things"
# confusion from 0.4.0-pre.
PICKER_PRESETS = {
    "local": {
        "sta": 0.5, "lta": 10.0, "thr_on": 3.5, "thr_off": 1.5,
        "filter": ("bandpass", {"freqmin": 2.0, "freqmax": 10.0,
                                "corners": 4, "zerophase": True}),
        "description": "BP 2–10 Hz, STA 0.5 s / LTA 10 s, triggers 3.5 / 1.5",
    },
    "regional": {
        "sta": 2.0, "lta": 30.0, "thr_on": 3.0, "thr_off": 1.5,
        "filter": ("bandpass", {"freqmin": 1.0, "freqmax": 10.0,
                                "corners": 4, "zerophase": True}),
        "description": "BP 1–10 Hz, STA 2 s / LTA 30 s, triggers 3.0 / 1.5",
    },
    "tele-p": {
        "sta": 5.0, "lta": 120.0, "thr_on": 2.5, "thr_off": 1.5,
        "filter": ("bandpass", {"freqmin": 0.5, "freqmax": 2.0,
                                "corners": 4, "zerophase": True}),
        "description": "BP 0.5–2 Hz, STA 5 s / LTA 120 s, triggers 2.5 / 1.5",
    },
}


@dataclass
class PickerConfig:
    sta: float
    lta: float
    thr_on: float
    thr_off: float
    filter_spec: Tuple[str, dict]
    preset_name: Optional[str] = None


def resolve_picker_config(
    preset_name: Optional[str],
    sta: Optional[float] = None,
    lta: Optional[float] = None,
    thr_on: Optional[float] = None,
    thr_off: Optional[float] = None,
) -> Optional[PickerConfig]:
    """Build a PickerConfig from a preset name and optional per-field overrides.

    Returns None if ``preset_name`` is None (picker disabled).
    """
    if preset_name is None:
        return None
    if preset_name not in PICKER_PRESETS:
        raise ValueError(
            f"Unknown picker preset {preset_name!r}. "
            f"Valid: {list(PICKER_PRESETS.keys())}"
        )
    p = PICKER_PRESETS[preset_name]
    return PickerConfig(
        sta=sta if sta is not None else p["sta"],
        lta=lta if lta is not None else p["lta"],
        thr_on=thr_on if thr_on is not None else p["thr_on"],
        thr_off=thr_off if thr_off is not None else p["thr_off"],
        filter_spec=p["filter"],
        preset_name=preset_name,
    )


def compute_cft(tr: Trace, cfg: PickerConfig):
    """Apply the picker's detection filter and compute the STA/LTA CFT.

    Returns
    -------
    cft : ndarray | None
        Characteristic function (same length as the input trace); None if the
        trace is shorter than the LTA window.
    times_s : ndarray | None
        Time axis in seconds, relative to ``tr.stats.starttime``. None when
        cft is None.
    """
    fs = tr.stats.sampling_rate
    nsta = int(round(cfg.sta * fs))
    nlta = int(round(cfg.lta * fs))
    if nlta <= nsta or tr.stats.npts < nlta + 1:
        return None, None

    tr_pick = tr.copy()
    ftype, fkwargs = cfg.filter_spec
    tr_pick.filter(ftype, **fkwargs)

    cft = recursive_sta_lta(tr_pick.data.astype(float), nsta, nlta)
    times_s = tr_pick.times()
    return cft, times_s


def describe_filter_band(filter_spec: Tuple[str, dict]) -> str:
    """Render a short human-readable label for a picker/filter spec — e.g.
    ``("bandpass", {"freqmin": 2, "freqmax": 10})`` → ``"BP 2–10 Hz"``."""
    ftype, fkw = filter_spec
    fmt = lambda x: f"{x:g}"
    if ftype == "bandpass":
        return f"BP {fmt(fkw['freqmin'])}\u2013{fmt(fkw['freqmax'])} Hz"
    if ftype == "highpass":
        return f"HP {fmt(fkw['freq'])} Hz"
    if ftype == "lowpass":
        return f"LP {fmt(fkw['freq'])} Hz"
    return ftype


def find_onsets(cft, times_s, cfg: PickerConfig) -> List[float]:
    """Return onset times (seconds, relative to trace start) where the CFT
    crossed ``thr_on``. Each entry corresponds to one trigger-on event."""
    if cft is None or cft.size == 0:
        return []
    pairs = trigger_onset(cft, cfg.thr_on, cfg.thr_off)
    if len(pairs) == 0:
        return []
    pairs = np.asarray(pairs)
    return [float(times_s[int(on_idx)]) for on_idx in pairs[:, 0]]
