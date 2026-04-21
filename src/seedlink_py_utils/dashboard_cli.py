"""CLI for the SeedLink stream availability dashboard.

Live operator view: polls the server's ``INFO=STREAMS`` on a schedule and
renders a coloured table of per-NSLC latency with OK / LAG / STALE
classification. Complements the one-shot ``seedlink-py-info`` — that's
the snapshot; this is the dashboard.
"""

import argparse
import sys

from .dashboard import DashboardConfig, run_dashboard


class _Formatter(argparse.RawDescriptionHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve epilog line breaks while still showing argument defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-dashboard",
        description=(
            "Live stream availability dashboard for a SeedLink server. Polls "
            "INFO=STREAMS every --interval seconds and shows per-NSLC "
            "latency (wall-clock time since the server's last packet for "
            "that stream) with OK / LAG / STALE classification. Exit with "
            "Ctrl-C."
        ),
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-dashboard                                  # default server (IRIS)\n"
            "\n"
            "  seedlink-py-dashboard --network PQ --interval 10       # just PQ stations, fast poll\n"
            "\n"
            "  seedlink-py-dashboard --station ANMO                   # one station across channels\n"
            "\n"
            "  seedlink-py-dashboard --channel BHZ                    # verticals only — one row/station\n"
            "\n"
            "  seedlink-py-dashboard -n CN -c 'HH?'                   # wildcards in the channel filter\n"
            "\n"
            "  seedlink-py-dashboard --sort-by-status                 # STALE at the top, OK at the bottom\n"
            "\n"
            "  seedlink-py-dashboard --once                           # single snapshot (scriptable)\n"
            "\n"
            "  seedlink-py-dashboard --ok-threshold 30 \\\n"
            "      --stale-threshold 300                              # tighter thresholds\n"
            "\n"
            "Status bands (defaults): OK <60s, LAG 60-600s, STALE >600s, UNKNOWN if the\n"
            "server's end_time for a stream is empty or unparseable."
        ),
    )

    p.add_argument("server", nargs="?", default="rtserve.iris.washington.edu:18000",
                   help="SeedLink server host:port.")

    g_poll = p.add_argument_group("Polling")
    g_poll.add_argument("--interval", type=float, default=30.0, metavar="SEC",
                        help="Poll interval in seconds.")
    g_poll.add_argument("--once", action="store_true",
                        help="Run one poll and exit. Disables screen-clearing "
                             "so the output is suitable for logs / cron.")
    g_poll.add_argument("--timeout", type=float, default=30.0, metavar="SEC",
                        help="Per-poll socket timeout in seconds.")

    g_thr = p.add_argument_group("Status thresholds")
    g_thr.add_argument("--ok-threshold", type=float, default=60.0, metavar="SEC",
                       help="Latency below this is OK (green).")
    g_thr.add_argument("--stale-threshold", type=float, default=600.0,
                       metavar="SEC",
                       help="Latency above this is STALE (red). Between\n"
                            "--ok-threshold and --stale-threshold is LAG (yellow).")

    g_filt = p.add_argument_group("Filtering (client-side)")
    g_filt.add_argument("--network", "-n", default=None,
                        help="Filter by network code (exact match, case-insensitive).")
    g_filt.add_argument("--station", "-S", default=None,
                        help="Filter by station code (exact match, case-insensitive).")
    g_filt.add_argument("--channel", "-c", default=None, metavar="CHA",
                        help="Filter by channel code. Supports SeedLink-style\n"
                             "wildcards (? and *), case-insensitive. Examples:\n"
                             "  EHZ    one row per station for Shake verticals\n"
                             "  HH?    all HH-band channels\n"
                             "  '*Z'   all verticals regardless of band code\n"
                             "Quote the pattern in shells that glob * / ?.")

    g_alert = p.add_argument_group("Alerting")
    g_alert.add_argument("--alert", action="store_true",
                         help="Enable transition alerts: log + optional webhook\n"
                              "when an NSLC transitions to/from STALE. Implied\n"
                              "if --webhook is set.")
    g_alert.add_argument("--webhook",
                         help="Slack-compatible incoming-webhook URL. Fires on\n"
                              "STALE transitions and recoveries. LAG <-> OK\n"
                              "transitions are logged only (not webhoooked).\n"
                              "See docs/slack-webhook.md for setup.")
    g_alert.add_argument("--webhook-timeout", type=float, default=10.0,
                         metavar="SEC",
                         help="Per-request timeout for the webhook POST.")
    g_alert.add_argument("--hostname",
                         help="Label used in alert text (default: host FQDN).")

    g_out = p.add_argument_group("Output")
    g_out.add_argument("--sort-by-status", dest="sort_by_status",
                       action="store_true", default=False,
                       help="Group rows by status to focus attention on problems:\n"
                            "STALE first, then LAG, UNKNOWN, OK last. Within each\n"
                            "group rows are sorted alphabetically by NSLC.\n"
                            "Default: alphabetical by NSLC only.")
    g_out.add_argument("--no-color", dest="color", action="store_false",
                       default=True,
                       help="Disable ANSI colour escapes. Auto-disabled when\n"
                            "stdout is not a TTY regardless of this flag.")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.ok_threshold >= args.stale_threshold:
        print("--ok-threshold must be strictly less than --stale-threshold",
              file=sys.stderr)
        return 2

    cfg = DashboardConfig(
        server=args.server,
        interval=args.interval,
        ok_threshold=args.ok_threshold,
        stale_threshold=args.stale_threshold,
        network=args.network,
        station=args.station,
        channel=args.channel,
        color=args.color,
        once=args.once,
        timeout=args.timeout,
        sort_by_status=args.sort_by_status,
        alert=args.alert,
        webhook_url=args.webhook,
        webhook_timeout=args.webhook_timeout,
        hostname=args.hostname,
    )
    run_dashboard(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
