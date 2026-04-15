"""Command-line interface for the SeedLink-to-SDS archiver."""

import argparse
import logging

from .archiver import run_archiver
from .logging_setup import setup_logger


class _Formatter(argparse.RawDescriptionHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve epilog line breaks while still showing argument defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-archiver",
        description="Archive real-time SeedLink streams into an SDS miniSEED archive.",
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-archiver AM.RA382..EH? --archive /data/sds\n"
            "\n"
            "  seedlink-py-archiver AM.RA382..EH? AM.RA481..EH? PQ.DAOB..HH? \\\n"
            "      --server seiscomp.hakai.org:18000 \\\n"
            "      --archive /data/sds \\\n"
            "      --state-file /var/lib/slarchiver/state.txt \\\n"
            "      --log-file /var/log/slarchiver.log\n"
            "\n"
            "  seedlink-py-archiver 'AM.*..EH?' --archive /data/sds --expand-wildcards\n"
            "\n"
            "Stream syntax: NET.STA.LOC.CHA, with ? / * wildcards allowed in LOC and CHA\n"
            "natively. Wildcards in NET or STA require --expand-wildcards (one extra\n"
            "INFO=STREAMS query at startup). Empty LOC is written as two dots,\n"
            "e.g. PQ.DAOB..HHZ."
        ),
    )

    p.add_argument("streams", nargs="+",
                   help="One or more streams in NET.STA.LOC.CHA form "
                        "(wildcards ? / * allowed in LOC/CHA).")

    p.add_argument("--server", "-s", default="seiscomp.hakai.org:18000",
                   help="SeedLink server host:port.")
    p.add_argument("--archive", "-a", required=True,
                   help="Root directory of the SDS archive.")
    p.add_argument("--state-file",
                   help="Path to the SeedLink state file (enables resume on restart).")

    # Time window (for replay from server buffer)
    p.add_argument("--begin-time",
                   help="Replay start time (ISO 8601, e.g. '2026-04-14T12:00:00'). "
                        "If set, operates in dial-up mode instead of real-time.")
    p.add_argument("--end-time",
                   help="Replay end time (ISO 8601). Requires --begin-time.")

    # Reconnection
    p.add_argument("--reconnect-wait", type=float, default=10.0,
                   help="Seconds to wait between reconnection attempts.")
    p.add_argument("--max-reconnects", type=int, default=None,
                   help="Maximum number of reconnect attempts (default: unlimited).")

    # Wildcards
    p.add_argument("--expand-wildcards", action="store_true",
                   help="Expand ? / * in NET and STA fields by querying the "
                        "server's INFO=STREAMS at startup. Quote the spec to "
                        "stop the shell from globbing it (e.g. 'AM.*..EH?').")

    # Logging
    p.add_argument("--log-file",
                   help="Path to a rotating log file (10 MB × 5 backups).")
    p.add_argument("--log-level", default="INFO",
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
