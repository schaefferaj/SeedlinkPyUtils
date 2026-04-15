"""Real-time SeedLink stream availability dashboard.

Polls ``INFO=STREAMS`` on a schedule and renders a per-NSLC latency table
(wall-clock seconds since the server's last packet for that stream) with
OK / LAG / STALE classification. Meant to be left running in a terminal
as a live operator view — the one-shot ``seedlink-py-info`` is the
snapshot equivalent.

All rendering goes through pure functions (``classify``, ``_fmt_latency``,
``compute_rows``, ``render``) so the protocol-free logic is testable
without a live server. :func:`run_dashboard` is the polling loop around
them.
"""

import dataclasses
import fnmatch
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from obspy import UTCDateTime

from .info import filter_records, parse_streams, query_info


# ANSI escape sequences. Kept as module-level constants so tests can
# match against them literally.
ANSI_CLEAR = "\x1b[2J\x1b[H"
ANSI_RESET = "\x1b[0m"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_GREEN = "\x1b[32m"
ANSI_YELLOW = "\x1b[33m"
ANSI_RED = "\x1b[31m"


_STATUS_COLOR = {
    "OK":      ANSI_GREEN,
    "LAG":     ANSI_YELLOW,
    "STALE":   ANSI_RED,
    "UNKNOWN": ANSI_DIM,
}


@dataclass
class DashboardConfig:
    """Runtime configuration for the stream availability dashboard."""

    server: str = "seiscomp.hakai.org:18000"
    interval: float = 30.0           # poll period, seconds
    ok_threshold: float = 60.0       # latency < this → OK
    stale_threshold: float = 600.0   # latency > this → STALE; between = LAG
    network: Optional[str] = None    # client-side NET filter (exact match)
    station: Optional[str] = None    # client-side STA filter (exact match)
    channel: Optional[str] = None    # client-side CHA filter; ? / * wildcards
                                     # (e.g. 'EHZ', 'HH?', '*Z')
    color: bool = True               # emit ANSI colour escapes
    once: bool = False               # run one poll and exit (no screen clear)
    timeout: float = 30.0            # per-poll socket timeout, seconds


# ---------------------------------------------------------------------------
# Pure helpers (no I/O, no global state — testable)
# ---------------------------------------------------------------------------

def classify(latency_s: Optional[float], ok: float, stale: float) -> str:
    """Classify a latency into OK / LAG / STALE / UNKNOWN.

    Negative latencies (server clock ahead of local clock) are treated as
    OK — data is flowing, there's just clock skew. `None` means the server
    didn't give us a parseable end_time and the caller should show UNKNOWN.
    """
    if latency_s is None:
        return "UNKNOWN"
    if latency_s < 0:
        return "OK"
    if latency_s <= ok:
        return "OK"
    if latency_s <= stale:
        return "LAG"
    return "STALE"


def _parse_end_time(s: str) -> Optional[UTCDateTime]:
    """Parse a server-supplied end_time. Returns None on anything unparseable.

    Permissive by design — SeisComP, IRIS ringserver, and older servers
    don't all emit the same format, and ``UTCDateTime`` handles most
    variants. A missing or placeholder (e.g. ``'1970-01-01...'``) value
    still parses; the resulting latency will just be very large and
    classified as STALE.
    """
    if not s:
        return None
    try:
        return UTCDateTime(s)
    except Exception:
        return None


def filter_by_channel(records: list, pattern: Optional[str]) -> list:
    """Filter INFO=STREAMS records by channel code, with SeedLink-style
    ``?`` / ``*`` wildcards. Case-insensitive, matching the semantics of
    the existing ``filter_records`` (NET/STA).

    ``pattern=None`` or empty is a no-op. Typical uses:
    ``'EHZ'`` → exact (collapses a Shake network to one row per station);
    ``'HH?'`` → all HH-band channels; ``'*Z'`` → all verticals.
    """
    if not pattern:
        return list(records)
    up = pattern.upper()
    return [r for r in records
            if fnmatch.fnmatchcase(r.get("channel", "").upper(), up)]


def _fmt_latency(latency_s: Optional[float]) -> str:
    """Format a latency in the largest unit that keeps the number small."""
    if latency_s is None:
        return "—"
    if latency_s < 0:
        # Server clock ahead of ours; treat the magnitude as the display.
        latency_s = -latency_s
    if latency_s < 60:
        return f"{latency_s:.1f}s"
    if latency_s < 3600:
        return f"{latency_s/60:.1f}m"
    if latency_s < 86400:
        return f"{latency_s/3600:.1f}h"
    return f"{latency_s/86400:.1f}d"


