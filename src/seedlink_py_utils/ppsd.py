"""Real-time Probabilistic Power Spectral Density (PPSD) monitor.

Feeds a live SeedLink stream into :class:`obspy.signal.spectral_estimation.PPSD`
and re-renders the accumulated 2-D histogram every ``redraw_ms`` on a
matplotlib figure, with Peterson's NLNM/NHNM noise models overlaid.

Follows McNamara & Buland (2004) by default: 3600 s segments with 50 %
overlap, 13 sub-segments per segment, response-removed to acceleration.
``--ppsd-length`` and ``--overlap`` expose the knobs but departing from
the defaults breaks comparability with published noise models.

Architecture is deliberately parallel to ``viewer.run_viewer``:

- SeedLink worker (``buffer.start_seedlink_worker``) fills a
  :class:`~seedlink_py_utils.buffer.TraceBuffer` with raw packets.
- Main thread polls the buffer, passes the latest trace into
  :meth:`obspy.signal.spectral_estimation.PPSD.add` — which is
  idempotent w.r.t. already-processed segments — and re-draws.
- Response removal happens inside PPSD (given ``metadata=inventory``),
  so we don't call ``remove_response_safe`` ourselves.

Key differences from the viewer:

- The buffer is sized to hold at least ``2 * ppsd_length`` so a redraw
  can see a full un-processed segment (the viewer only needs the
  display window).
- A response is **required** — without it, PPSD output is unitless and
  cannot be compared against NLNM/NHNM. ``run_ppsd`` raises at startup
  if inventory loading returns ``None``.
"""

import warnings
from dataclasses import dataclass
from typing import Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from obspy import UTCDateTime
from obspy.signal.spectral_estimation import PPSD, get_nhnm, get_nlnm

from .buffer import TraceBuffer, start_seedlink_worker
from .config import THEMES
from .gui import apply_theme_to_axes, go_fullscreen, set_tk_window_bg
from .processing import load_inventory


# ObsPy prints these UserWarnings on every ``PPSD.add()`` call:
#
#   "Trace is shorter than this PPSD's 'ppsd_length' (...) Skipping trace: ..."
#     — during the ~60 minutes of initial accumulation before the first
#       segment lands (every redraw retries).
#   "Already covered time spans detected (...), skipping these slices."
#     — every redraw after the first segment, because the rolling
#       buffer we hand to add() always overlaps what PPSD has already
#       processed. This is the idempotency we deliberately rely on; the
#       warning is literally "you gave me data I already saw".
#
# Both are expected behaviour in this live-monitor design; silence them
# so the terminal stays readable during long sessions. Specific
# message-pattern filters rather than a blanket UserWarning ignore so
# other PPSD warnings (e.g. response-removal failures) still surface.
_SHORT_TRACE_WARNING_PATTERN = r"Trace is shorter than this PPSD's .*"
_ALREADY_COVERED_WARNING_PATTERN = r"Already covered time spans detected.*"


@dataclass
class PPSDConfig:
    """Runtime configuration for the real-time PPSD monitor."""

    nslc: Tuple[str, str, str, str]
    seedlink_server: str = "rtserve.iris.washington.edu:18000"
    fdsn_server: Optional[str] = "https://service.earthscope.org"
    inventory_path: Optional[str] = None
    no_cache: bool = False

    # PPSD segment parameters. Defaults match McNamara & Buland (2004);
    # departing from them breaks comparability with published noise models.
    ppsd_length: float = 3600.0
    overlap: float = 0.5

    # Startup backfill from the server's ring buffer so the histogram is
    # non-empty within minutes of launch instead of hours. Most SeedLink
    # ring buffers cover at least a few hours, so 2 h is a safe default.
    backfill_hours: float = 2.0

    # If set, drop PSDs older than this many hours from the displayed
    # histogram (sliding window). ``None`` accumulates forever.
    max_hours: Optional[float] = None

    # Redraw period for the matplotlib animation. PPSD changes slowly —
    # 10 s is comfortable and costs almost nothing.
    redraw_ms: int = 10000

    # Peterson low/high noise model overlay. Default on because it's the
    # point of the PPSD plot; --no-noise-models disables.
    show_noise_models: bool = True

    cmap: str = "pqlx"

    fullscreen: bool = False
    dark_mode: bool = False


