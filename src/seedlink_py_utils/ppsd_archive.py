"""Headless PPSD archiver: long-running daemon that maintains a master
PPSD per NSLC and periodically renders per-bucket PNGs to disk.

Architecture (Model B):

- One ``PPSD`` object per NSLC, accumulating for the lifetime of the
  daemon. On restart the master ``.npz`` is reloaded so no PSDs are
  lost across crashes or planned restarts.
- At each render tick (default 1800 s), for each active period (daily,
  weekly, monthly, quarterly, yearly — multiple may be active
  simultaneously), we call
  ``ppsd.calculate_histogram(starttime=bucket_start, endtime=bucket_end)``
  and save the resulting histogram as a PNG. Different periods simply
  re-bin the same underlying PSDs into different windows.
- Output layout::

      <output-root>/
      └── <NET>.<STA>/
          ├── <NSLC>.npz                     (master, one per NSLC)
          ├── daily/<NSLC>_YYYY-MM-DD.png
          ├── weekly/<NSLC>_YYYY-Www.png
          └── monthly/<NSLC>_YYYY-MM.png

- SIGTERM / SIGINT triggers a final flush (re-render + re-save NPZ)
  before the process exits, so you never lose the latest histogram
  even on a clean shutdown.

The rendering helper and warning filters are imported from ``ppsd.py``
unchanged, so both the interactive (``seedlink-py-ppsd``) and the
headless (``seedlink-py-ppsd-archive``) tools paint identical
histograms, coverage strips, and NLNM/NHNM overlays.
"""

import logging
import os
import signal
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

# ``matplotlib.use("Agg")`` lives at the top of ppsd_archive_cli.py,
# before any other import, so the backend is locked to Agg before
# pyplot is ever loaded. Doing it here would be too late — __init__.py
# is the gateway for all package imports and the CLI is the single
# entry point that needs Agg.
import matplotlib.pyplot as plt

from obspy import UTCDateTime
from obspy.signal.spectral_estimation import PPSD

from .buffer import TraceBuffer, start_seedlink_worker
from .config import DEFAULT_PALETTE, resolve_theme
from .info import expand_all_wildcards
from .ppsd import (
    _ALREADY_COVERED_WARNING_PATTERN,
    _SHORT_TRACE_WARNING_PATTERN,
    _render_ppsd_on_axes,
)
from .processing import load_inventory


logger = logging.getLogger("seedlink_py_utils.ppsd_archive")


# Recognised period names. Order matters for help text only.
PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass
class PPSDArchiveConfig:
    """Runtime configuration for the headless PPSD archiver."""

    # One or more NSLC spec strings "NET.STA.LOC.CHA"; wildcards OK.
    # NET/STA wildcards need ``expand_wildcards=True``.
    streams: List[str] = field(default_factory=list)

    output_root: str = "./ppsd"

    seedlink_server: str = "rtserve.earthscope.org:18000"
    fdsn_server: Optional[str] = "https://service.earthscope.org"
    inventory_path: Optional[str] = None
    no_cache: bool = False

    # Which buckets to maintain. Multiple may be active simultaneously;
    # each NSLC's master PPSD feeds all of them.
    periods: Tuple[str, ...] = ("weekly",)

    ppsd_length: float = 3600.0
    overlap: float = 0.5

    render_interval: float = 1800.0  # seconds between PNG re-renders

    expand_wildcards: bool = False

    show_noise_models: bool = True
    cmap: str = "pqlx"

    # Hue family for the rendered PNGs; see config.PALETTES. There is no
    # dark_mode counterpart on purpose — _render_bucket_png always takes
    # the light variant, because these PNGs are viewed on white (a browser
    # tab, a report, a printout). "print" is the greyscale option.
    palette: str = DEFAULT_PALETTE

    # Backfill: periodically look back this many days for windows with
    # no PSD (station outages, archiver downtime) and fill them from FDSN
    # dataselect, then re-render the buckets that changed. 0 disables.
    backfill_days: float = 0.0
    # Dataselect base URL; None means use fdsn_server.
    backfill_fdsn: Optional[str] = None
    # Seconds between backfill passes once caught up.
    backfill_interval: float = 21600.0
    # Seconds before re-requesting a stretch that yielded no new PSDs
    # (data that genuinely doesn't exist, e.g. a dead station).
    backfill_retry: float = 86400.0

    # Per-NSLC response-load failures are logged and the NSLC is
    # skipped from the subscription list. If *all* NSLCs fail, we
    # raise — nothing left to do.
    # (No CLI flag; documented behaviour, not a toggle.)