def compute_rows(records: list, now: UTCDateTime,
                 cfg: DashboardConfig) -> List[dict]:
    """Decorate each INFO=STREAMS record with end_time / latency / status.

    Returns a list of dicts with keys:
    ``network, station, location, channel, end_time, latency_s, status``.
    """
    rows = []
    for r in records:
        end_t = _parse_end_time(r.get("end_time", ""))
        latency_s = float(now - end_t) if end_t is not None else None
        status = classify(latency_s, cfg.ok_threshold, cfg.stale_threshold)
        rows.append({
            "network":   r.get("network", ""),
            "station":   r.get("station", ""),
            "location":  r.get("location", ""),
            "channel":   r.get("channel", ""),
            "end_time":  r.get("end_time", ""),
            "latency_s": latency_s,
            "status":    status,
        })
    return rows


def _counts(rows: List[dict]) -> Counter:
    c = Counter()
    for r in rows:
        c[r["status"]] += 1
    return c


def render(rows: List[dict], cfg: DashboardConfig, server: str,
           polled_at: UTCDateTime, clear_screen: bool = True) -> str:
    """Render one dashboard frame as a single string.

    When ``clear_screen`` is True the frame starts with an ANSI clear-
    and-home sequence; pass False for ``--once`` or non-TTY output where
    a growing log is the intent.
    """
    out = []
    if clear_screen:
        out.append(ANSI_CLEAR)

    # Banner
    banner = (
        f"SeedLink Stream Availability — {server}\n"
        f"Polled: {polled_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"   interval: {cfg.interval:.0f}s"
        f"   streams: {len(rows)}"
    )
    if cfg.color:
        banner = ANSI_BOLD + banner + ANSI_RESET
    out.append(banner)
    out.append("")

    # Column header
    col_hdr = (
        f"{'NET':<3} {'STA':<5} {'LOC':<3} {'CHA':<3} "
        f"{'LAST PACKET':<24} {'LATENCY':>10}  STATUS"
    )
    if cfg.color:
        col_hdr = ANSI_BOLD + col_hdr + ANSI_RESET
    out.append(col_hdr)
    out.append("-" * 72)

    # Rows
    for r in rows:
        status = r["status"]
        status_field = f"{status:<7}"
        if cfg.color:
            status_field = _STATUS_COLOR[status] + status_field + ANSI_RESET
        loc = r["location"] if r["location"] else "--"
        line = (
            f"{r['network']:<3} {r['station']:<5} {loc:<3} {r['channel']:<3} "
            f"{r['end_time']:<24} {_fmt_latency(r['latency_s']):>10}  "
            f"{status_field}"
        )
        out.append(line)

    # Summary footer
    counts = _counts(rows)
    summary = (
        f"  OK: {counts['OK']}"
        f"   LAG: {counts['LAG']}"
        f"   STALE: {counts['STALE']}"
        f"   UNKNOWN: {counts['UNKNOWN']}"
    )
    if cfg.color:
        summary = ANSI_BOLD + summary + ANSI_RESET
    out.append("")
    out.append(summary)

    return "\n".join(out) + "\n"


def _sort_key(r: dict):
    return (r.get("network", ""), r.get("station", ""),
            r.get("location", ""), r.get("channel", ""))


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run_dashboard(cfg: DashboardConfig) -> None:
    """Main polling loop.

    Renders one frame per ``cfg.interval`` seconds. Handles Ctrl-C cleanly
    and keeps running across per-poll errors (network blip, server
    temporarily refusing INFO) — those show up as a one-line "Poll failed"
    message in place of the frame and the loop continues.

    When stdout is not a TTY (piped / redirected), colour escapes and
    screen-clearing are disabled automatically so the output is a readable
    growing log rather than a sea of control codes.
    """
    is_tty = sys.stdout.isatty()
    if not is_tty:
        cfg = dataclasses.replace(cfg, color=False)
    clear_screen = is_tty and not cfg.once

    try:
        while True:
            try:
                xml = query_info(cfg.server, level="STREAMS",
                                 timeout=cfg.timeout)
                records = parse_streams(xml)
                if cfg.network or cfg.station:
                    records = filter_records(
                        records, network=cfg.network, station=cfg.station,
                    )
                if cfg.channel:
                    records = filter_by_channel(records, cfg.channel)
                records.sort(key=_sort_key)
                now = UTCDateTime()
                rows = compute_rows(records, now, cfg)
                frame = render(rows, cfg, cfg.server,
                               polled_at=now, clear_screen=clear_screen)
                sys.stdout.write(frame)
                sys.stdout.flush()
            except Exception as e:
                sys.stdout.write(f"Poll failed: {e}\n")
                sys.stdout.flush()

            if cfg.once:
                break
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
