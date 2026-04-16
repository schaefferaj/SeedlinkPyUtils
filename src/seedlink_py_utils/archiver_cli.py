"""Command-line interface for the SeedLink-to-SDS archiver."""

import argparse
import logging

from .archiver import run_archiver
from .logging_setup import setup_logger


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
            "  seedlink-py-archiver CN.PGC..HH? PQ.DAOB..HH? \\\n"
            "      --archive /data/sds \\\n"
            "      --state-file /var/lib/slarchiver/state.txt \\\n"
            "      --log-file /var/log/slarchiver.log\n"
            "\n"
            "  seedlink-py-archiver 'PQ.*..HH?' --archive /data/sds --expand-wildcards\n"
            "\n"
            "Stream syntax: NET.STA.LOC.CHA, with ? / * wildcards allowed in LOC and CHA\n"
            "natively. Wildcards in NET or STA require --expand-wildcards (one extra\n"
            "INFO=STREAMS query at startup). Empty LOC is written as two dots,\n"
            "e.g. PQ.DAOB..HHZ."
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
    )


if __name__ == "__main__":
    main()
