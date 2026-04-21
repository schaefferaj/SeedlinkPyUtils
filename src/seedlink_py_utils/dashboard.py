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
import logging
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional

from obspy import UTCDateTime

from .alerts import post_webhook, resolve_hostname
from .info import filter_records, parse_streams, query_info


log = logging.getLogger("seedlink_py_utils.dashboard")


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

    server: str = "rtserve.iris.washington.edu:18000"
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
    sort_by_status: bool = False     # group rows by status (STALE first);
                                     # alphabetical NSLC within each group
    alert: bool = False              # enable transition alerting
    webhook_url: Optional[str] = None
    webhook_timeout: float = 10.0
    hostname: Optional[str] = None


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


# Fixed layout overhead when paginating, in terminal lines:
#   2  banner (multi-line f-string)
#   1  blank separator
#   1  column header
#   1  divider
#   1  blank before summary
#   1  summary
#   1  hidden-rows notice (always reserved so the floor is safe even
#      when no truncation ends up happening)
# = 8 lines. Floor the data-row count at 3 so even a tiny terminal still
# shows *some* data rather than going blank.
_LAYOUT_OVERHEAD = 8
_MIN_DATA_ROWS = 3


def _terminal_lines(fallback: int = 24) -> int:
    """Return terminal height in lines with a sensible fallback."""
    try:
        return shutil.get_terminal_size(fallback=(80, fallback)).lines
    except Exception:
        return fallback


def _paginate(rows: List[dict], term_lines: int):
    """Fit ``rows`` into the available terminal space.

    Returns ``(visible, hidden_count, hidden_by_status)``. When the input
    already fits, returns the input unchanged with zero hidden. When it
    doesn't, truncates from the *end* of the list (preserving the
    caller's sort order) — so with ``--sort-by-status`` the STALE rows
    at the top are the ones that stay visible, and the first rows to
    drop off are OK rows at the bottom.
    """
    max_rows = max(_MIN_DATA_ROWS, term_lines - _LAYOUT_OVERHEAD)
    if len(rows) <= max_rows:
        return rows, 0, Counter()
    visible = rows[:max_rows]
    hidden = rows[max_rows:]
    return visible, len(hidden), Counter(r["status"] for r in hidden)