def _resolve_cmap(name):
    """Resolve a colormap name to something matplotlib will accept.

    Special-cases the string ``"pqlx"`` (the historical PQLX PPSD
    colormap, which is ObsPy's own default for ``PPSD.plot``). The
    ``obspy.imaging.cm.pqlx`` object isn't registered with matplotlib
    by name, so we resolve it here and pass the Colormap object
    through. Any other string goes straight to matplotlib, which will
    raise if it's unknown.
    """
    if name == "pqlx":
        try:
            from obspy.imaging.cm import pqlx as _pqlx
            return _pqlx
        except Exception:
            return "viridis"
    return name


def _coverage_bars(times_processed, window_start, window_end, seg_length):
    """Compute ``broken_barh`` inputs for the coverage strip.

    Returns a list of ``(x, width)`` tuples, one per processed segment
    inside the window. Segments extending past the window edges are
    clipped. Returns ``[]`` if no segments fall inside.
    """
    bars = []
    w0 = float(window_start.timestamp)
    w1 = float(window_end.timestamp)
    for t in times_processed:
        s = float(t.timestamp)
        e = s + seg_length
        if e <= w0 or s >= w1:
            continue
        s = max(s, w0)
        e = min(e, w1)
        bars.append((s, e - s))
    return bars


def _render_ppsd_on_axes(
    ax,
    ppsd: PPSD,
    *,
    cmap: str,
    show_noise_models: bool,
    theme,
    fg_color: str,
    buffered_seconds: Optional[float] = None,
    ax_coverage=None,
    window: Optional[Tuple[UTCDateTime, UTCDateTime]] = None,
) -> None:
    """Render the current PPSD histogram on ``ax``.

    Clears the axes and redraws from scratch each call. That's cheap
    enough at ~0.1 Hz redraw rates (the histogram is small, pcolormesh
    is fast) and avoids the bookkeeping of maintaining persistent
    artists across axis-range changes.

    We roll our own plot rather than calling :meth:`PPSD.plot` because
    ObsPy's plot creates a new Figure each call — fine for one-shot
    plotting, wrong for a live dashboard where we want a single window
    that updates in place.

    Parameters
    ----------
    ax_coverage
        Optional second axes for a coverage strip beneath the main
        histogram. When provided, filled bars mark the intervals
        covered by successfully-processed PSDs within ``window``.
        Requires ``window`` to be set.
    window
        Optional ``(start, end)`` UTCDateTime tuple defining the time
        range represented by the plot. Used to lay out the coverage
        strip; unused if ``ax_coverage`` is None.
    """
    cmap = _resolve_cmap(cmap)
    ax.clear()
    apply_theme_to_axes(ax, theme)

    ax.set_xscale("log")
    ax.set_xlabel("Period [s]")
    ax.set_ylabel("Amplitude [dB re 1 (m/s$^2$)$^2$/Hz]")
    ax.grid(True, which="both", linestyle="--",
            alpha=theme["grid_alpha"], color=theme["grid"])

    if ax_coverage is not None:
        ax_coverage.clear()
        apply_theme_to_axes(ax_coverage, theme)

    # ObsPy's ``current_histogram`` is a property whose getter raises
    # ``Exception("No data accumulated")`` — NOT returns None — when no
    # PSDs have been processed yet. Gate the access on
    # ``times_processed`` to avoid the exception; accessing the property
    # is only safe once at least one segment has landed.
    n_psds = len(ppsd.times_processed)
    hist = ppsd.current_histogram if n_psds > 0 else None

    if hist is None or hist.sum() == 0:
        seg_min = ppsd.ppsd_length / 60.0
        lines = ["Accumulating first PPSD segment..."]
        if buffered_seconds is not None:
            pct = min(100.0, 100.0 * buffered_seconds / ppsd.ppsd_length)
            lines.append("")
            lines.append(
                f"Buffered: {buffered_seconds / 60.0:.1f} / {seg_min:.0f} min  "
                f"({pct:.0f}%)"
            )
        lines.append("")
        lines.append(
            f"Waiting for one contiguous {int(ppsd.ppsd_length)} s segment. "
            f"Segments processed so far: {n_psds}."
        )
        lines.append("")
        lines.append(
            "Tip: for faster feedback while testing, relaunch with\n"
            "  --ppsd-length 300   (5 min segments; breaks NLNM/NHNM comparability)"
        )
        ax.text(
            0.5, 0.5, "\n".join(lines),
            transform=ax.transAxes, ha="center", va="center",
            color=fg_color, fontsize=11,
        )
        if ax_coverage is not None:
            ax_coverage.set_yticks([])
            ax_coverage.set_xticks([])
        return

    # Convert counts to percentage-per-period-bin so the colour scale
    # stays interpretable regardless of how many PSDs have accumulated.
    # ``hist`` is shape (n_period_bins, n_db_bins); each row sums to
    # ``n_psds`` once a segment has contributed to every period bin.
    with np.errstate(divide="ignore", invalid="ignore"):
        row_sums = hist.sum(axis=1, keepdims=True)
        hist_pct = np.where(row_sums > 0, hist * 100.0 / row_sums, 0.0)

    # Edges for pcolormesh: x needs one extra entry beyond
    # period_bin_left_edges, y is the db_bin_edges that ObsPy already
    # stores as edges. ``period_xedges`` was added in a recent ObsPy
    # and bundles both; prefer it when available.
    period_edges = getattr(ppsd, "period_xedges", None)
    if period_edges is None:
        left = np.asarray(ppsd.period_bin_left_edges)
        # Half-octave step is the McNamara default; use the spacing of
        # the last two bins to pick the right end so we don't hard-code
        # it (a user can override ``period_smoothing_width_octaves``).
        if left.size >= 2:
            ratio = left[-1] / left[-2]
        else:
            ratio = 2.0 ** 0.125
        period_edges = np.append(left, left[-1] * ratio)
    db_edges = np.asarray(ppsd.db_bin_edges)

    ax.pcolormesh(
        period_edges, db_edges, hist_pct.T,
        cmap=cmap, shading="flat", rasterized=True,
    )

    if show_noise_models:
        nlnm_p, nlnm_db = get_nlnm()
        nhnm_p, nhnm_db = get_nhnm()
        ax.plot(nlnm_p, nlnm_db, color="0.5", lw=1.3, ls="--", zorder=5)
        ax.plot(nhnm_p, nhnm_db, color="0.5", lw=1.3, ls="--", zorder=5)

    ax.set_xlim(period_edges[0], period_edges[-1])
    ax.set_ylim(db_edges[0], db_edges[-1])

    # Coverage strip: one filled bar per processed segment, clipped to
    # the window. Shows at a glance where data is present vs absent
    # across the window.
    if ax_coverage is not None and window is not None:
        w0, w1 = window
        bars = _coverage_bars(
            ppsd.times_processed, w0, w1, ppsd.ppsd_length,
        )
        if bars:
            ax_coverage.broken_barh(
                bars, (0.15, 0.7),
                facecolors=theme["accent"], edgecolors="none",
            )
        ax_coverage.set_xlim(float(w0.timestamp), float(w1.timestamp))
        ax_coverage.set_ylim(0, 1)
        ax_coverage.set_yticks([])
        # X-axis: at most a handful of ticks, rendered as dates
        _format_coverage_xaxis(ax_coverage, w0, w1, fg_color)


