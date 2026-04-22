"""Main real-time viewer: wires the buffer, processing, and GUI together."""

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from obspy import UTCDateTime
from scipy.signal import spectrogram

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
from .processing import apply_filter, load_inventory, remove_response_safe


# Colour for pick markers and the "trigger on" threshold line — deliberately
# a saturated red that stands out against both light-theme (white/grey) and
# dark-theme (near-black/grey) backgrounds.
_PICK_COLOR = "#e53935"
_THRESH_OFF_COLOR = "#f9a825"


def run_viewer(cfg: ViewerConfig):
    """Launch the real-time SeedLink trace + spectrogram viewer.

    Parameters
    ----------
    cfg : ViewerConfig
        Runtime configuration. See :class:`seedlink_py_utils.config.ViewerConfig`.
    """
    net, sta, loc, cha = cfg.nslc
    theme = THEMES["dark" if cfg.dark_mode else "light"]

    inventory = load_inventory(cfg)

    # Pick a spectrogram dB clip range appropriate to the data units. The
    # default (-180, -100) is tuned for (m/s)²/Hz after response removal;
    # in raw counts the power is ~8 orders of magnitude higher and the
    # spectrogram saturates to a single colour. Switch to counts-friendly
    # defaults when no inventory is available and the user hasn't
    # overridden --db-clip explicitly.
    db_clip = cfg.db_clip
    if inventory is None and not cfg.db_clip_set:
        db_clip = (0.0, 60.0)
        print(f"No inventory available: using counts spectrogram clip "
              f"{db_clip} instead of the m/s default {cfg.db_clip}. "
              "Pass --db-clip to override.")

    tracebuf = TraceBuffer(cfg.buffer_seconds, no_clock=cfg.no_clock)
    start_seedlink_worker(
        cfg.seedlink_server, [cfg.nslc], tracebuf,
        backfill_seconds=cfg.buffer_seconds if cfg.backfill_on_start else 0,
        no_clock=cfg.no_clock,
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

    # On TkAgg and QtAgg we put the filter selector into a native dropdown
    # packed above the canvas, leaving the figure area entirely to the data
    # panels. On other backends we fall back to the legacy in-figure radio
    # strip, which needs its own gridspec row.
    backend = matplotlib.get_backend().lower()
    prefer_native_dropdown = (
        locked_filter is None and ("tk" in backend or "qt" in backend)
    )
    use_radio_row = locked_filter is None and not prefer_native_dropdown

    # Build the gridspec dynamically. Rows from top to bottom:
    #   radio strip  (only when interactive filter and Tk dropdown unavailable)
    #   CFT strip    (only when picker is active)
    #   waveform
    #   spectrogram
    rows, ratios = [], []
    if use_radio_row:
        rows.append("radio"); ratios.append(0.08)
    if picker_cfg is not None:
        rows.append("cft"); ratios.append(0.35)
    rows.append("wf"); ratios.append(1.0)
    rows.append("sp"); ratios.append(1.3)

    fig = plt.figure(figsize=(14, 7.5), facecolor=theme["bg"])
    top = 0.96 if use_radio_row else 0.94
    gs = fig.add_gridspec(
        len(rows), 1, height_ratios=ratios,
        hspace=0.08,
        left=0.06, right=0.99, top=top, bottom=0.07,
    )
    axes = {name: fig.add_subplot(gs[i, 0]) for i, name in enumerate(rows)}
    ax_wf = axes["wf"]
    ax_sp = axes["sp"]
    # Share x with waveform so zoom/pan stays aligned
    ax_sp.sharex(ax_wf)
    ax_cft = axes.get("cft")
    if ax_cft is not None:
        ax_cft.sharex(ax_wf)

    # --- Filter selection widget (interactive mode) ---
    # Dropdown labels append the CLI alias in brackets so users can discover
    # the shorthand they'd pass to --filter (e.g. "BP 1–25 Hz (bp1-25)").
    _alias_by_key = {v: k for k, v in FILTER_CLI_ALIASES.items()}
    filter_display_labels = [
        f"{key} ({_alias_by_key[key]})" if key in _alias_by_key else key
        for key in FILTERS.keys()
    ]
    _key_by_display = dict(zip(filter_display_labels, FILTERS.keys()))

    def on_filter_change(label):
        # Accept both the decorated dropdown label and the raw FILTERS key so
        # the radio-strip fallback (which shows raw keys) keeps working.
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
    # The native-dropdown path is handled after plt.show() below, once the
    # Tk/Qt window actually exists.

    # --- CFT strip (STA/LTA picker) ---
    cft_line = None
    thr_on_line = None
    thr_off_line = None
    if ax_cft is not None:
        ax_cft.set_facecolor(theme["bg"])
        (cft_line,) = ax_cft.plot([], [], lw=0.8, color=theme["accent"])
        thr_on_line = ax_cft.axhline(
            picker_cfg.thr_on, color=_PICK_COLOR, lw=0.8,
            linestyle="--", alpha=0.9,
        )
        thr_off_line = ax_cft.axhline(
            picker_cfg.thr_off, color=_THRESH_OFF_COLOR, lw=0.8,
            linestyle=":", alpha=0.9,
        )
        ax_cft.set_ylabel("STA/LTA", fontsize=8)
        ax_cft.tick_params(axis="y", labelsize=8)
        ax_cft.tick_params(labelbottom=False)
        ax_cft.grid(True, which="both", linestyle="--",
                    alpha=theme["grid_alpha"], color=theme["grid"])
        apply_theme_to_axes(ax_cft, theme)
        ax_cft.set_xlim(-cfg.buffer_seconds, 0)
        ax_cft.set_ylim(0, picker_cfg.thr_on * 1.5)

    # --- Waveform panel ---
    (line,) = ax_wf.plot([], [], lw=0.5, color=theme["trace"])
    units = "m/s" if inventory is not None else "counts"
    ax_wf.set_ylabel(units)
    loc_str = loc if loc else "--"
    title = f"{net}.{sta}.{loc_str}.{cha} — live from {cfg.seedlink_server}"
    if locked_filter:
        title += f"   [filter: {locked_filter}]"
    if picker_cfg:
        band = describe_filter_band(picker_cfg.filter_spec)
        title += f"   [picker: {picker_cfg.preset_name} ({band})]"
    # Put the title above the CFT strip when present, so it sits at the top
    # of the plotting area instead of squeezed between the CFT and waveform.
    # Colour is set explicitly because apply_theme_to_axes only paints the
    # title colour if the axes already has a title at the time it's called,
    # and for ax_cft we theme the axes before setting the title.
    title_ax = ax_cft if ax_cft is not None else ax_wf
    title_ax.set_title(title, fontsize=10, color=theme["fg"])
    ax_wf.grid(True, which="both", linestyle="--",
               alpha=theme["grid_alpha"], color=theme["grid"])
    ax_wf.tick_params(labelbottom=False)
    apply_theme_to_axes(ax_wf, theme)

    # Persistent pick markers (UTCDateTime timestamps) and the axvline
    # artists currently rendered for them. We rebuild the artist list every
    # tick because each pick's x position drifts as "now" advances.
    picks: list = []
    pick_artists: list = []

    # --- Spectrogram panel ---
    img = ax_sp.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        extent=(-cfg.buffer_seconds, 0, cfg.fmin, cfg.fmax),
        cmap=cfg.cmap,
        vmin=db_clip[0],
        vmax=db_clip[1],
        interpolation="nearest",
    )
    ax_sp.set_ylabel("Frequency (Hz)")
    ax_sp.set_xlabel("Time (s) before now")
    ax_sp.set_xlim(-cfg.buffer_seconds, 0)
    apply_theme_to_axes(ax_sp, theme)

    _update_count = [0]

    def update(_frame):
        import time as _time
        _t0 = _time.perf_counter()

        tr_raw = tracebuf.latest(cha)
        if tr_raw is None or tr_raw.stats.npts < cfg.nperseg:
            return line, img
        _t1 = _time.perf_counter()

        tr_vel = remove_response_safe(tr_raw, inventory, cfg)
        _t2 = _time.perf_counter()

        now = tr_vel.stats.endtime if cfg.no_clock else UTCDateTime()
        fs = tr_vel.stats.sampling_rate

        tr_plot = apply_filter(tr_vel, current_filter["name"])
        data_plot = tr_plot.data.astype(float)
        times = tr_plot.times() + (tr_plot.stats.starttime - now)
        _t3 = _time.perf_counter()

        line.set_data(times, data_plot)
        ax_wf.set_xlim(-cfg.buffer_seconds, 0)
        if data_plot.size:
            amp = np.max(np.abs(data_plot))
            if amp > 0:
                ax_wf.set_ylim(-1.1 * amp, 1.1 * amp)

        # --- STA/LTA picker ---
        if picker_cfg is not None:
            cft, t_rel = compute_cft(tr_vel, picker_cfg)
            if cft is not None and cft.size:
                t0_offset = float(tr_vel.stats.starttime - now)
                cft_times = t_rel + t0_offset
                cft_line.set_data(cft_times, cft)
                ax_cft.set_xlim(-cfg.buffer_seconds, 0)
                ax_cft.set_ylim(0, max(
                    float(np.max(cft)) * 1.1, picker_cfg.thr_on * 1.5
                ))

                # New onsets: dedupe against existing picks within 1 s.
                new_onset_times = find_onsets(cft, t_rel, picker_cfg)
                new_utcs = [tr_vel.stats.starttime + t for t in new_onset_times]
                for onset in new_utcs:
                    if all(abs(onset - p) > 1.0 for p in picks):
                        picks.append(onset)

                # Prune picks that have scrolled off the buffer.
                cutoff = now - cfg.buffer_seconds
                picks[:] = [p for p in picks if p >= cutoff]

            # Repaint pick markers on both the waveform and the CFT strip.
            # Cheap for small N; revisit if we ever see hundreds of picks in
            # the buffer.
            for artist in pick_artists:
                artist.remove()
            pick_artists.clear()
            for p in picks:
                x = float(p - now)
                pick_artists.append(
                    ax_wf.axvline(x, color=_PICK_COLOR, lw=1.2, alpha=0.75)
                )
                pick_artists.append(
                    ax_cft.axvline(x, color=_PICK_COLOR, lw=1.0, alpha=0.6)
                )

        # --- Spectrogram (unfiltered response-removed trace) ---
        data_spec = tr_vel.data.astype(float)
        f, t_spec, Sxx = spectrogram(
            data_spec, fs=fs,
            nperseg=cfg.nperseg,
            noverlap=min(cfg.noverlap, cfg.nperseg - 1),
            scaling="density",
            mode="psd",
        )
        fmax = min(cfg.fmax, fs / 2)
        fmask = (f >= cfg.fmin) & (f <= fmax)
        f = f[fmask]
        Sxx = Sxx[fmask, :]

        Sxx_db = 10.0 * np.log10(Sxx + 1e-30)

        t0_offset = float(tr_vel.stats.starttime - now)
        t_plot_arr = t_spec + t0_offset

        img.set_data(Sxx_db)
        img.set_extent((t_plot_arr[0], t_plot_arr[-1], f[0], f[-1]))
        ax_sp.set_ylim(f[0], f[-1])
        _t4 = _time.perf_counter()

        _update_count[0] += 1
        if _update_count[0] % 10 == 0:
            print(f"[tick {_update_count[0]}] "
                  f"buf={_t1-_t0:.3f}  resp={_t2-_t1:.3f}  "
                  f"filt={_t3-_t2:.3f}  spec+draw={_t4-_t3:.3f}  "
                  f"total={_t4-_t0:.3f}  npts={tr_raw.stats.npts}")

        return line, img

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
        """After the Tk/Qt window exists, install the dropdown (or warn and
        continue with no filter selector if it fails)."""
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
