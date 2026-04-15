"""Multi-channel / multi-station real-time SeedLink viewer.

One panel per subscribed NSLC, stacked vertically. Typical use cases:

- One station, three components (``PQ.DAOB..HH?``): stacked Z/N/E panels.
- Vertical-only from a selection of stations: one Z panel per station.
- A mixed list of streams with explicit LOC/CHA.

No spectrogram — the focus is cross-panel visual correlation. Each panel
has its own optional STA/LTA picker (same preset across panels, but
independent pick state so per-station triggers appear in the right place).
"""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from dataclasses import dataclass, field
from matplotlib.animation import FuncAnimation
from obspy import UTCDateTime
from typing import List, Tuple

from .buffer import TraceBuffer, start_seedlink_worker
from .config import FILTER_CLI_ALIASES, FILTERS, THEMES, ViewerConfig
from .gui import (
    HRadioButtons,
    apply_theme_to_axes,
    create_filter_dropdown,
    go_fullscreen,
    set_tk_window_bg,
)
from .picker import (
    compute_cft,
    describe_filter_band,
    find_onsets,
    resolve_picker_config,
)
from .processing import apply_filter, load_inventory_multi, remove_response_safe


_PICK_COLOR = "#e53935"


@dataclass
class _Panel:
    """Per-panel state: the NSLC it represents, its plot artists, and its
    own picker state so multi-station triggers don't interfere."""
    nslc: Tuple[str, str, str, str]
    ax: object
    line: object
    picks: List[UTCDateTime] = field(default_factory=list)
    pick_artists: list = field(default_factory=list)


def _nslc_label(nslc):
    net, sta, loc, cha = nslc
    loc_str = loc if loc else "--"
    return f"{net}.{sta}.{loc_str}.{cha}"