# --------------------------------------------------------------------------- #
# Bucket math — pure functions, unit-testable without a live server
# --------------------------------------------------------------------------- #

def bucket_bounds(period: str, t: UTCDateTime) -> Tuple[UTCDateTime, UTCDateTime]:
    """Return ``(start, end)`` of the UTC calendar bucket containing ``t``.

    - ``daily``: midnight-to-midnight UTC
    - ``weekly``: ISO week, Monday 00:00 UTC to next Monday 00:00
    - ``monthly``: first-of-month UTC to first-of-next-month UTC
    - ``quarterly``: Q1=Jan–Mar, Q2=Apr–Jun, ...
    - ``yearly``: Jan 1 UTC to next Jan 1 UTC
    """
    dt = t.datetime.replace(tzinfo=timezone.utc)
    if period == "daily":
        start = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
    elif period == "weekly":
        weekday = dt.isoweekday() - 1  # Mon=0 .. Sun=6
        monday = dt - timedelta(days=weekday,
                                hours=dt.hour, minutes=dt.minute,
                                seconds=dt.second, microseconds=dt.microsecond)
        start = monday
        end = start + timedelta(days=7)
    elif period == "monthly":
        start = datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)
        if dt.month == 12:
            end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(dt.year, dt.month + 1, 1, tzinfo=timezone.utc)
    elif period == "quarterly":
        q_index = (dt.month - 1) // 3          # 0..3
        first_month = q_index * 3 + 1          # 1, 4, 7, 10
        start = datetime(dt.year, first_month, 1, tzinfo=timezone.utc)
        if first_month + 3 > 12:
            end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(dt.year, first_month + 3, 1, tzinfo=timezone.utc)
    elif period == "yearly":
        start = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
        end = datetime(dt.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        raise ValueError(f"Unknown period: {period!r}")
    return UTCDateTime(start), UTCDateTime(end)


def bucket_key(period: str, t: UTCDateTime) -> str:
    """Return the filename-safe bucket identifier for ``t`` under ``period``.

    - daily:     YYYY-MM-DD
    - weekly:    YYYY-Www     (ISO year + ISO week)
    - monthly:   YYYY-MM
    - quarterly: YYYY-Qn
    - yearly:    YYYY
    """
    dt = t.datetime.replace(tzinfo=timezone.utc)
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "weekly":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return dt.strftime("%Y-%m")
    if period == "quarterly":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    if period == "yearly":
        return str(dt.year)
    raise ValueError(f"Unknown period: {period!r}")


def expected_psds(period_duration: float, ppsd_length: float,
                  overlap: float) -> int:
    """Approximate expected PSD count for a fully-covered bucket.

    The PPSD segment step is ``ppsd_length * (1 - overlap)``; expected
    count is total duration divided by that step, rounded down.
    """
    step = max(ppsd_length * (1.0 - overlap), 1.0)
    return max(1, int(period_duration / step))


# --------------------------------------------------------------------------- #
# Backfill planning — pure functions
# --------------------------------------------------------------------------- #

def find_holes(
    times: List[UTCDateTime],
    window_start: UTCDateTime,
    window_end: UTCDateTime,
    step: float,
) -> List[Tuple[UTCDateTime, UTCDateTime]]:
    """Return the stretches of ``[window_start, window_end)`` with no PSD.

    ``times`` are PSD start times (``ppsd.times_processed``). Consecutive
    PSDs on continuous data sit one ``step`` apart; a larger spacing
    means windows are missing. Each hole starts one step after the PSD
    before it, so filling it continues the existing time grid and ObsPy
    accepts the new windows (they overlap their neighbours by exactly the
    normal amount). A hole runs up to the start of the next PSD, or to
    ``window_end``. Holes shorter than one step are ignored.
    """
    ts = sorted(t for t in times if window_start - step <= t < window_end)
    holes = []
    cursor = window_start
    if ts and ts[0] > window_start:
        # Put a leading hole on the first PSD's grid too.
        cursor = ts[0] - int((ts[0] - window_start) // step) * step
    for t in ts:
        if t - cursor >= step:
            holes.append((cursor, t))
        cursor = max(cursor, t + step)
    if window_end - cursor >= step:
        holes.append((cursor, window_end))
    return holes


def chunk_hole(
    start: UTCDateTime,
    end: UTCDateTime,
    ppsd_length: float,
    chunk: float = 86400.0,
) -> List[Tuple[UTCDateTime, UTCDateTime]]:
    """Split a hole into fetch ranges of about ``chunk`` seconds.

    Chunk starts stay on the hole's grid (``chunk`` is a whole number of
    steps for the usual 1 h / 50 % segmentation) and each fetch runs
    ``ppsd_length`` past its chunk, so windows straddling a chunk
    boundary still get computed; the repeats are skipped by ObsPy as
    already covered. The last fetch also runs ``ppsd_length`` past the
    hole so its final windows have data to the end.
    """
    out = []
    c = start
    while c < end:
        out.append((c, min(c + chunk, end) + ppsd_length))
        c += chunk
    return out


def buckets_touched(
    times: List[UTCDateTime],
    periods: Tuple[str, ...],
) -> Dict[Tuple[str, str], UTCDateTime]:
    """Map ``(period, bucket_key)`` → a time inside it, for every bucket
    that contains at least one of ``times``."""
    out: Dict[Tuple[str, str], UTCDateTime] = {}
    for t in times:
        for period in periods:
            out.setdefault((period, bucket_key(period, t)), t)
    return out


# --------------------------------------------------------------------------- #
# NPZ helpers
# --------------------------------------------------------------------------- #

def _npz_path(output_root: str, nslc: Tuple[str, str, str, str]) -> str:
    """Master NPZ path for an NSLC: ``<root>/<NET>.<STA>/<NSLC>.npz``."""
    net, sta, loc, cha = nslc
    ns_dir = os.path.join(output_root, f"{net}.{sta}")
    nslc_str = f"{net}.{sta}.{loc}.{cha}"
    return os.path.join(ns_dir, f"{nslc_str}.npz")


def _atomic_save_npz(ppsd: PPSD, path: str) -> None:
    """Save the PPSD state atomically via write-then-rename.

    ObsPy's ``PPSD.save_npz`` delegates to numpy, which auto-appends
    ``.npz`` if the filename doesn't have it — harmless here because
    we already pass a ``.npz``-ending tempfile name. ``os.replace`` is
    atomic on both POSIX and Windows for same-filesystem renames.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(path) or ".",
        prefix=".tmp_ppsd_", suffix=".npz",
    )
    os.close(tmp_fd)  # we only wanted the unique name
    try:
        ppsd.save_npz(tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _try_load_npz(
    path: str,
    inventory,
    expected_length: float,
    expected_overlap: float,
) -> Optional[PPSD]:
    """Load an NPZ from ``path`` if it exists and is compatible.

    Returns ``None`` if the file doesn't exist. Raises ``RuntimeError``
    if the file exists but its ``ppsd_length`` or ``overlap`` disagree
    with the current config — silently mixing would produce a
    histogram where different PSDs assume different windowing.
    """
    if not os.path.exists(path):
        return None
    try:
        ppsd = PPSD.load_npz(path, metadata=inventory)
    except Exception as e:
        raise RuntimeError(
            f"Found existing NPZ at {path} but could not load it: {e}. "
            f"Move or delete the file and re-run to start fresh."
        ) from e

    # ObsPy's load_npz builds the PPSD from placeholder stats and only
    # then restores the real SEED id, so the responses it preloads in
    # __init__ match nothing and come back empty. Every later add() then
    # fails with "No matching response found" — silently, since add()
    # only warns — and a reloaded stream stops gaining PSDs. Redo the
    # preload now that the id is right.
    if inventory is not None:
        ppsd.responses = ppsd._preload_responses()
        if not ppsd.responses:
            logger.warning(
                "No response in the inventory matches %s; new data for it "
                "will be rejected.", ppsd.id,
            )

    if abs(ppsd.ppsd_length - expected_length) > 1e-6:
        raise RuntimeError(
            f"Existing NPZ at {path} has ppsd_length={ppsd.ppsd_length}, "
            f"but config specifies {expected_length}. Either pass "
            f"--ppsd-length {ppsd.ppsd_length} to match, or move/delete "
            f"the NPZ to start fresh."
        )
    # Not every ObsPy version exposes 'overlap' as an attribute, but
    # when it does we can check it too.
    existing_overlap = getattr(ppsd, "overlap", None)
    if existing_overlap is not None and abs(existing_overlap - expected_overlap) > 1e-6:
        raise RuntimeError(
            f"Existing NPZ at {path} has overlap={existing_overlap}, "
            f"but config specifies {expected_overlap}. Either pass "
            f"--overlap {existing_overlap} to match, or move/delete "
            f"the NPZ to start fresh."
        )
    return ppsd


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def _png_path(
    output_root: str,
    nslc: Tuple[str, str, str, str],
    period: str,
    key: str,
) -> str:
    """``<root>/<NS>/<period>/<NSLC>_<key>.png``."""
    net, sta, loc, cha = nslc
    ns_dir = os.path.join(output_root, f"{net}.{sta}", period)
    nslc_str = f"{net}.{sta}.{loc}.{cha}"
    return os.path.join(ns_dir, f"{nslc_str}_{key}.png")


def _render_bucket_png(
    ppsd: PPSD,
    nslc: Tuple[str, str, str, str],
    period: str,
    bucket_start: UTCDateTime,
    bucket_end: UTCDateTime,
    cmap: str,
    show_noise_models: bool,
    output_path: str,
    palette: str = DEFAULT_PALETTE,
) -> Tuple[int, int]:
    """Render one bucket PPSD to ``output_path``.

    Returns ``(n_psds_in_window, expected_psds)`` so the caller can log
    completeness. Re-bins the master PPSD into the bucket window
    before rendering.
    """
    # Re-bin for this window. Gated on non-empty times_processed
    # because calculate_histogram on an empty master is a no-op but
    # the ``current_histogram`` access in the renderer would raise.
    if len(ppsd.times_processed) > 0:
        ppsd.calculate_histogram(starttime=bucket_start, endtime=bucket_end)

    # Count PSDs in-window for the title/log.
    in_window = sum(
        1 for t in ppsd.times_processed
        if bucket_start <= t < bucket_end
    )
    expected = expected_psds(
        float(bucket_end - bucket_start),
        ppsd.ppsd_length,
        getattr(ppsd, "overlap", 0.5),
    )

    # PNG output is always the light variant — these are viewed on white.
    theme = resolve_theme(palette, dark=False)
    fig = plt.figure(figsize=(10, 7.0), facecolor=theme["bg"])
    gs = fig.add_gridspec(
        2, 1, height_ratios=[1.0, 0.08],
        left=0.09, right=0.97, top=0.90, bottom=0.09, hspace=0.22,
    )
    ax = fig.add_subplot(gs[0, 0])
    ax_cov = fig.add_subplot(gs[1, 0])

    try:
        _render_ppsd_on_axes(
            ax, ppsd,
            cmap=cmap,
            show_noise_models=show_noise_models,
            theme=theme,
            fg_color=theme["fg"],
            ax_coverage=ax_cov,
            window=(bucket_start, bucket_end),
        )

        # Title: NSLC, bucket key, date range, completeness.
        net, sta, loc, cha = nslc
        loc_str = loc if loc else "--"
        nslc_str = f"{net}.{sta}.{loc_str}.{cha}"
        key = bucket_key(period, bucket_start)
        date_range = (f"{bucket_start.strftime('%Y-%m-%d')}"
                      f" → {(bucket_end - 1).strftime('%Y-%m-%d')}")
        pct = int(round(100.0 * in_window / expected)) if expected > 0 else 0
        title = (
            f"PPSD — {nslc_str} — {period} {key} ({date_range} UTC)\n"
            f"[{in_window} / {expected} PSDs ({pct}%), "
            f"{int(ppsd.ppsd_length)} s × "
            f"{int(getattr(ppsd, 'overlap', 0.5) * 100)}% overlap]"
        )
        ax.set_title(title, fontsize=9, color=theme["fg"])

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        fig.savefig(output_path, dpi=120, facecolor=theme["bg"])
    finally:
        plt.close(fig)

    return in_window, expected


# --------------------------------------------------------------------------- #
# Daemon driver
# --------------------------------------------------------------------------- #

class _ShutdownSignal:
    """Shared flag set by SIGTERM / SIGINT handlers. Main loop polls
    it in short sleep ticks so the daemon shuts down promptly."""
    def __init__(self):
        self._flag = threading.Event()

    def set(self):
        self._flag.set()

    def is_set(self) -> bool:
        return self._flag.is_set()

    def wait(self, timeout: float) -> bool:
        """Sleep up to ``timeout`` seconds. Returns True if the shutdown
        flag was set before the timeout — useful as a ``break`` signal."""
        return self._flag.wait(timeout)


def _install_signal_handlers(shutdown: _ShutdownSignal) -> None:
    def _handler(signum, _frame):
        # Safe-ish in handlers: Event.set() is async-signal-safe in
        # CPython; logging is NOT. Defer the log to the main loop.
        shutdown.set()

    signal.signal(signal.SIGINT, _handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handler)


def _resolve_nslcs(cfg: PPSDArchiveConfig) -> List[Tuple[str, str, str, str]]:
    """Parse + (optionally) expand wildcards. Every returned tuple is
    fully concrete — no ``?`` or ``*`` — so we can pre-create output
    directories and master-NPZ paths."""
    if cfg.expand_wildcards:
        logger.info("Expanding wildcards via INFO=STREAMS on %s ...",
                    cfg.seedlink_server)
        expanded = expand_all_wildcards(cfg.seedlink_server, cfg.streams)
        logger.info("Expanded %d spec(s) to %d NSLC stream(s).",
                    len(cfg.streams), len(expanded))
    else:
        expanded = list(cfg.streams)
        # Sanity-check: reject wildcards if the user didn't ask for expansion.
        for spec in expanded:
            if any(c in spec for c in "?*"):
                raise ValueError(
                    f"Stream {spec!r} contains wildcards but "
                    f"--expand-wildcards was not given."
                )

    nslcs: List[Tuple[str, str, str, str]] = []
    for spec in expanded:
        parts = spec.split(".")
        if len(parts) != 4:
            raise ValueError(
                f"Stream must be NET.STA.LOC.CHA (4 dot-separated fields), "
                f"got {spec!r}"
            )
        nslcs.append(tuple(parts))  # type: ignore[arg-type]
    return nslcs


def _load_inventories(
    cfg: PPSDArchiveConfig,
    nslcs: List[Tuple[str, str, str, str]],
):
    """Load a response inventory per NSLC. Soft-fail: NSLCs whose
    inventory cannot be fetched are logged and dropped from the
    returned dict. Raises if all fail.

    ``load_inventory`` duck-types against its cfg argument (it only
    reads ``nslc``/``inventory_path``/``no_cache``/``fdsn_server``), so
    we hand it a ``SimpleNamespace`` per NSLC rather than wrapping it
    in yet another dataclass.
    """
    inventories = {}
    for nslc in nslcs:
        inv_cfg = SimpleNamespace(
            nslc=nslc,
            fdsn_server=cfg.fdsn_server,
            inventory_path=cfg.inventory_path,
            no_cache=cfg.no_cache,
        )
        inv = load_inventory(inv_cfg)
        if inv is None:
            logger.warning(
                "Skipping %s.%s.%s.%s: inventory could not be loaded "
                "(PPSD requires a response).", *nslc,
            )
            continue
        inventories[nslc] = inv

    if not inventories:
        raise RuntimeError(
            "No NSLCs could be loaded with a response. PPSD is unitless "
            "without one; nothing to do. Check --fdsn / --inventory and "
            "try --no-cache if a stale cache is masking the real error."
        )
    return inventories


def _render_and_log(
    cfg: PPSDArchiveConfig,
    ppsd: PPSD,
    nslc: Tuple[str, str, str, str],
    period: str,
    t: UTCDateTime,
    prefix: str = "",
) -> None:
    """Render the ``period`` bucket containing ``t`` and log its fill."""
    start, end = bucket_bounds(period, t)
    key = bucket_key(period, t)
    png = _png_path(cfg.output_root, nslc, period, key)
    try:
        got, expected = _render_bucket_png(
            ppsd, nslc, period, start, end,
            cfg.cmap, cfg.show_noise_models, png, cfg.palette,
        )
        pct = int(round(100.0 * got / expected)) if expected else 0
        logger.info("  %s%s %s %s: %d / %d PSDs (%d%%) → %s",
                    prefix, ".".join(nslc), period, key, got, expected,
                    pct, png)
    except Exception as e:
        logger.exception("  %s%s %s %s: render failed: %s",
                         prefix, ".".join(nslc), period, key, e)


def _add_quietly(ppsd: PPSD, data, nslc) -> None:
    """``ppsd.add`` with the routine warnings silenced and response
    failures logged. A segment skipped for want of a response is
    otherwise invisible."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.filterwarnings(
            "ignore", message=_SHORT_TRACE_WARNING_PATTERN,
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore", message=_ALREADY_COVERED_WARNING_PATTERN,
            category=UserWarning,
        )
        ppsd.add(data)
    n_resp_fail = sum("response" in str(w.message) for w in caught)
    if n_resp_fail:
        logger.warning(
            "  %s: %d segment(s) skipped — no matching response.",
            ".".join(nslc), n_resp_fail,
        )


class _Backfiller:
    """Fills PSD holes from FDSN dataselect, a slice at a time.

    Each pass walks the streams round-robin, finds holes in the last
    ``backfill_days`` (stopping short of the stretch the live SeedLink
    buffer still covers), fetches them a day at a time, adds them to the
    master PPSD, re-renders every bucket that gained PSDs and saves the
    NPZ. A pass works to a deadline and resumes where it stopped, so a
    big first catch-up never holds off the regular render tick for long.

    A fetch that adds nothing (no data, or data that can't make a PSD) is
    remembered and not re-requested for ``backfill_retry`` seconds, so a
    dead station isn't downloaded over and over. That memory is per
    process; a restart retries everything once.
    """

    # One request per day of data: ~35 MB of 100 Hz int32 at most.
    CHUNK = 86400.0

    def __init__(self, cfg: PPSDArchiveConfig, live_margin: float):
        from obspy.clients.fdsn import Client
        self.cfg = cfg
        self.url = cfg.backfill_fdsn or cfg.fdsn_server
        self.client = Client(self.url, timeout=300)
        self.live_margin = live_margin
        self.step = cfg.ppsd_length * (1.0 - cfg.overlap)
        self.cursor = 0           # next stream index in the current pass
        self.pass_open = False    # a pass is in progress
        self.last_pass_done: Optional[UTCDateTime] = None
        self.no_gain: Dict[Tuple, UTCDateTime] = {}  # (nslc, t0) -> when
        self._pass_stats = [0, set(), 0]  # PSDs added, streams, PNGs

    def due(self, now: UTCDateTime) -> bool:
        return (self.pass_open or self.last_pass_done is None
                or now - self.last_pass_done >= self.cfg.backfill_interval)

    def run(self, nslcs, masters, inventories, deadline: float,
            shutdown: "_ShutdownSignal") -> None:
        """Work through streams until the pass ends, ``deadline``
        (``time.monotonic()``) passes, or shutdown is requested."""
        if not self.pass_open:
            self.pass_open = True
            self.cursor = 0
            self._pass_stats = [0, set(), 0]
            logger.info("[backfill] pass begin: %d stream(s), last %g day(s), "
                        "from %s", len(nslcs), self.cfg.backfill_days,
                        self.url)
        while self.cursor < len(nslcs):
            if shutdown.is_set() or time.monotonic() >= deadline:
                logger.info("[backfill] pausing at stream %d/%d; resumes "
                            "after the next tick.", self.cursor, len(nslcs))
                return
            nslc = nslcs[self.cursor]
            try:
                self._fill_stream(nslc, masters, inventories[nslc],
                                  deadline, shutdown)
            except Exception as e:
                logger.exception("[backfill] %s failed: %s",
                                 ".".join(nslc), e)
            if shutdown.is_set() or time.monotonic() >= deadline:
                # _fill_stream may have stopped part-way; revisit it.
                continue
            self.cursor += 1
        self.pass_open = False
        self.last_pass_done = UTCDateTime()
        added, changed, pngs = self._pass_stats
        logger.info("[backfill] pass end: +%d PSDs in %d stream(s), %d PNG(s) "
                    "re-rendered.", added, len(changed), pngs)

    def _fill_stream(self, nslc, masters, inventory, deadline, shutdown):
        from obspy.clients.fdsn.header import FDSNNoDataException

        now = UTCDateTime()
        win_end = now - self.live_margin
        win_start = now - self.cfg.backfill_days * 86400.0
        ppsd = masters.get(nslc)
        times = list(ppsd.times_processed) if ppsd is not None else []
        holes = find_holes(times, win_start, win_end, self.step)
        if not holes:
            return

        before = set(t._ns for t in times)
        net, sta, loc, cha = nslc
        for h0, h1 in holes:
            for f0, f1 in chunk_hole(h0, h1, self.cfg.ppsd_length, self.CHUNK):
                if shutdown.is_set() or time.monotonic() >= deadline:
                    break
                key = (nslc, f0._ns)
                tried = self.no_gain.get(key)
                if tried is not None and now - tried < self.cfg.backfill_retry:
                    continue
                try:
                    st = self.client.get_waveforms(net, sta, loc, cha, f0, f1)
                except FDSNNoDataException:
                    st = None
                n0 = len(ppsd.times_processed) if ppsd is not None else 0
                if st:
                    if ppsd is None:
                        ppsd = PPSD(st[0].stats, metadata=inventory,
                                    ppsd_length=self.cfg.ppsd_length,
                                    overlap=self.cfg.overlap)
                        masters[nslc] = ppsd
                    _add_quietly(ppsd, st, nslc)
                n1 = len(ppsd.times_processed) if ppsd is not None else 0
                if n1 > n0:
                    self.no_gain.pop(key, None)
                else:
                    self.no_gain[key] = now
            else:
                continue
            break  # deadline / shutdown inside the chunk loop

        if ppsd is None:
            return
        new = [t for t in ppsd.times_processed if t._ns not in before]
        if not new:
            return
        touched = buckets_touched(new, self.cfg.periods)
        logger.info("[backfill] %s: +%d PSDs (%s → %s); re-rendering %d "
                    "bucket(s).", ".".join(nslc), len(new),
                    min(new).strftime("%Y-%m-%dT%H:%M"),
                    max(new).strftime("%Y-%m-%dT%H:%M"), len(touched))
        for (period, _key), t in sorted(touched.items()):
            _render_and_log(self.cfg, ppsd, nslc, period, t,
                            prefix="[backfill] ")
        try:
            _atomic_save_npz(ppsd, _npz_path(self.cfg.output_root, nslc))
        except Exception as e:
            logger.exception("[backfill] %s: NPZ save failed: %s",
                             ".".join(nslc), e)
        self._pass_stats[0] += len(new)
        self._pass_stats[1].add(nslc)
        self._pass_stats[2] += len(touched)


def run_ppsd_archive(cfg: PPSDArchiveConfig) -> None:
    """Headless PPSD daemon: maintain master PPSDs, render per-bucket
    PNGs on a schedule, persist master NPZ files across restarts.

    Runs until SIGINT / SIGTERM, with a final flush on shutdown.
    """
    # Validate periods
    bad = [p for p in cfg.periods if p not in PERIODS]
    if bad:
        raise ValueError(
            f"Unknown period(s): {bad}. Valid: {list(PERIODS)}"
        )
    if not cfg.periods:
        raise ValueError("At least one --period is required.")

    if not (cfg.fdsn_server or cfg.inventory_path):
        raise ValueError(
            "PPSD requires an instrument response: set fdsn_server or "
            "inventory_path."
        )

    os.makedirs(cfg.output_root, exist_ok=True)

    # 1) Resolve NSLCs (wildcard expansion)
    nslcs = _resolve_nslcs(cfg)
    if not nslcs:
        raise ValueError("No streams to subscribe to after wildcard expansion.")
    logger.info("Subscribing to %d stream(s): %s", len(nslcs),
                ", ".join(".".join(n) for n in nslcs))

    # 2) Load inventories (soft-fail per NSLC)
    n_requested = len(nslcs)
    inventories = _load_inventories(cfg, nslcs)
    nslcs = [n for n in nslcs if n in inventories]
    logger.info("Response inventories loaded for %d/%d streams.",
                len(inventories), n_requested)

    # 3) Load/create master PPSDs (lazy — needs first packet's Stats;
    #    NPZ reload is the only eager path).
    masters: Dict[Tuple[str, str, str, str], Optional[PPSD]] = {}
    for nslc in nslcs:
        path = _npz_path(cfg.output_root, nslc)
        loaded = _try_load_npz(
            path, inventories[nslc], cfg.ppsd_length, cfg.overlap,
        )
        masters[nslc] = loaded
        if loaded is not None:
            logger.info("Reloaded %d prior PSDs for %s from %s",
                        len(loaded.times_processed), ".".join(nslc), path)

    # 4) Buffer + worker
    buffer_seconds = int(max(cfg.ppsd_length * 2, 7200))
    tracebuf = TraceBuffer(buffer_seconds)
    logger.info("Starting SeedLink worker on %s (buffer=%ds).",
                cfg.seedlink_server, buffer_seconds)
    start_seedlink_worker(
        cfg.seedlink_server, nslcs, tracebuf, backfill_seconds=0,
    )

    # 5) Signal-driven main loop
    shutdown = _ShutdownSignal()
    _install_signal_handlers(shutdown)

    def do_tick(final: bool = False) -> None:
        """One render cycle: drain buffer into each master PPSD, render
        all active buckets for each, save master NPZs. ``final=True``
        changes only the log prefix."""
        tag = "final" if final else "tick"
        logger.info("[%s] render cycle begin", tag)
        now = UTCDateTime()

        for nslc in nslcs:
            tr = tracebuf.latest_nslc(*nslc)
            if tr is None:
                logger.debug("[%s] %s: no trace in buffer yet; skipping",
                             tag, ".".join(nslc))
                continue

            if masters[nslc] is None:
                masters[nslc] = PPSD(
                    tr.stats, metadata=inventories[nslc],
                    ppsd_length=cfg.ppsd_length, overlap=cfg.overlap,
                )
                logger.info(
                    "PPSD initialized for %s (fs=%s Hz, ppsd_length=%ss, "
                    "overlap=%s).",
                    ".".join(nslc), tr.stats.sampling_rate,
                    cfg.ppsd_length, cfg.overlap,
                )

            ppsd = masters[nslc]

            # Feed the rolling buffer — idempotent; already-covered
            # slices are skipped.
            _add_quietly(ppsd, tr, nslc)

            n_total = len(ppsd.times_processed)
            if n_total == 0:
                buffered_min = float(tr.stats.endtime - tr.stats.starttime) / 60.0
                logger.info(
                    "  %s: no complete PSD yet (buffered %.1f / %.0f min)",
                    ".".join(nslc), buffered_min, cfg.ppsd_length / 60.0,
                )
                continue

            # Render one PNG per active period.
            for period in cfg.periods:
                _render_and_log(cfg, ppsd, nslc, period, now)

            # Persist the master NPZ every tick so the worst-case crash
            # loses at most ``render_interval`` of PPSD state.
            npz = _npz_path(cfg.output_root, nslc)
            try:
                _atomic_save_npz(ppsd, npz)
                logger.debug("  %s: saved master NPZ (%d PSDs) → %s",
                             ".".join(nslc), n_total, npz)
            except Exception as e:
                logger.exception("  %s: NPZ save failed: %s",
                                 ".".join(nslc), e)

        logger.info("[%s] render cycle end", tag)

    backfiller = None
    if cfg.backfill_days > 0:
        # Leave the stretch the live buffer still holds to the tick.
        try:
            backfiller = _Backfiller(cfg, live_margin=buffer_seconds)
        except Exception as e:
            logger.error("Backfill disabled: could not reach dataselect at "
                         "%s: %s", cfg.backfill_fdsn or cfg.fdsn_server, e)

    # Initial tick runs one render cycle promptly (so the user sees
    # progress immediately after startup instead of after the first
    # render_interval), but doesn't block shutdown if signal came
    # during NPZ load or inventory fetch.
    if not shutdown.is_set():
        do_tick()

    # Main loop: tick every render_interval. Backfill, when enabled,
    # works in the time between ticks and pauses at the next one, so the
    # live histograms keep updating however much catching up there is.
    next_tick = time.monotonic() + cfg.render_interval
    while not shutdown.is_set():
        if backfiller is not None and backfiller.due(UTCDateTime()):
            backfiller.run(nslcs, masters, inventories,
                           deadline=next_tick, shutdown=shutdown)
        if shutdown.wait(max(0.0, next_tick - time.monotonic())):
            break
        do_tick()
        next_tick = time.monotonic() + cfg.render_interval

    # Shutdown: one final tick so the latest state hits disk.
    logger.info("Shutdown requested; flushing final render + NPZs.")
    do_tick(final=True)
    logger.info("PPSD archiver stopped cleanly.")