def _format_coverage_xaxis(ax, w0: UTCDateTime, w1: UTCDateTime, fg_color: str):
    """Put a small number of readable date ticks on the coverage strip.

    ObsPy `UTCDateTime.timestamp` is unix seconds; we convert a few
    evenly-spaced tick times back to UTC date strings at a resolution
    appropriate to the window length.
    """
    span = float(w1 - w0)
    n_ticks = 5
    positions = [float(w0.timestamp) + i * span / (n_ticks - 1)
                 for i in range(n_ticks)]
    if span <= 2 * 86400:
        fmt = "%H:%M"
    elif span <= 14 * 86400:
        fmt = "%m-%d"
    else:
        fmt = "%Y-%m-%d"
    labels = [UTCDateTime(p).strftime(fmt) for p in positions]
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=8, color=fg_color)


def run_ppsd(cfg: PPSDConfig) -> None:
    """Launch the real-time PPSD monitor.

    Parameters
    ----------
    cfg : PPSDConfig
        Runtime configuration. ``cfg.fdsn_server`` or ``cfg.inventory_path``
        must resolve to a response — PPSD output is unitless otherwise and
        can't be compared against Peterson's noise models.

    Raises
    ------
    RuntimeError
        If no inventory could be loaded.
    """
    net, sta, loc, cha = cfg.nslc
    theme = THEMES["dark" if cfg.dark_mode else "light"]

    # Loading requires a ViewerConfig-shaped object; PPSDConfig duck-types
    # cleanly because ``load_inventory`` only reads ``nslc``,
    # ``inventory_path``, ``no_cache``, ``fdsn_server``.
    inventory = load_inventory(cfg)
    if inventory is None:
        raise RuntimeError(
            "PPSD requires an instrument response but none could be loaded. "
            "Pass --fdsn with a working FDSN base URL (default: "
            "https://service.earthscope.org) or --inventory path/to/station.xml."
        )

    # Buffer must cover at least one full PPSD segment plus some slack
    # so the main thread can always see an un-processed segment in the
    # buffer even if it's momentarily behind. 2× ppsd_length is safe;
    # override with a floor of 2 h (matches the default backfill).
    buffer_seconds = int(max(cfg.ppsd_length * 2, 7200))
    tracebuf = TraceBuffer(buffer_seconds)
    backfill_seconds = int(cfg.backfill_hours * 3600) if cfg.backfill_hours > 0 else 0
    start_seedlink_worker(
        cfg.seedlink_server, [cfg.nslc], tracebuf,
        backfill_seconds=backfill_seconds,
    )

    # The PPSD object needs a ``Stats`` to know NSLC + sampling rate, so
    # we initialize it lazily on the first trace. This also avoids
    # guessing the sample rate from the inventory (which is fine in
    # principle but redundant once the first packet arrives).
    state = {"ppsd": None}

    fig = plt.figure(figsize=(10, 7.0), facecolor=theme["bg"])
    # Two rows: histogram on top, thin coverage strip below.
    gs = fig.add_gridspec(
        2, 1, height_ratios=[1.0, 0.08],
        left=0.09, right=0.97, top=0.92, bottom=0.08, hspace=0.22,
    )
    ax = fig.add_subplot(gs[0, 0])
    ax_cov = fig.add_subplot(gs[1, 0])
    apply_theme_to_axes(ax, theme)
    apply_theme_to_axes(ax_cov, theme)

    loc_str = loc if loc else "--"
    nslc_str = f"{net}.{sta}.{loc_str}.{cha}"

    def update(_frame):
        tr = tracebuf.latest_nslc(net, sta, loc, cha)
        if tr is None:
            ax.clear()
            ax_cov.clear()
            apply_theme_to_axes(ax, theme)
            apply_theme_to_axes(ax_cov, theme)
            ax.text(
                0.5, 0.5, "Waiting for first SeedLink packet...",
                transform=ax.transAxes, ha="center", va="center",
                color=theme["fg"], fontsize=11,
            )
            ax.set_title(
                f"PPSD — {nslc_str} from {cfg.seedlink_server}",
                fontsize=10, color=theme["fg"],
            )
            ax_cov.set_xticks([])
            ax_cov.set_yticks([])
            return ()

        # Lazy PPSD init on the first usable trace. ``PPSD.__init__``
        # needs the ``Stats`` for sampling rate and NSLC; we can't know
        # the sampling rate until a packet arrives.
        if state["ppsd"] is None:
            state["ppsd"] = PPSD(
                tr.stats, metadata=inventory,
                ppsd_length=cfg.ppsd_length,
                overlap=cfg.overlap,
            )
            print(f"PPSD initialized: fs={tr.stats.sampling_rate} Hz, "
                  f"ppsd_length={cfg.ppsd_length}s, overlap={cfg.overlap}")

        ppsd = state["ppsd"]

        # ``add`` is idempotent — it only processes segments it hasn't
        # seen before, by matching the requested window against
        # ``_times_processed``. So calling it with overlapping data (a
        # rolling 2h buffer handed over every ~10 s) is safe and cheap.
        # Short traces (< ppsd_length) are skipped with a UserWarning
        # that ObsPy emits on every redraw until the buffer covers a
        # full segment — suppressed here because we already report
        # accumulation progress on the figure.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=_SHORT_TRACE_WARNING_PATTERN,
                category=UserWarning,
            )
            warnings.filterwarnings(
                "ignore",
                message=_ALREADY_COVERED_WARNING_PATTERN,
                category=UserWarning,
            )
            ppsd.add(tr)

        # Sliding-window cap. Passing ``starttime`` re-bins from only
        # the PSDs in that range; the master PSD list in the PPSD
        # object is not trimmed, so max_hours only affects what's
        # plotted. Skip the call entirely when no PSDs exist yet,
        # otherwise ``current_histogram`` access in the renderer will
        # raise ``Exception("No data accumulated")``.
        if len(ppsd.times_processed) > 0:
            if cfg.max_hours is not None:
                cutoff = UTCDateTime() - cfg.max_hours * 3600
                ppsd.calculate_histogram(starttime=cutoff)
            else:
                ppsd.calculate_histogram()

        # Coverage window: for the live tool, from the earliest
        # processed segment (or now - max_hours if set) to now.
        now = UTCDateTime()
        if len(ppsd.times_processed) > 0:
            if cfg.max_hours is not None:
                window_start = now - cfg.max_hours * 3600
            else:
                window_start = min(ppsd.times_processed)
        else:
            window_start = tr.stats.starttime
        window = (window_start, now)

        buffered = float(tr.stats.endtime - tr.stats.starttime)
        _render_ppsd_on_axes(
            ax, ppsd,
            cmap=cfg.cmap,
            show_noise_models=cfg.show_noise_models,
            theme=theme,
            fg_color=theme["fg"],
            buffered_seconds=buffered,
            ax_coverage=ax_cov,
            window=window,
        )

        # Title: NSLC, date range, PSD count, completeness.
        n = len(ppsd.times_processed)
        span_s = float(now - window_start)
        # Expected PSDs across the window, given overlap. Strictly a
        # lower bound on "could have had" — real coverage depends on
        # when streaming actually started.
        step = max(cfg.ppsd_length * (1.0 - cfg.overlap), 1.0)
        expected = max(1, int(span_s / step))
        pct = min(100, int(round(100.0 * n / expected))) if expected > 0 else 0

        date_range = (f"{window_start.strftime('%Y-%m-%d %H:%M')}"
                      f" → {now.strftime('%Y-%m-%d %H:%M')} UTC")
        window_note = f"   [last {cfg.max_hours:g} h]" if cfg.max_hours else ""
        title = (
            f"PPSD — {nslc_str} — {date_range}{window_note}\n"
            f"[{n} / {expected} PSDs ({pct}%), "
            f"{int(cfg.ppsd_length)} s × {int(cfg.overlap * 100)}% overlap]"
        )
        ax.set_title(title, fontsize=9, color=theme["fg"])
        return ()

    ani = FuncAnimation(
        fig, update, interval=cfg.redraw_ms,
        blit=False, cache_frame_data=False,
    )

    def on_key(event):
        if event.key == "escape":
            plt.close(fig)
    fig.canvas.mpl_connect("key_press_event", on_key)

    fig._ani = ani  # keep a reference so FuncAnimation isn't GC'd

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
