"""
Real-time SeedLink trace + spectrogram viewer with filter options.

Usage:
    python rt_viewer.py NET.STA.LOC.CHA [options]

Examples:
    python rt_viewer.py AM.RA382.00.EHZ
    python rt_viewer.py PQ.DAOB..HHZ --fullscreen --dark-mode
    python rt_viewer.py IU.ANMO.00.BHZ --server rtserve.iris.washington.edu:18000 \\
                                        --fdsn https://service.iris.edu \\
                                        --buffer 600 --fmax 20

Requires: obspy, matplotlib, numpy, scipy
"""

import argparse
import os
import sys
import threading

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import RadioButtons
from obspy import Stream, UTCDateTime, read_inventory
from obspy.clients.fdsn import Client as FDSNClient
from obspy.clients.seedlink.easyseedlink import create_client
from scipy.signal import spectrogram


# ---------- Themes ----------
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

# Global state shared between the SeedLink thread and the GUI
buffer_stream = Stream()
buffer_lock = threading.Lock()
current_filter = {"name": "None"}
inventory = None
config = {}  # populated in main() from argparse; read by the worker & callbacks


# ---------- Argument parsing ----------
def parse_nslc(s):
    """Parse a NET.STA.LOC.CHA string. Empty LOC (e.g. 'PQ.DAOB..HHZ') is allowed."""
    parts = s.split(".")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"stream must be NET.STA.LOC.CHA (4 dot-separated fields), got {s!r}"
        )
    net, sta, loc, cha = parts
    if not (net and sta and cha):
        raise argparse.ArgumentTypeError(
            f"NET, STA, and CHA may not be empty in {s!r} (LOC may be empty)"
        )
    return net, sta, loc, cha


def parse_db_clip(s):
    try:
        lo, hi = (float(x) for x in s.split(","))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--db-clip must be 'LO,HI' (e.g. '-180,-100'), got {s!r}"
        )
    if lo >= hi:
        raise argparse.ArgumentTypeError(f"--db-clip LO must be < HI, got {s!r}")
    return (lo, hi)


def parse_pre_filt(s):
    try:
        vals = tuple(float(x) for x in s.split(","))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--pre-filt must be 4 comma-separated floats, got {s!r}"
        )
    if len(vals) != 4:
        raise argparse.ArgumentTypeError(
            f"--pre-filt needs exactly 4 values (f1,f2,f3,f4), got {s!r}"
        )
    return vals


def build_parser():
    p = argparse.ArgumentParser(
        description="Real-time SeedLink trace + spectrogram viewer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("stream", type=parse_nslc,
                   help="Stream in NET.STA.LOC.CHA format (e.g. AM.RA382.00.EHZ or PQ.DAOB..HHZ).")

    # Server / data source
    p.add_argument("--server", "-s", default="seiscomp.hakai.org:18000",
                   help="SeedLink server host:port.")
    p.add_argument("--fdsn", default="http://seiscomp.hakai.org/fdsnws",
                   help="FDSN web-service base URL for response metadata. "
                        "Set to '' to skip response removal (plot counts).")
    p.add_argument("--inventory", default=None,
                   help="Path to a local StationXML file (overrides --fdsn).")
    p.add_argument("--no-cache", action="store_true",
                   help="Do not read or write the on-disk inventory cache.")

    # Buffer / display
    p.add_argument("--buffer", "-b", type=int, default=300,
                   help="Rolling buffer length in seconds.")
    p.add_argument("--redraw-ms", type=int, default=1000,
                   help="Redraw interval in milliseconds.")

    # Spectrogram
    p.add_argument("--nperseg", type=int, default=512,
                   help="FFT window length in samples.")
    p.add_argument("--noverlap", type=int, default=400,
                   help="FFT window overlap in samples.")
    p.add_argument("--fmin", type=float, default=0.5,
                   help="Spectrogram minimum frequency (Hz).")
    p.add_argument("--fmax", type=float, default=50.0,
                   help="Spectrogram maximum frequency (Hz); clipped to Nyquist at runtime.")
    p.add_argument("--db-clip", type=parse_db_clip, default=(-180.0, -100.0),
                   metavar="LO,HI",
                   help="Spectrogram dB colour limits as 'LO,HI'.")
    p.add_argument("--cmap", default="magma",
                   help="Matplotlib colormap for the spectrogram.")

    # Response removal
    p.add_argument("--water-level", type=float, default=60.0,
                   help="Water-level for response deconvolution.")
    p.add_argument("--pre-filt", type=parse_pre_filt, default=(0.05, 0.1, 45.0, 50.0),
                   metavar="F1,F2,F3,F4",
                   help="Pre-filter cosine taper corners for response removal.")

    # Window behaviour
    p.add_argument("--fullscreen", "-f", action="store_true",
                   help="Open fullscreen with no toolbar (press Esc to exit).")
    p.add_argument("--dark-mode", "-d", action="store_true",
                   help="Use a dark colour theme.")

    return p


