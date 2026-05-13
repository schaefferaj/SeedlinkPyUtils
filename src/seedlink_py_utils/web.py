"""Web interface for the dashboard and PPSD archive.

Provides a local web UI with two tabs:

- ``/dashboard`` — live SeedLink stream availability table, mirroring
  ``seedlink-py-dashboard`` but in a browser. A background thread polls
  ``INFO=STREAMS`` at the configured interval and the page auto-refreshes
  via JS poll.
- ``/ppsd`` — read-only browser over the PPSD archive directory tree
  produced by ``seedlink-py-ppsd-archive``. Drilldown: networks → stations
  → NSLCs → bucket PNGs. Images auto-refresh on file mtime change so a
  running ppsd-archive is reflected without manual reload.

Either tab can be disabled. With only ``--ppsd-root`` set, the dashboard
tab is hidden; with only ``--server`` set, the PPSD tab is hidden. With
both set, both tabs are available.

Flask is an optional dependency — install with
``pip install 'seedlink-py-utils[web]'``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from obspy import UTCDateTime

from .dashboard import (
    DashboardConfig,
    _sort_key,
    _sort_key_by_status,
    compute_rows,
    filter_by_channel,
)
from .info import filter_records, parse_streams, query_info


log = logging.getLogger("seedlink_py_utils.web")


# PPSD period subdirectories produced by seedlink-py-ppsd-archive.
_PPSD_PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")


@dataclass
class WebConfig:
    """Runtime configuration for the web interface."""

    host: str = "127.0.0.1"
    port: int = 8080

    # Dashboard tab — disabled when server is None.
    server: Optional[str] = None
    interval: float = 30.0
    timeout: float = 30.0
    ok_threshold: float = 60.0
    stale_threshold: float = 600.0
    network: Optional[str] = None
    station: Optional[str] = None
    channel: Optional[str] = None
    sort_by_status: bool = False

    # PPSD tab — disabled when ppsd_root is None.
    ppsd_root: Optional[str] = None

    debug: bool = False

    @property
    def dashboard_enabled(self) -> bool:
        return self.server is not None

    @property
    def ppsd_enabled(self) -> bool:
        return self.ppsd_root is not None


# ---------------------------------------------------------------------------
# Dashboard background polling
# ---------------------------------------------------------------------------

@dataclass
class DashboardState:
    """Thread-safe cache of the most recent dashboard poll."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    rows: List[dict] = field(default_factory=list)
    polled_at: Optional[UTCDateTime] = None
    error: Optional[str] = None

    def update(self, rows: List[dict], polled_at: UTCDateTime) -> None:
        with self._lock:
            self.rows = rows
            self.polled_at = polled_at
            self.error = None

    def set_error(self, err: str) -> None:
        with self._lock:
            self.error = err

    def snapshot(self) -> Tuple[List[dict], Optional[UTCDateTime], Optional[str]]:
        with self._lock:
            return list(self.rows), self.polled_at, self.error


