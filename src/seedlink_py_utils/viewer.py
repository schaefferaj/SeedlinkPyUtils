"""Main real-time viewer: wires the buffer, processing, and GUI together."""

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from obspy import UTCDateTime
from scipy.signal import spectrogram

from .buffer import TraceBuffer, start_seedlink_worker
from .config import FILTERS, THEMES, ViewerConfig
from .gui import (
    HRadioButtons,
    apply_theme_to_axes,
    go_fullscreen,
    set_tk_window_bg,
)
from .processing import apply_filter, load_inventory, remove_response_safe


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

    tracebuf = TraceBuffer(cfg.buffer_seconds)
    start_seedlink_worker(cfg.seedlink_server, cfg.nslc, tracebuf)

    current_filter = {"name": "None"}

    fig = plt.figure(figsize=(14, 7.5), facecolor=theme["bg"])
    gs = fig.add_gridspec(
        3, 1, height_ratios=[0.08, 1, 1.3],
        hspace=0.08,
        left=0.06, right=0.99, top=0.96, bottom=0.07,
    )
    ax_radio = fig.add_subplot(gs[0, 0])
    ax_wf = fig.add_subplot(gs[1, 0])
    ax_sp = fig.add_subplot(gs[2, 0], sharex=ax_wf)

    # Radio strip
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

    def on_filter_change(label):
        current_filter["name"] = label
        print(f"Filter -> {label}")

    radio.on_clicked(on_filter_change)

    # Waveform panel
    (line,) = ax_wf.plot([], [], lw=0.5, color=theme["trace"])
    units = "m/s" if inventory is not None else "counts"
    ax_wf.set_ylabel(units)
    loc_str = loc if loc else "--"
    ax_wf.set_title(
        f"{net}.{sta}.{loc_str}.{cha} — live from {cfg.seedlink_server}",
        fontsize=10,
    )
    ax_wf.grid(True, which="both", linestyle="--",
               alpha=theme["grid_alpha"], color=theme["grid"])
    ax_wf.tick_params(labelbottom=False)
    apply_theme_to_axes(ax_wf, theme)

    # Spectrogram panel
    img = ax_sp.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        extent=(-cfg.buffer_seconds, 0, cfg.fmin, cfg.fmax),
        cmap=cfg.cmap,
        vmin=cfg.db_clip[0],
        vmax=cfg.db_clip[1],
        interpolation="nearest",
    )
    ax_sp.set_ylabel("Frequency (Hz)")
    ax_sp.set_xlabel("Time (s) before now")
    ax_sp.set_xlim(-cfg.buffer_seconds, 0)
    apply_theme_to_axes(ax_sp, theme)

    def update(_frame):
        tr_raw = tracebuf.latest(cha)
        if tr_raw is None or tr_raw.stats.npts < cfg.nperseg:
            return line, img

        tr_vel = remove_response_safe(tr_raw, inventory, cfg)
        now = UTCDateTime()
        fs = tr_vel.stats.sampling_rate

        tr_plot = apply_filter(tr_vel, current_filter["name"])
        data_plot = tr_plot.data.astype(float)
        times = tr_plot.times() + (tr_plot.stats.starttime - now)
        line.set_data(times, data_plot)
        ax_wf.set_xlim(-cfg.buffer_seconds, 0)
        if data_plot.size:
            amp = np.max(np.abs(data_plot))
            if amp > 0:
                ax_wf.set_ylim(-1.1 * amp, 1.1 * amp)

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

        return line, img

    ani = FuncAnimation(
        fig, update, interval=cfg.redraw_ms,
        blit=False, cache_frame_data=False,
    )

    def on_key(event):
        if event.key == "escape":
            plt.close(fig)
    fig.canvas.mpl_connect("key_press_event", on_key)

    fig._radio = radio
    fig._ani = ani

    if cfg.fullscreen:
        plt.show(block=False)
        for _ in range(10):
            plt.pause(0.05)
        set_tk_window_bg(fig, theme["bg"])
        go_fullscreen(fig)
        plt.show()
    else:
        plt.show(block=False)
        plt.pause(0.05)
        set_tk_window_bg(fig, theme["bg"])
        plt.show()