# ---------- Inventory / data handling ----------
def load_inventory():
    global inventory
    net, sta, loc, cha = config["nslc"]
    cache_path = f"./inv_{net}_{sta}_{cha}.xml"
    try:
        if config["inventory_path"]:
            inventory = read_inventory(config["inventory_path"])
            print(f"Loaded inventory from {config['inventory_path']}")
            return
        if not config["no_cache"] and os.path.exists(cache_path):
            inventory = read_inventory(cache_path)
            print(f"Loaded cached inventory from {cache_path}")
            return
        if config["fdsn_server"]:
            print(f"Fetching response for {net}.{sta}.{loc}.{cha} from {config['fdsn_server']}...")
            fdsn = FDSNClient(config["fdsn_server"])
            inventory = fdsn.get_stations(
                network=net, station=sta, location=loc, channel=cha,
                level="response",
            )
            if not config["no_cache"]:
                inventory.write(cache_path, format="STATIONXML")
                print(f"Fetched and cached inventory to {cache_path}")
            else:
                print("Fetched inventory (cache disabled).")
        else:
            print("No inventory configured — plotting raw counts.")
    except Exception as e:
        print(f"Could not load inventory ({e}). Falling back to raw counts.")
        inventory = None


def on_data(trace):
    global buffer_stream
    with buffer_lock:
        buffer_stream += trace
        buffer_stream.merge(method=1, fill_value=0)
        cutoff = UTCDateTime() - config["buffer_seconds"]
        buffer_stream.trim(starttime=cutoff)


def seedlink_worker():
    net, sta, _loc, cha = config["nslc"]
    client = create_client(config["seedlink_server"], on_data=on_data)
    client.select_stream(net, sta, cha)
    client.run()


def remove_response_safe(tr):
    tr = tr.copy()
    tr.detrend("demean")
    if inventory is not None:
        try:
            tr.remove_response(
                inventory=inventory, output="VEL",
                pre_filt=config["pre_filt"], water_level=config["water_level"],
                taper=True, taper_fraction=0.05,
            )
        except Exception as e:
            print(f"Response removal failed: {e}")
    return tr


def apply_filter(tr):
    flt = FILTERS[current_filter["name"]]
    if flt is None:
        return tr
    tr = tr.copy()
    kind, kwargs = flt
    tr.filter(kind, **kwargs)
    return tr