def render(rows: List[dict], cfg: DashboardConfig, server: str,
           polled_at: UTCDateTime, clear_screen: bool = True,
           paginate: bool = False) -> str:
    """Render one dashboard frame as a single string.

    When ``clear_screen`` is True the frame starts with an ANSI clear-
    and-home sequence; pass False for ``--once`` or non-TTY output where
    a growing log is the intent.

    When ``paginate`` is True, truncate the table to fit the current
    terminal height (via :func:`shutil.get_terminal_size`) and append a
    "... N more rows hidden" notice summarising what was dropped by
    status bucket. The banner and summary footer always reflect the
    **full** row set, so the counts stay accurate regardless of
    truncation.
    """
    # Snapshot the full list so the banner and summary reflect totals
    # rather than the post-truncation subset.
    all_rows = rows
    hidden_count = 0
    hidden_by_status: Counter = Counter()
    if paginate:
        rows, hidden_count, hidden_by_status = _paginate(
            rows, _terminal_lines()
        )

    out = []
    if clear_screen:
        out.append(ANSI_CLEAR)

    # Banner — streams count is the total, not the visible subset.
    banner = (
        f"SeedLink Stream Availability — {server}\n"
        f"Polled: {polled_at.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"   interval: {cfg.interval:.0f}s"
        f"   streams: {len(all_rows)}"
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

    # Rows (possibly truncated)
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

    # Truncation notice — ordered by most-populous bucket first.
    if hidden_count:
        breakdown = ", ".join(
            f"{n} {s}" for s, n in hidden_by_status.most_common()
        )
        notice = f"  ... {hidden_count} more rows hidden ({breakdown})"
        if cfg.color:
            notice = ANSI_DIM + notice + ANSI_RESET
        out.append(notice)

    # Summary footer — always reflects the full row set.
    counts = _counts(all_rows)
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


# Status ordering used by --sort-by-status. STALE first (needs focus),
# then LAG (watch territory), then UNKNOWN (rare schema surprise worth
# investigating), then OK (healthy confirmation at the bottom). Any status
# not in this table falls through to the end via the default 99.
_STATUS_RANK = {"STALE": 0, "LAG": 1, "UNKNOWN": 2, "OK": 3}


def _sort_key_by_status(r: dict):
    """Sort key that ranks by status first, then alphabetically by NSLC.

    Requires ``r["status"]`` to be set — so this is applied to rows
    returned from :func:`compute_rows`, not to raw INFO=STREAMS records.
    """
    return (_STATUS_RANK.get(r.get("status", "UNKNOWN"), 99),
            r.get("network", ""), r.get("station", ""),
            r.get("location", ""), r.get("channel", ""))


# ---------------------------------------------------------------------------
# Transition alerting
# ---------------------------------------------------------------------------

def _worst_status(statuses) -> str:
    """Return the worst status from an iterable, using _STATUS_RANK ordering.

    _STATUS_RANK: STALE=0, LAG=1, UNKNOWN=2, OK=3 — lower is worse.
    """
    return min(statuses, key=lambda s: _STATUS_RANK.get(s, 99))


_WEBHOOK_COLOR = {
    "STALE": "#cc0000",   # red
    "LAG":   "#ff9900",   # orange
    "OK":    "#2eb67d",   # green
    "UNKNOWN": "#888888", # grey
}


class DashboardAlerter:
    """Track per-station status across dashboard polls and emit alerts.

    Status is aggregated at the station level (NET.STA): the station's
    status is the worst of its channels (STALE > LAG > UNKNOWN > OK).
    Webhooks fire on any station-level status change (OK -> LAG,
    LAG -> STALE, and recoveries). First sighting is baseline only.

    Each webhook message includes all channels of the station with their
    individual status and latency, so the operator has full context
    without a second query.
    """

    def __init__(self, cfg: DashboardConfig):
        self._prev_station: Dict[str, str] = {}  # "NET.STA" -> status
        self._webhook_url = cfg.webhook_url
        self._webhook_timeout = cfg.webhook_timeout
        self._hostname = resolve_hostname(cfg.hostname)

    @staticmethod
    def _aggregate(rows: List[dict]) -> Dict[str, dict]:
        """Group rows by NET.STA and compute station-level status.

        Returns ``{station_key: {"status": str, "channels": [row, ...]}}``
        where channels are sorted by LOC.CHA.
        """
        stations: Dict[str, list] = {}
        for r in rows:
            key = f"{r['network']}.{r['station']}"
            stations.setdefault(key, []).append(r)
        result = {}
        for key, channels in stations.items():
            channels.sort(key=lambda c: (c.get("location", ""),
                                         c.get("channel", "")))
            status = _worst_status(c["status"] for c in channels)
            result[key] = {"status": status, "channels": channels}
        return result

    def update(self, rows: List[dict]) -> None:
        """Compare station-level status against previous poll and alert."""
        current = self._aggregate(rows)
        for sta_key, info in current.items():
            status = info["status"]
            prev = self._prev_station.get(sta_key)
            self._prev_station[sta_key] = status
            if prev is None or prev == status:
                continue
            self._on_transition(sta_key, prev, status, info["channels"])

    def _on_transition(self, station: str, prev: str, now: str,
                       channels: List[dict]) -> None:
        direction = "degraded" if (_STATUS_RANK.get(now, 99)
                                   < _STATUS_RANK.get(prev, 99)) else "improved"
        text = f"[{self._hostname}] {station}: {prev} \u2192 {now}"
        detail_lines = []
        for c in channels:
            loc = c.get("location") or "--"
            cha = c.get("channel", "")
            lat = _fmt_latency(c.get("latency_s"))
            detail_lines.append(f"  {loc}.{cha}  {lat} ({c['status']})")
        full_text = text + "\n" + "\n".join(detail_lines)
        log.warning(full_text)
        if self._webhook_url:
            post_webhook(
                self._webhook_url, text=full_text, event=direction,
                hostname=self._hostname, station=station,
                previous_status=prev, new_status=now,
                color=_WEBHOOK_COLOR.get(now),
                timeout=self._webhook_timeout,
            )


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
    # Screen-clear and pagination are both "interactive live mode" features:
    # they only make sense when the output is going to a TTY AND we plan to
    # redraw on a schedule (not --once). Snapshot / piped output stays
    # unconstrained so the caller gets the full table.
    interactive = is_tty and not cfg.once
    clear_screen = interactive
    paginate = interactive

    alerter = DashboardAlerter(cfg) if (cfg.alert or cfg.webhook_url) else None

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
                now = UTCDateTime()
                rows = compute_rows(records, now, cfg)
                # Sort after compute_rows so the status-aware ordering has
                # a status field to key on. Default is alphabetical NSLC.
                rows.sort(key=_sort_key_by_status
                          if cfg.sort_by_status else _sort_key)
                if alerter is not None:
                    alerter.update(rows)
                frame = render(rows, cfg, cfg.server,
                               polled_at=now,
                               clear_screen=clear_screen,
                               paginate=paginate)
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