def run_viewer_mc(cfg: ViewerConfig):
    """Launch the multi-channel / multi-station viewer.

    Expects ``cfg.nslcs`` to be populated with the list of streams to draw
    (one panel per NSLC). ``cfg.nslc`` is still the first stream for
    single-station legacy callers.
    """
    streams = list(cfg.nslcs) if cfg.nslcs else [cfg.nslc]
    if not streams:
        raise ValueError("run_viewer_mc: at least one stream required.")

    if len(streams) > cfg.max_panels:
        print(f"Warning: {len(streams)} streams exceeds --max-panels "
              f"({cfg.max_panels}); truncating.")
        streams = streams[:cfg.max_panels]

    theme = THEMES["dark" if cfg.dark_mode else "light"]

    # One combined inventory covers response removal for every station.
    inventory = load_inventory_multi(cfg, streams)

    # Pick the primary stream for the single-stream cfg.nslc slot (used by
    # load_inventory-style paths that only look at the first NSLC). Already
    # handled above via load_inventory_multi, but we keep the slot tidy.
    tracebuf = TraceBuffer(cfg.buffer_seconds)
    start_seedlink_worker(
        cfg.seedlink_server, streams, tracebuf,
        backfill_seconds=cfg.buffer_seconds if cfg.backfill_on_start else 0,
    )

    if cfg.filter_name is not None and cfg.filter_name not in FILTERS:
        raise ValueError(
            f"filter_name={cfg.filter_name!r} is not a known preset. "
            f"Valid names: {list(FILTERS.keys())}"
        )

    picker_cfg = resolve_picker_config(
        cfg.picker_preset,
        sta=cfg.picker_sta, lta=cfg.picker_lta,
        thr_on=cfg.picker_thr_on, thr_off=cfg.picker_thr_off,
    )

    locked_filter = cfg.filter_name
    current_filter = {"name": locked_filter if locked_filter else "None"}
    radio = None
    native_filter_widget = None

    backend = matplotlib.get_backend().lower()
    prefer_native_dropdown = (
        locked_filter is None and ("tk" in backend or "qt" in backend)
    )
    use_radio_row = locked_filter is None and not prefer_native_dropdown

    n_panels = len(streams)
    # Gridspec: optional radio row + n_panels waveform panels.
    rows, ratios = [], []
    if use_radio_row:
        rows.append("radio"); ratios.append(0.08)
    for i in range(n_panels):
        rows.append(f"p{i}"); ratios.append(1.0)

    fig = plt.figure(figsize=(14, 7.5), facecolor=theme["bg"])
    top = 0.96 if use_radio_row else 0.94
    gs = fig.add_gridspec(
        len(rows), 1, height_ratios=ratios,
        hspace=0.08,
        left=0.08, right=0.99, top=top, bottom=0.07,
    )
    axes = {name: fig.add_subplot(gs[i, 0]) for i, name in enumerate(rows)}
    panel_axes = [axes[f"p{i}"] for i in range(n_panels)]
    for ax in panel_axes[1:]:
        ax.sharex(panel_axes[0])

    # --- Filter selection widget -----------------------------------------
    _alias_by_key = {v: k for k, v in FILTER_CLI_ALIASES.items()}
    filter_display_labels = [
        f"{key} ({_alias_by_key[key]})" if key in _alias_by_key else key
        for key in FILTERS.keys()
    ]
    _key_by_display = dict(zip(filter_display_labels, FILTERS.keys()))

    def on_filter_change(label):
        key = _key_by_display.get(label, label)
        current_filter["name"] = key
        print(f"Filter -> {key}")

    if use_radio_row:
        ax_radio = axes["radio"]
        ax_radio.set_facecolor(theme["bg"])
        for spine in ax_radio.spines.values():
            spine.set_visible(False)
        ax_radio.set_xticks([])
        ax_radio.set_yticks([])
        ax_radio.text(-0.005, 0.5, "Filter:", transform=ax_radio.transAxes,
                      fontsize=10, va="center", ha="right", fontweight="bold",
                      color=theme["fg"])
        radio = HRadioButtons(ax_radio, list(FILTERS.keys()), active=0,
                              activecolor=theme["accent"])
        for label in radio.labels:
            label.set_fontsize(9)
            label.set_color(theme["fg"])
        radio.on_clicked(on_filter_change)

    # --- Waveform panels --------------------------------------------------
    units = "m/s" if inventory is not None else "counts"
    panels: List[_Panel] = []
    for i, (ax, nslc) in enumerate(zip(panel_axes, streams)):
        (ln,) = ax.plot([], [], lw=0.5, color=theme["trace"])
        ax.set_ylabel(f"{_nslc_label(nslc)}\n{units}",
                      fontsize=9, color=theme["fg"])
        ax.grid(True, which="both", linestyle="--",
                alpha=theme["grid_alpha"], color=theme["grid"])
        if i < n_panels - 1:
            ax.tick_params(labelbottom=False)
        else:
            ax.set_xlabel("Time (s) before now")
        apply_theme_to_axes(ax, theme)
        panels.append(_Panel(nslc=nslc, ax=ax, line=ln))

    # Header title lives on the top panel. Summarise the subscription:
    # single-station ID, or "N streams on SERVER" otherwise.
    if n_panels == 1:
        header = f"{_nslc_label(streams[0])} — live from {cfg.seedlink_server}"
    else:
        # Show the set compactly: e.g. "AM.RA382..EHZ +3 others"
        header = (f"{_nslc_label(streams[0])} +{n_panels - 1} other"
                  f"{'s' if n_panels - 1 != 1 else ''}"
                  f" — live from {cfg.seedlink_server}")
    if locked_filter:
        header += f"   [filter: {locked_filter}]"
    if picker_cfg:
        band = describe_filter_band(picker_cfg.filter_spec)
        header += f"   [picker: {picker_cfg.preset_name} ({band})]"
    panel_axes[0].set_title(header, fontsize=10, color=theme["fg"])

    def update(_frame):
        now = UTCDateTime()

        for panel in panels:
            net, sta, loc, cha = panel.nslc
            # For LOC/CHA wildcards, fall back to a looser match by
            # (NET, STA) — first trace found is what we draw. Good enough
            # for the common "PQ.DAOB..HH?" case where only one channel
            # per panel usually matches a wildcard when streams are listed
            # one per panel (not HH? within one panel).
            tr_raw = panel_axes and tracebuf.latest_nslc(net, sta, loc, cha)
            if tr_raw is None and ("?" in cha or "*" in cha):
                # Wildcard CHA on this panel: find any trace for this
                # station whose channel matches, first-arrived.
                with tracebuf._lock:
                    sel = tracebuf._stream.select(network=net, station=sta,
                                                  location=loc, channel=cha)
                    tr_raw = sel[0].copy() if len(sel) else None

            if tr_raw is None or tr_raw.stats.npts < 2:
                continue

            tr_vel = remove_response_safe(tr_raw, inventory, cfg)
            tr_plot = apply_filter(tr_vel, current_filter["name"])
            data_plot = tr_plot.data.astype(float)
            times = tr_plot.times() + (tr_plot.stats.starttime - now)
            panel.line.set_data(times, data_plot)
            panel.ax.set_xlim(-cfg.buffer_seconds, 0)
            if data_plot.size:
                amp = np.max(np.abs(data_plot))
                if amp > 0:
                    panel.ax.set_ylim(-1.1 * amp, 1.1 * amp)

            # --- Per-panel picker ----------------------------------------
            if picker_cfg is not None:
                cft, t_rel = compute_cft(tr_vel, picker_cfg)
                if cft is not None and cft.size:
                    new_onset_times = find_onsets(cft, t_rel, picker_cfg)
                    new_utcs = [tr_vel.stats.starttime + t
                                for t in new_onset_times]
                    for onset in new_utcs:
                        if all(abs(onset - p) > 1.0 for p in panel.picks):
                            panel.picks.append(onset)
                    cutoff = now - cfg.buffer_seconds
                    panel.picks[:] = [p for p in panel.picks if p >= cutoff]

                for artist in panel.pick_artists:
                    artist.remove()
                panel.pick_artists.clear()
                for p in panel.picks:
                    x = float(p - now)
                    panel.pick_artists.append(
                        panel.ax.axvline(x, color=_PICK_COLOR,
                                         lw=1.2, alpha=0.75)
                    )

        return tuple(p.line for p in panels)

    ani = FuncAnimation(
        fig, update, interval=cfg.redraw_ms,
        blit=False, cache_frame_data=False,
    )

    def on_key(event):
        if event.key == "escape":
            plt.close(fig)
    fig.canvas.mpl_connect("key_press_event", on_key)

    if radio is not None:
        fig._radio = radio
    fig._ani = ani

    def _install_native_filter_dropdown():
        nonlocal native_filter_widget
        if not prefer_native_dropdown:
            return
        native_filter_widget = create_filter_dropdown(
            fig, filter_display_labels, active=0,
            on_change=on_filter_change, theme=theme,
        )
        if native_filter_widget is None:
            print("Could not create native filter dropdown; restart with "
                  "--filter to lock a preset.")
        else:
            fig._filter_dropdown = native_filter_widget

    if cfg.fullscreen:
        plt.show(block=False)
        for _ in range(10):
            plt.pause(0.05)
        set_tk_window_bg(fig, theme["bg"])
        _install_native_filter_dropdown()
        go_fullscreen(fig)
        plt.show()
    else:
        plt.show(block=False)
        plt.pause(0.05)
        set_tk_window_bg(fig, theme["bg"])
        _install_native_filter_dropdown()
        plt.show()