# ---------- Horizontal radio buttons ----------
class HRadioButtons(RadioButtons):
    """RadioButtons laid out horizontally; compatible with matplotlib <3.7 and >=3.7."""

    def __init__(self, ax, labels, active=0, activecolor="C0"):
        super().__init__(ax, labels, active=active, activecolor=activecolor)
        self._relayout_horizontal()

    def _relayout_horizontal(self):
        n = len(self.labels)
        positions = [(i + 0.5) / n for i in range(n)]

        for i, label in enumerate(self.labels):
            label.set_position((positions[i] + 0.005, 0.5))
            label.set_horizontalalignment("left")
            label.set_verticalalignment("center")

        if hasattr(self, "_buttons"):
            offsets = np.array([[p - 0.03, 0.5] for p in positions])
            self._buttons.set_offsets(offsets)
            self._buttons.set_sizes([120] * n)
        elif hasattr(self, "circles"):
            for i, circle in enumerate(self.circles):
                circle.set_center((positions[i] - 0.03, 0.5))
                circle.set_radius(0.025)
        else:
            print("Warning: unknown RadioButtons internals; layout may be off.")


# ---------- Theming ----------
def apply_theme_to_axes(ax, theme):
    ax.set_facecolor(theme["bg"])
    for spine in ax.spines.values():
        spine.set_color(theme["fg"])
    ax.tick_params(colors=theme["fg"], which="both")
    ax.xaxis.label.set_color(theme["fg"])
    ax.yaxis.label.set_color(theme["fg"])
    if ax.get_title():
        ax.title.set_color(theme["fg"])


# ---------- Fullscreen (TkAgg-targeted, with fallbacks) ----------
def go_fullscreen(fig):
    backend = matplotlib.get_backend().lower()
    mgr = fig.canvas.manager

    for hide_attempt in (
        lambda: fig.canvas.toolbar.pack_forget(),
        lambda: fig.canvas.toolbar.setVisible(False),
        lambda: mgr.toolbar.Hide(),
    ):
        try:
            hide_attempt()
        except Exception:
            pass

    if "tk" in backend:
        w = mgr.window
        w.update_idletasks()
        w.deiconify()
        w.update()

        def _make_fullscreen():
            try:
                w.attributes("-fullscreen", True)
                w.update()
                if not bool(w.attributes("-fullscreen")):
                    raise RuntimeError("WM ignored -fullscreen")
            except Exception as e:
                print(f"Tk -fullscreen failed ({e}); falling back to overrideredirect")
                try:
                    w.overrideredirect(True)
                    sw = w.winfo_screenwidth()
                    sh = w.winfo_screenheight()
                    w.geometry(f"{sw}x{sh}+0+0")
                    w.update()
                except Exception as e2:
                    print(f"overrideredirect fallback failed: {e2}")

        _make_fullscreen()
        w.after(100, _make_fullscreen)
        w.after(500, _make_fullscreen)
        return

    try:
        if "qt" in backend:
            mgr.window.showFullScreen()
        elif "wx" in backend:
            mgr.frame.ShowFullScreen(True)
        elif "gtk" in backend:
            mgr.window.fullscreen()
        elif "macosx" in backend:
            mgr.full_screen_toggle()
        else:
            mgr.full_screen_toggle()
    except Exception as e:
        print(f"Could not enter fullscreen on backend '{backend}': {e}")


def set_tk_window_bg(fig, color):
    try:
        fig.canvas.manager.window.configure(bg=color)
    except Exception:
        pass


