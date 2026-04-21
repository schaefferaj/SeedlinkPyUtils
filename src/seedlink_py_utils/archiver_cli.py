"""Command-line interface for the SeedLink-to-SDS archiver."""

import argparse
import logging

from .archiver import run_archiver
from .logging_setup import setup_logger
from .monitor import MonitorConfig


class _Formatter(argparse.RawTextHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve line breaks in argument help and epilog while still showing defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-archiver",
        description="Archive real-time SeedLink streams into an SDS miniSEED archive.",
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-archiver IU.ANMO.00.BH? --archive /data/sds\n"
            "\n"
            "  seedlink-py-archiver CN.PGC..HH? CN.NLLB..HH? \\\n"
            "      --archive /data/sds \\\n"
            "      --state-file /var/lib/slarchiver/state.txt \\\n"
            "      --log-file /var/log/slarchiver.log\n"
            "\n"
            "  seedlink-py-archiver 'CN.*..HH?' --archive /data/sds --expand-wildcards\n"
            "\n"
            "Stream syntax: NET.STA.LOC.CHA, with ? / * wildcards allowed in LOC and CHA\n"
            "natively. Wildcards in NET or STA require --expand-wildcards (one extra\n"
            "INFO=STREAMS query at startup). Empty LOC is written as two dots,\n"
            "e.g. CN.PGC..HHZ."
        ),
    )

    p.add_argument("streams", nargs="+",
                   help="One or more streams in NET.STA.LOC.CHA form,\n"
                        "e.g. IU.ANMO.00.BHZ (wildcards ? / * allowed in LOC/CHA).")

    # ---- Server & output --------------------------------------------------
    g_io = p.add_argument_group("Server & output")
    g_io.add_argument("--server", "-s", default="rtserve.iris.washington.edu:18000",
                      help="SeedLink server host:port.")
    g_io.add_argument("--archive", "-a", required=True,
                      help="Root directory of the SDS archive.")
    g_io.add_argument("--state-file",
                      help="Path to the SeedLink state file "
                           "(enables resume on restart).")

    # ---- Time window (replay / dial-up) ----------------------------------
    g_time = p.add_argument_group("Time window (replay from server buffer)")
    g_time.add_argument("--begin-time",
                        help="Replay start time (ISO 8601, "
                             "e.g. '2026-04-14T12:00:00'). If set, operates in\n"
                             "dial-up mode instead of real-time.")
    g_time.add_argument("--end-time",
                        help="Replay end time (ISO 8601). Requires --begin-time.")

    # ---- Reconnection -----------------------------------------------------
    g_rc = p.add_argument_group("Reconnection")
    g_rc.add_argument("--reconnect-wait", type=float, default=10.0,
                      help="Seconds to wait between reconnection attempts.")
    g_rc.add_argument("--max-reconnects", type=int, default=None,
                      help="Maximum number of reconnect attempts "
                           "(default: unlimited).")

    # ---- Wildcards --------------------------------------------------------
    g_wild = p.add_argument_group("Wildcards")
    g_wild.add_argument("--expand-wildcards", action="store_true",
                        help="Expand ? / * in NET and STA fields by querying the\n"
                             "server's INFO=STREAMS at startup. Quote the spec\n"
                             "to stop the shell from globbing it (e.g. 'AM.*..EH?').")

    # ---- Monitoring -------------------------------------------------------
    g_mon = p.add_argument_group("Monitoring (stale-stream watchdog)")
    g_mon.add_argument("--monitor", action="store_true",
                       help="Enable the per-NSLC stale-stream watchdog.\n"
                            "Alerts go to the logger (always) and optionally\n"
                            "to a Slack-compatible webhook. See\n"
                            "docs/slack-webhook.md for webhook setup and\n"
                            "docs/systemd.md for pairing with systemd auto-\n"
                            "restart.")
    g_mon.add_argument("--stale-timeout", type=float, default=300.0,
                       help="Seconds without a packet before an NSLC is\n"
                            "classified STALE and alerted.")
    g_mon.add_argument("--monitor-interval", type=float, default=60.0,
                       help="Seconds between watchdog checks. Must be less\n"
                            "than --stale-timeout.")
    g_mon.add_argument("--webhook",
                       help="Slack-compatible incoming-webhook URL. The\n"
                            "watcher POSTs a JSON body with 'text' (human-\n"
                            "readable) and structured fields (event, nslc,\n"
                            "age_seconds, ...). Slack ignores unknown fields.")
    g_mon.add_argument("--webhook-timeout", type=float, default=10.0,
                       help="Per-request timeout for the webhook POST.")
    g_mon.add_argument("--exit-on-all-stale", action="store_true",
                       help="Exit with status 2 when every registered NSLC\n"
                            "is STALE. Intended to pair with a systemd\n"
                            "Restart=on-failure unit.")
    g_mon.add_argument("--hostname",
                       help="Label used in alert text (default: host FQDN).")

    # ---- Logging ----------------------------------------------------------
    g_log = p.add_argument_group("Logging")
    g_log.add_argument("--log-file",
                       help="Path to a rotating log file (10 MB × 5 backups).")
    g_log.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Logging verbosity.")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    setup_logger(
        name="seedlink_py_utils",
        log_file=args.log_file,
        level=getattr(logging, args.log_level),
    )

    monitor_config = None
    if args.monitor or args.webhook:
        if args.monitor_interval >= args.stale_timeout:
            raise SystemExit(
                "--monitor-interval must be less than --stale-timeout "
                f"({args.monitor_interval} >= {args.stale_timeout})."
            )
        monitor_config = MonitorConfig(
            stale_timeout=args.stale_timeout,
            check_interval=args.monitor_interval,
            webhook_url=args.webhook,
            webhook_timeout=args.webhook_timeout,
            exit_on_all_stale=args.exit_on_all_stale,
            hostname=args.hostname,
        )

    run_archiver(
        server=args.server,
        streams=args.streams,
        archive_root=args.archive,
        state_file=args.state_file,
        begin_time=args.begin_time,
        end_time=args.end_time,
        reconnect_wait=args.reconnect_wait,
        max_reconnects=args.max_reconnects,
        expand_wildcards=args.expand_wildcards,
        monitor_config=monitor_config,
    )


if __name__ == "__main__":
    main()
