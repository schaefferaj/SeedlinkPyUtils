"""CLI entry point for the web interface.

``seedlink-py-web`` exposes a local web UI that complements the existing
CLIs. It supports two tabs:

- **Dashboard** (``--server``) — live SeedLink stream-availability table,
  same data as ``seedlink-py-dashboard`` but in a browser.
- **PPSD browser** (``--ppsd-root``) — read-only navigation of the PNG
  archive produced by ``seedlink-py-ppsd-archive``.

At least one of ``--server`` or ``--ppsd-root`` must be supplied; both is
fine and gives you both tabs at the same URL.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .logging_setup import setup_logger
from .web import WebConfig, run_web


class _Formatter(argparse.RawDescriptionHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve epilog line breaks while still showing argument defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-web",
        description=(
            "Local web UI for SeedlinkPyUtils. Exposes a live dashboard "
            "(--server) and/or a PPSD archive browser (--ppsd-root) on a "
            "local HTTP port. Open the printed URL in a browser."
        ),
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  # Both tabs: live dashboard against IRIS + PPSD archive browser\n"
            "  seedlink-py-web --server rtserve.iris.washington.edu:18000 \\\n"
            "      --ppsd-root /data/ppsd\n"
            "\n"
            "  # Dashboard only, faster polling, focused on one network\n"
            "  seedlink-py-web --server rs.local:18000 --interval 10 --network AM\n"
            "\n"
            "  # PPSD browser only\n"
            "  seedlink-py-web --ppsd-root /data/ppsd\n"
            "\n"
            "  # LAN-visible (default is localhost only)\n"
            "  seedlink-py-web --server rs.local:18000 --host 0.0.0.0 --port 8888\n"
        ),
    )

    g_srv = p.add_argument_group("Web server")
    g_srv.add_argument("--host", default="127.0.0.1",
                       help="Network interface to bind. Use 0.0.0.0 to expose\n"
                            "the UI to your LAN; 127.0.0.1 keeps it local only.")
    g_srv.add_argument("--port", type=int, default=8080,
                       help="TCP port to bind.")
    g_srv.add_argument("--debug", action="store_true",
                       help="Enable Flask debug mode (verbose errors,\n"
                            "auto-reload on code change). Development only.")

    g_dash = p.add_argument_group("Dashboard tab (--server enables)")
    g_dash.add_argument("--server",
                       help="SeedLink server host:port. If unset, the\n"
                            "dashboard tab is disabled.")
    g_dash.add_argument("--interval", type=float, default=30.0, metavar="SEC",
                       help="Server poll interval (seconds).")
    g_dash.add_argument("--timeout", type=float, default=30.0, metavar="SEC",
                       help="Per-poll socket timeout.")
    g_dash.add_argument("--ok-threshold", type=float, default=60.0,
                       metavar="SEC",
                       help="Latency below this is OK (green).")
    g_dash.add_argument("--stale-threshold", type=float, default=600.0,
                       metavar="SEC",
                       help="Latency above this is STALE (red); between is\n"
                            "LAG (yellow).")
    g_dash.add_argument("--network", "-n",
                       help="Filter by network code. Accepts a single code\n"
                            "or a comma-separated list (e.g. 'PQ,NY').")
    g_dash.add_argument("--station", "-S",
                       help="Filter by station code. Comma-separated list ok.")
    g_dash.add_argument("--channel", "-c",
                       help="Filter by channel code. Supports ? / * wildcards\n"
                            "(e.g. 'EHZ', 'HH?', '*Z').")
    g_dash.add_argument("--sort-by-status", action="store_true",
                       help="Group rows by status (STALE first) instead of\n"
                            "alphabetical NSLC ordering.")

    g_ppsd = p.add_argument_group("PPSD browser tab (--ppsd-root enables)")
    g_ppsd.add_argument("--ppsd-root",
                       help="Root directory written by seedlink-py-ppsd-archive\n"
                            "(contains <NET>.<STA>/ subdirs). If unset, the\n"
                            "PPSD tab is disabled.")

    g_log = p.add_argument_group("Logging")
    g_log.add_argument("--log-file",
                       help="Optional rotating log file (10 MB x 5 backups).")
    g_log.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not args.server and not args.ppsd_root:
        print(
            "Error: at least one of --server (for the dashboard tab) or\n"
            "--ppsd-root (for the PPSD browser tab) must be supplied.\n"
            "See seedlink-py-web --help.",
            file=sys.stderr,
        )
        return 2

    setup_logger(
        name="seedlink_py_utils",
        log_file=args.log_file,
        level=getattr(logging, args.log_level),
    )

    cfg = WebConfig(
        host=args.host,
        port=args.port,
        server=args.server,
        interval=args.interval,
        timeout=args.timeout,
        ok_threshold=args.ok_threshold,
        stale_threshold=args.stale_threshold,
        network=args.network,
        station=args.station,
        channel=args.channel,
        sort_by_status=args.sort_by_status,
        ppsd_root=args.ppsd_root,
        debug=args.debug,
    )

    print(f"seedlink-py-web: serving on http://{cfg.host}:{cfg.port}/")
    if cfg.dashboard_enabled:
        print(f"  dashboard: {cfg.server} (poll every {cfg.interval:.0f}s)")
    if cfg.ppsd_enabled:
        print(f"  ppsd: {cfg.ppsd_root}")
    print("Press Ctrl-C to stop.")

    try:
        run_web(cfg)
    except KeyboardInterrupt:
        print("\nshutting down.")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