# ---------- Main ----------
def main():
    args = build_parser().parse_args()

    net, sta, loc, cha = args.stream
    config.update({
        "nslc":             (net, sta, loc, cha),
        "seedlink_server":  args.server,
        "fdsn_server":      args.fdsn if args.fdsn else None,
        "inventory_path":   args.inventory,
        "no_cache":         args.no_cache,
        "buffer_seconds":   args.buffer,
        "redraw_ms":        args.redraw_ms,
        "nperseg":          args.nperseg,
        "noverlap":         args.noverlap,
        "fmin":             args.fmin,
        "fmax":             args.fmax,
        "db_clip":          args.db_clip,
        "cmap":             args.cmap,
        "water_level":      args.water_level,
        "pre_filt":         args.pre_filt,
    })

    # Sanity: noverlap must be < nperseg
    if config["noverlap"] >= config["nperseg"]:
        print(f"Warning: --noverlap ({config['noverlap']}) >= --nperseg "
              f"({config['nperseg']}); clamping.")
        config["noverlap"] = max(0, config["nperseg"] - 1)

    theme = THEMES["dark" if args.dark_mode else "light"]

    load_inventory()

    t = threading.Thread(target=seedlink_worker, daemon=True)
    t.start()

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

    # Waveform
    (line,) = ax_wf.plot([], [], lw=0.5, color=theme["trace"])
    units = "m/s" if inventory is not None else "counts"
    ax_wf.set_ylabel(units)
    loc_str = loc if loc else "--"
    ax_wf.set_title(
        f"{net}.{sta}.{loc_str}.{cha} — live from {config['seedlink_server']}",
        fontsize=10,
    )
    ax_wf.grid(True, which="both", linestyle="--",
               alpha=theme["grid_alpha"], color=theme["grid"])
    ax_wf.tick_params(labelbottom=False)
    apply_theme_to_axes(ax_wf, theme)

    # Spectrogram
    img = ax_sp.imshow(
        np.zeros((2, 2)),
        origin="lower",
        aspect="auto",
        extent=(-config["buffer_seconds"], 0, config["fmin"], config["fmax"]),
        cmap=config["cmap"],
        vmin=config["db_clip"][0],
        vmax=config["db_clip"][1],
        interpolation="nearest",
    )
    ax_sp.set_ylabel("Frequency (Hz)")
    ax_sp.set_xlabel("Time (s) before now")
    ax_sp.set_xlim(-config["buffer_seconds"], 0)
    apply_theme_to_axes(ax_sp, theme)

    def update(_frame):
        with buffer_lock:
            if len(buffer_stream) == 0:
                return line, img
            tr_raw = buffer_stream.select(channel=cha)[0].copy()

        if tr_raw.stats.npts < config["nperseg"]:
            return line, img

        tr_vel = remove_response_safe(tr_raw)
        now = UTCDateTime()
        fs = tr_vel.stats.sampling_rate

        # Waveform (filtered)
        tr_plot = apply_filter(tr_vel)
        data_plot = tr_plot.data.astype(float)
        times = tr_plot.times() + (tr_plot.stats.starttime - now)
        line.set_data(times, data_plot)
        ax_wf.set_xlim(-config["buffer_seconds"], 0)
        if data_plot.size:
            amp = np.max(np.abs(data_plot))
            if amp > 0:
                ax_wf.set_ylim(-1.1 * amp, 1.1 * amp)

        # Spectrogram (unfiltered)
        data_spec = tr_vel.data.astype(float)
        f, t_spec, Sxx = spectrogram(
            data_spec, fs=fs,
            nperseg=config["nperseg"],
            noverlap=min(config["noverlap"], config["nperseg"] - 1),
            scaling="density",
            mode="psd",
        )
        fmax = min(config["fmax"], fs / 2)
        fmask = (f >= config["fmin"]) & (f <= fmax)
        f = f[fmask]
        Sxx = Sxx[fmask, :]

        Sxx_db = 10.0 * np.log10(Sxx + 1e-30)

        t0_offset = float(tr_vel.stats.starttime - now)
        t_plot_arr = t_spec + t0_offset

        img.set_data(Sxx_db)
        img.set_extent((t_plot_arr[0], t_plot_arr[-1], f[0], f[-1]))
        ax_sp.set_ylim(f[0], f[-1])

        return line, img

    _ani = FuncAnimation(
        fig, update, interval=config["redraw_ms"],
        blit=False, cache_frame_data=False,
    )

    def on_key(event):
        if event.key == "escape":
            plt.close(fig)
    fig.canvas.mpl_connect("key_press_event", on_key)

    fig._radio = radio
    fig._ani = _ani

    if args.fullscreen:
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


if __name__ == "__main__":
    main()
