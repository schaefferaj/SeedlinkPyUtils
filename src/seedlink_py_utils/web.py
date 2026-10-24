"""Web interface for the dashboard and PPSD archive.

Provides a local web UI with two tabs:

- ``/dashboard`` — live SeedLink stream availability table, mirroring
  ``seedlink-py-dashboard`` but in a browser. A background thread polls
  ``INFO=STREAMS`` at the configured interval and the page auto-refreshes
  via JS poll. Several servers can be watched at once (one poller thread
  each); the page then shows an "All" sub-tab merging every server's rows
  with a Source column, plus one sub-tab per server.
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
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from obspy import UTCDateTime

from .dashboard import (
    DashboardAlerter,
    DashboardConfig,
    _sort_key,
    _sort_key_by_status,
    _worst_status,
    compute_rows,
    filter_by_channel,
)
from .info import filter_records, parse_streams, query_info


log = logging.getLogger("seedlink_py_utils.web")


# PPSD period subdirectories produced by seedlink-py-ppsd-archive.
_PPSD_PERIODS = ("daily", "weekly", "monthly", "quarterly", "yearly")


@dataclass
class ServerSpec:
    """One SeedLink server watched by the dashboard tab.

    ``label`` is the short name used for the tab, the "Source" column on
    the aggregated view, and alert text. The filters are the *effective*
    ones for this server (global value, or a ``LABEL=`` override).
    """

    label: str
    address: str
    network: Optional[str] = None
    station: Optional[str] = None
    channel: Optional[str] = None


_LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _split_scoped(value: str) -> Tuple[Optional[str], str]:
    """Split ``"LABEL=VALUE"`` into ``(LABEL, VALUE)``; plain → ``(None, value)``.

    SeedLink addresses, network/station codes and channel globs never
    contain ``=``, so its presence unambiguously marks a label.
    """
    if "=" in value:
        label, _, rest = value.partition("=")
        return label.strip(), rest.strip()
    return None, value.strip()


def _default_label(address: str) -> str:
    host = address.rsplit(":", 1)[0] if ":" in address else address
    if host.replace(".", "").isdigit():
        return host  # bare IPv4: keep the whole thing
    return host.split(".", 1)[0] or address


def parse_server_specs(
    servers: Sequence[str],
    networks: Sequence[str] = (),
    stations: Sequence[str] = (),
    channels: Sequence[str] = (),
) -> List[ServerSpec]:
    """Build :class:`ServerSpec` list from repeatable CLI values.

    ``servers`` entries are ``HOST:PORT`` or ``LABEL=HOST:PORT``. Filter
    entries are ``VALUE`` (applies to every server) or ``LABEL=VALUE``
    (that server only, replacing the global value for that field).

    Raises ``ValueError`` with a user-facing message on bad input.
    """
    specs: List[ServerSpec] = []
    explicit = set()
    for raw in servers:
        label, address = _split_scoped(raw)
        if not address:
            raise ValueError(f"--server {raw!r}: missing HOST:PORT")
        if label is not None:
            if not _LABEL_RE.match(label):
                raise ValueError(
                    f"--server {raw!r}: label must be letters, digits, "
                    f"'.', '_' or '-'")
            explicit.add(label)
        specs.append(ServerSpec(label=label or "", address=address))

    # Default labels: first DNS component, falling back to the full
    # address when that would collide with another server.
    for sp in specs:
        if not sp.label:
            sp.label = _default_label(sp.address)
    seen: Dict[str, int] = {}
    for sp in specs:
        seen[sp.label] = seen.get(sp.label, 0) + 1
    for sp in specs:
        if seen[sp.label] > 1 and sp.label not in explicit:
            sp.label = sp.address
    labels = [sp.label for sp in specs]
    dupes = sorted({lb for lb in labels if labels.count(lb) > 1})
    if dupes:
        raise ValueError(
            f"duplicate server label(s): {', '.join(dupes)}. "
            f"Give each --server a unique LABEL=HOST:PORT.")

    by_label = {sp.label: sp for sp in specs}
    for flag, field_name, values in (("--network", "network", networks),
                                     ("--station", "station", stations),
                                     ("--channel", "channel", channels)):
        global_val: Optional[str] = None
        scoped: Dict[str, str] = {}
        for raw in values:
            label, val = _split_scoped(raw)
            if label is None:
                if global_val is not None:
                    raise ValueError(
                        f"{flag} given twice without a label "
                        f"({global_val!r}, {val!r}); combine them as a "
                        f"comma-separated list or scope one with LABEL=")
                global_val = val
            else:
                if label not in by_label:
                    raise ValueError(
                        f"{flag} {raw!r}: no server labelled {label!r} "
                        f"(known: {', '.join(labels)})")
                if label in scoped:
                    raise ValueError(f"{flag} given twice for {label!r}")
                scoped[label] = val
        for sp in specs:
            setattr(sp, field_name, scoped.get(sp.label, global_val) or None)
    return specs


@dataclass
class WebConfig:
    """Runtime configuration for the web interface."""

    host: str = "127.0.0.1"
    port: int = 8080

    # Dashboard tab — disabled when no servers are configured. ``servers``
    # is the multi-server form; the single-server ``server`` / ``network``
    # / ``station`` / ``channel`` fields are kept for library callers and
    # folded into ``servers`` when ``servers`` is empty.
    servers: List[ServerSpec] = field(default_factory=list)
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

    # Alerting (dashboard tab only). Reuses DashboardAlerter so the
    # behaviour matches seedlink-py-dashboard --alert exactly. The poll
    # cycle is shared with the browser — no extra SeedLink traffic. With
    # several servers each gets its own alerter, tagged with its label.
    alert: bool = False
    webhook_url: Optional[str] = None
    webhook_timeout: float = 10.0
    hostname: Optional[str] = None
    alert_settle: int = 0

    debug: bool = False

    def __post_init__(self) -> None:
        if not self.servers and self.server:
            self.servers = [ServerSpec(
                label=_default_label(self.server), address=self.server,
                network=self.network, station=self.station,
                channel=self.channel,
            )]

    @property
    def dashboard_enabled(self) -> bool:
        return bool(self.servers)

    @property
    def multi_server(self) -> bool:
        return len(self.servers) > 1

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
    server polling frequency. One poller runs per :class:`ServerSpec`.
    """

    def __init__(self, state: DashboardState, cfg: WebConfig,
                 spec: Optional[ServerSpec] = None):
        self.state = state
        self.cfg = cfg
        self.spec = spec if spec is not None else cfg.servers[0]
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Build the DashboardConfig shim once. compute_rows only reads
        # ok_threshold / stale_threshold; DashboardAlerter reads the
        # alert-related fields. Reused on every poll.
        self._dash_cfg = DashboardConfig(
            ok_threshold=cfg.ok_threshold,
            stale_threshold=cfg.stale_threshold,
            alert=cfg.alert,
            webhook_url=cfg.webhook_url,
            webhook_timeout=cfg.webhook_timeout,
            hostname=cfg.hostname,
            alert_settle=cfg.alert_settle,
        )
        # Only tag alerts with the source when there is more than one, so
        # single-server alert text is unchanged.
        self._alerter: Optional[DashboardAlerter] = (
            DashboardAlerter(self._dash_cfg,
                             source=self.spec.label if cfg.multi_server
                             else None)
            if (cfg.alert or cfg.webhook_url) else None
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name=f"dashboard-poller-{self.spec.label}",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "dashboard-poller started: %s=%s interval=%.0fs",
            self.spec.label, self.spec.address, self.cfg.interval,
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
                log.warning("dashboard poll failed (%s): %s",
                            self.spec.label, e)
                self.state.set_error(str(e))
            if self._stop.wait(self.cfg.interval):
                break

    def _poll_once(self) -> None:
        sp = self.spec
        xml = query_info(sp.address, level="STREAMS",
                         timeout=self.cfg.timeout)
        records = parse_streams(xml)
        if sp.network or sp.station:
            records = filter_records(
                records, network=sp.network, station=sp.station,
            )
        if sp.channel:
            records = filter_by_channel(records, sp.channel)
        now = UTCDateTime()
        rows = compute_rows(records, now, self._dash_cfg)
        rows.sort(key=_sort_key_by_status if self.cfg.sort_by_status
                  else _sort_key)
        if self._alerter is not None:
            self._alerter.update(rows)
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

    # One state + poller per server, keyed by label. Insertion order is
    # the --server order, which is also the tab order.
    states: Dict[str, DashboardState] = {}
    app._pollers = []  # keep refs to prevent GC
    for spec in cfg.servers:
        states[spec.label] = DashboardState()
        poller = DashboardPoller(states[spec.label], cfg, spec)
        poller.start()
        app._pollers.append(poller)

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
        data = dashboard_payload(cfg, states)
        title = (cfg.servers[0].address if not cfg.multi_server
                 else f"{len(cfg.servers)} servers")
        return render_template(
            "dashboard.html",
            data=data,
            title=title,
            multi=cfg.multi_server,
            servers=cfg.servers,
            interval=cfg.interval,
            ok_threshold=cfg.ok_threshold,
            stale_threshold=cfg.stale_threshold,
            **_tabs(),
        )

    @app.route("/dashboard/data")
    def dashboard_data():
        if not cfg.dashboard_enabled:
            abort(404)
        return jsonify(dashboard_payload(cfg, states))

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


def _json_row(r: dict, source: str) -> dict:
    return {
        "source": source,
        "network": r["network"],
        "station": r["station"],
        "location": r["location"],
        "channel": r["channel"],
        "end_time": r["end_time"],
        "latency_s": r["latency_s"],
        "status": r["status"],
    }


def dashboard_payload(cfg: WebConfig,
                      states: Dict[str, DashboardState]) -> dict:
    """Build the ``/dashboard/data`` body from every server's cached state.

    Top-level ``rows`` / ``counts`` / ``polled_at`` / ``error`` describe the
    aggregate (identical to the single-server shape when there is one
    server). ``sources`` carries the per-server breakdown the tabs use.
    Rows are never de-duplicated across servers: the same NSLC relayed by
    two servers appears twice, tagged by ``source``, because their
    latencies legitimately differ.
    """
    sort_key = _sort_key_by_status if cfg.sort_by_status else _sort_key
    sources = []
    merged: List[Tuple[tuple, int, dict]] = []
    polled: List[UTCDateTime] = []
    errors: List[str] = []
    for idx, spec in enumerate(cfg.servers):
        rows, polled_at, error = states[spec.label].snapshot()
        counts = _status_counts(rows)
        sources.append({
            "label": spec.label,
            "server": spec.address,
            "network": spec.network,
            "station": spec.station,
            "channel": spec.channel,
            "polled_at": polled_at.isoformat() if polled_at else None,
            "error": error,
            "counts": counts,
            "worst": (_worst_status(r["status"] for r in rows)
                      if rows else ("STALE" if error else None)),
            "n_rows": len(rows),
        })
        if polled_at:
            polled.append(polled_at)
        if error:
            errors.append(f"{spec.label}: {error}" if cfg.multi_server
                          else error)
        for r in rows:
            merged.append((sort_key(r), idx, _json_row(r, spec.label)))
    # Server order breaks ties so duplicate NSLCs sit side by side.
    merged.sort(key=lambda t: (t[0], t[1]))
    rows_out = [t[2] for t in merged]
    return {
        "multi": cfg.multi_server,
        "rows": rows_out,
        "polled_at": max(polled).isoformat() if polled else None,
        "error": "; ".join(errors) or None,
        "counts": _status_counts(rows_out),
        "sources": sources,
    }


def run_web(cfg: WebConfig) -> None:
    """Build the Flask app and run the dev server.

    For production use behind a reverse proxy, point at this app via
    ``create_app(cfg)`` and run with gunicorn / waitress.
    """
    app = create_app(cfg)
    app.run(host=cfg.host, port=cfg.port, debug=cfg.debug, threaded=True,
            use_reloader=False)