class DashboardPoller:
    """Background thread that polls SeedLink INFO=STREAMS on a schedule.

    Stores results in a shared :class:`DashboardState`. The Flask routes
    read the cached state on each request, so HTTP load is decoupled from
    server polling frequency.
    """

    def __init__(self, state: DashboardState, cfg: WebConfig):
        self.state = state
        self.cfg = cfg
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="dashboard-poller", daemon=True
        )
        self._thread.start()
        log.info(
            "dashboard-poller started: server=%s interval=%.0fs",
            self.cfg.server, self.cfg.interval,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        # First poll happens immediately so the page isn't empty on first load.
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                log.warning("dashboard poll failed: %s", e)
                self.state.set_error(str(e))
            if self._stop.wait(self.cfg.interval):
                break

    def _poll_once(self) -> None:
        xml = query_info(self.cfg.server, level="STREAMS",
                         timeout=self.cfg.timeout)
        records = parse_streams(xml)
        if self.cfg.network or self.cfg.station:
            records = filter_records(
                records, network=self.cfg.network, station=self.cfg.station,
            )
        if self.cfg.channel:
            records = filter_by_channel(records, self.cfg.channel)
        now = UTCDateTime()
        # Reuse the dashboard's row computation. We only need the threshold
        # fields; build a minimal DashboardConfig shim.
        dash_cfg = DashboardConfig(
            ok_threshold=self.cfg.ok_threshold,
            stale_threshold=self.cfg.stale_threshold,
        )
        rows = compute_rows(records, now, dash_cfg)
        rows.sort(key=_sort_key_by_status if self.cfg.sort_by_status
                  else _sort_key)
        self.state.update(rows, now)


# ---------------------------------------------------------------------------
# PPSD directory walking
# ---------------------------------------------------------------------------

def _ppsd_root(cfg: WebConfig) -> Optional[Path]:
    if not cfg.ppsd_root:
        return None
    return Path(cfg.ppsd_root).expanduser().resolve()


def list_networks(root: Path) -> List[str]:
    """Return sorted unique network codes from ``<NET>.<STA>`` subdirs."""
    nets = set()
    try:
        for d in root.iterdir():
            if d.is_dir() and "." in d.name:
                net, _, _ = d.name.partition(".")
                if net:
                    nets.add(net)
    except OSError:
        pass
    return sorted(nets)


def list_stations(root: Path, network: str) -> List[str]:
    """Return sorted station codes for one network."""
    stas = []
    prefix = f"{network}."
    try:
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith(prefix):
                sta = d.name[len(prefix):]
                if sta:
                    stas.append(sta)
    except OSError:
        pass
    return sorted(stas)


def list_nslcs(root: Path, network: str,
               station: str) -> List[Tuple[str, str]]:
    """Return sorted unique (LOC, CHA) pairs for one station."""
    station_dir = root / f"{network}.{station}"
    nslcs = set()
    if not station_dir.is_dir():
        return []
    for f in station_dir.glob(f"{network}.{station}.*.npz"):
        stem = f.stem
        prefix = f"{network}.{station}."
        if not stem.startswith(prefix):
            continue
        rest = stem[len(prefix):]
        # rest is "LOC.CHA" with LOC possibly empty (".CHA")
        if "." in rest:
            loc, _, cha = rest.partition(".")
            if cha:
                nslcs.add((loc, cha))
    return sorted(nslcs)


def list_buckets(root: Path, network: str, station: str,
                 loc: str, cha: str, period: str) -> List[dict]:
    """Return a list of bucket dicts for one NSLC/period, newest first.

    Each dict has: ``name`` (bucket key, e.g. ``"2026-05-10"``),
    ``relpath`` (path relative to root, for URL construction), and
    ``mtime`` (int Unix timestamp for change detection).
    """
    period_dir = root / f"{network}.{station}" / period
    if not period_dir.is_dir():
        return []
    file_prefix = f"{network}.{station}.{loc}.{cha}_"
    buckets = []
    for f in period_dir.glob(f"{file_prefix}*.png"):
        stem = f.stem
        if not stem.startswith(file_prefix):
            continue
        bucket_name = stem[len(file_prefix):]
        try:
            mtime = int(f.stat().st_mtime)
        except OSError:
            continue
        buckets.append({
            "name": bucket_name,
            "relpath": f.relative_to(root).as_posix(),
            "mtime": mtime,
        })
    buckets.sort(key=lambda b: b["name"], reverse=True)
    return buckets


def latest_thumbnail_relpath(root: Path, network: str, station: str,
                             loc: str, cha: str) -> Optional[str]:
    """Return the newest PNG (any period) for an NSLC, or None.

    Used for thumbnails on the station listing page. Prefers ``daily`` over
    ``weekly`` over ``monthly`` etc., breaking ties by mtime.
    """
    best: Optional[Tuple[float, str]] = None  # (mtime, relpath)
    for period in _PPSD_PERIODS:
        for b in list_buckets(root, network, station, loc, cha, period):
            if best is None or b["mtime"] > best[0]:
                best = (b["mtime"], b["relpath"])
        if best is not None:
            # Prefer finer-grained period if any results found at this level.
            return best[1]
    return best[1] if best else None


def _safe_file(root: Path, relpath: str) -> Optional[Path]:
    """Resolve ``relpath`` under ``root``, returning None if it escapes."""
    target = (root / relpath).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


# ---------------------------------------------------------------------------
# Flask app factory
# ---------------------------------------------------------------------------

def create_app(cfg: WebConfig):
    """Build the Flask app for the given configuration.

    Imports Flask lazily so the module can be imported without the
    optional dep being installed (e.g. from ``__init__``).
    """
    try:
        from flask import (
            Flask, abort, jsonify, redirect, render_template,
            send_file, url_for,
        )
    except ImportError as e:
        raise SystemExit(
            "Flask is required for the web interface. Install with:\n"
            "    pip install 'seedlink-py-utils[web]'\n"
            "or:\n"
            "    pip install flask"
        ) from e

    app = Flask(__name__)

    # Stash state on the app object so background threads can reach it
    # via app context if needed.
    state = DashboardState() if cfg.dashboard_enabled else None
    if state is not None:
        poller = DashboardPoller(state, cfg)
        poller.start()
        app._poller = poller  # keep a ref to prevent GC

    def _tabs():
        return {
            "dashboard_enabled": cfg.dashboard_enabled,
            "ppsd_enabled": cfg.ppsd_enabled,
        }

    @app.route("/")
    def index():
        if cfg.dashboard_enabled:
            return redirect(url_for("dashboard_view"))
        if cfg.ppsd_enabled:
            return redirect(url_for("ppsd_index"))
        return (
            "<h1>seedlink-py-web</h1>"
            "<p>No tabs are enabled. Pass <code>--server</code> for the "
            "dashboard tab and/or <code>--ppsd-root</code> for the PPSD "
            "browser tab. See <code>seedlink-py-web --help</code>.</p>"
        )

    # ---- Dashboard --------------------------------------------------

    @app.route("/dashboard")
    def dashboard_view():
        if not cfg.dashboard_enabled:
            abort(404)
        rows, polled_at, error = state.snapshot()
        return render_template(
            "dashboard.html",
            rows=rows,
            polled_at=polled_at.isoformat() if polled_at else None,
            error=error,
            server=cfg.server,
            interval=cfg.interval,
            ok_threshold=cfg.ok_threshold,
            stale_threshold=cfg.stale_threshold,
            **_tabs(),
        )

    @app.route("/dashboard/data")
    def dashboard_data():
        if not cfg.dashboard_enabled:
            abort(404)
        rows, polled_at, error = state.snapshot()
        return jsonify({
            "rows": [
                {
                    "network": r["network"],
                    "station": r["station"],
                    "location": r["location"],
                    "channel": r["channel"],
                    "end_time": r["end_time"],
                    "latency_s": r["latency_s"],
                    "status": r["status"],
                }
                for r in rows
            ],
            "polled_at": polled_at.isoformat() if polled_at else None,
            "error": error,
            "counts": _status_counts(rows),
        })

    # ---- PPSD browser -----------------------------------------------

    @app.route("/ppsd")
    def ppsd_index():
        if not cfg.ppsd_enabled:
            abort(404)
        root = _ppsd_root(cfg)
        networks = list_networks(root)
        return render_template(
            "ppsd_index.html",
            networks=networks,
            ppsd_root=str(root),
            **_tabs(),
        )

    @app.route("/ppsd/<net>")
    def ppsd_network(net):
        if not cfg.ppsd_enabled:
            abort(404)
        root = _ppsd_root(cfg)
        stations = list_stations(root, net)
        if not stations:
            abort(404)
        return render_template(
            "ppsd_network.html",
            net=net,
            stations=stations,
            **_tabs(),
        )

    @app.route("/ppsd/<net>/<sta>")
    def ppsd_station(net, sta):
        if not cfg.ppsd_enabled:
            abort(404)
        root = _ppsd_root(cfg)
        nslcs = list_nslcs(root, net, sta)
        if not nslcs:
            abort(404)
        entries = []
        for loc, cha in nslcs:
            thumb = latest_thumbnail_relpath(root, net, sta, loc, cha)
            entries.append({
                "loc": loc,
                "cha": cha,
                "loc_slug": loc if loc else "--",
                "thumbnail_relpath": thumb,
            })
        return render_template(
            "ppsd_station.html",
            net=net,
            sta=sta,
            entries=entries,
            **_tabs(),
        )

    @app.route("/ppsd/<net>/<sta>/<loc>/<cha>")
    def ppsd_nslc(net, sta, loc, cha):
        if not cfg.ppsd_enabled:
            abort(404)
        actual_loc = "" if loc == "--" else loc
        root = _ppsd_root(cfg)
        sections = []
        for period in _PPSD_PERIODS:
            buckets = list_buckets(root, net, sta, actual_loc, cha, period)
            if buckets:
                sections.append({"period": period, "buckets": buckets})
        if not sections:
            abort(404)
        return render_template(
            "ppsd_nslc.html",
            net=net,
            sta=sta,
            loc=actual_loc,
            loc_slug=loc,
            cha=cha,
            sections=sections,
            **_tabs(),
        )

    @app.route("/ppsd/file/<path:relpath>")
    def ppsd_file(relpath):
        if not cfg.ppsd_enabled:
            abort(404)
        root = _ppsd_root(cfg)
        target = _safe_file(root, relpath)
        if target is None or not target.is_file():
            abort(404)
        if target.suffix.lower() != ".png":
            abort(404)
        return send_file(target, mimetype="image/png")

    @app.route("/ppsd/file/<path:relpath>/mtime")
    def ppsd_file_mtime(relpath):
        """Return the file's mtime as JSON. Used by the page to detect
        updates without re-fetching the (potentially large) PNG itself."""
        if not cfg.ppsd_enabled:
            abort(404)
        root = _ppsd_root(cfg)
        target = _safe_file(root, relpath)
        if target is None or not target.is_file():
            abort(404)
        return jsonify({"mtime": int(target.stat().st_mtime)})

    return app


def _status_counts(rows: List[dict]) -> Dict[str, int]:
    out = {"OK": 0, "LAG": 0, "STALE": 0, "UNKNOWN": 0}
    for r in rows:
        s = r.get("status", "UNKNOWN")
        out[s] = out.get(s, 0) + 1
    return out


def run_web(cfg: WebConfig) -> None:
    """Build the Flask app and run the dev server.

    For production use behind a reverse proxy, point at this app via
    ``create_app(cfg)`` and run with gunicorn / waitress.
    """
    app = create_app(cfg)
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug, threaded=True,
            use_reloader=False)
