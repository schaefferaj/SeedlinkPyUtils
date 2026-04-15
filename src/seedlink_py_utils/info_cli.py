"""Command-line interface for SeedLink server INFO queries.

Modelled after SeisComP's ``slinktool``: the same single-letter flags select
which INFO level to query (``-I`` id, ``-L`` stations, ``-Q`` streams,
``-G`` gaps, ``-C`` connections). Output defaults to a human-readable table;
``--json`` or ``--xml`` produce machine-readable variants.
"""

import argparse
import json
import sys
from typing import Dict, List

from .info import (
    filter_records,
    parse_connections,
    parse_gaps,
    parse_id,
    parse_stations,
    parse_streams,
    query_info,
)


# (level, parser) keyed by CLI flag. Mode-specific table formatters live
# in TABLE_FORMATTERS so each query gets a tight, slinktool-style layout.
QUERIES = {
    "id":          ("ID",          parse_id),
    "stations":    ("STATIONS",    parse_stations),
    "streams":     ("STREAMS",     parse_streams),
    "gaps":        ("GAPS",        parse_gaps),
    "connections": ("CONNECTIONS", parse_connections),
}


class _Formatter(argparse.RawDescriptionHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve epilog line breaks while still showing argument defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-info",
        description="Query a SeedLink server for available stations, streams, "
                    "gaps, and active connections — a Python slinktool.",
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-info -I                                     # server id\n"
            "\n"
            "  seedlink-py-info -L                                     # stations\n"
            "\n"
            "  seedlink-py-info -Q --network AM                        # AM streams\n"
            "\n"
            "  seedlink-py-info -Q --station RA382 --json              # JSON output\n"
            "\n"
            "  seedlink-py-info -G rtserve.iris.washington.edu:18000   # gaps on IRIS\n"
            "\n"
            "  seedlink-py-info -C                                     # connections (often redacted)\n"
        ),
    )

    p.add_argument("server", nargs="?", default="seiscomp.hakai.org:18000",
                   help="SeedLink server host:port.")

    # ---- Query type (mutually exclusive, one required) -------------------
    g_query = p.add_argument_group("Query type (exactly one required)")
    mode = g_query.add_mutually_exclusive_group(required=True)
    mode.add_argument("-I", "--id",          dest="mode", action="store_const",
                      const="id",          help="Server ID and version.")
    mode.add_argument("-L", "--stations",    dest="mode", action="store_const",
                      const="stations",    help="List stations.")
    mode.add_argument("-Q", "--streams",     dest="mode", action="store_const",
                      const="streams",     help="List streams (NSLC + time range).")
    mode.add_argument("-G", "--gaps",        dest="mode", action="store_const",
                      const="gaps",        help="List recent gaps (server-dependent).")
    mode.add_argument("-C", "--connections", dest="mode", action="store_const",
                      const="connections", help="List active client connections "
                                                "(often redacted by the server).")

    # ---- Filtering (client-side) -----------------------------------------
    g_filt = p.add_argument_group("Filtering (applies to -L, -Q, -G)")
    g_filt.add_argument("--network", "-n",
                        help="Filter by network code (exact match, case-insensitive).")
    g_filt.add_argument("--station", "-S",
                        help="Filter by station code (exact match, case-insensitive).")

    # ---- Output format ---------------------------------------------------
    g_out = p.add_argument_group("Output format")
    out = g_out.add_mutually_exclusive_group()
    out.add_argument("--json", dest="output", action="store_const", const="json",
                     help="Emit parsed records as JSON.")
    out.add_argument("--xml",  dest="output", action="store_const", const="xml",
                     help="Emit the raw XML response from the server.")
    p.set_defaults(output="table")

    # ---- Connection ------------------------------------------------------
    g_conn = p.add_argument_group("Connection")
    g_conn.add_argument("--timeout", type=float, default=30.0,
                        help="Socket timeout in seconds.")

    return p


def format_streams(rows: List[Dict[str, str]]) -> str:
    """Compact fixed-width streams listing (slinktool-style).

    Layout: ``NN SSSSS LL CCC D <begin> - <end>`` — 2-char network,
    5-char station, 2-char location (blank for empty), 3-char channel,
    1-char type, then the server-supplied begin/end timestamps.
    """
    if not rows:
        return "(no records)"
    lines = []
    for r in rows:
        net = r.get("network", "")[:2].ljust(2)
        sta = r.get("station", "")[:5].ljust(5)
        loc = r.get("location", "")[:2].ljust(2)
        cha = r.get("channel", "")[:3].ljust(3)
        typ = (r.get("type", "") or " ")[:1]
        begin = r.get("begin_time", "")
        end = r.get("end_time", "")
        lines.append(f"{net} {sta} {loc} {cha} {typ} {begin} - {end}")
    return "\n".join(lines)


def format_stations(rows: List[Dict[str, str]]) -> str:
    """Compact fixed-width stations listing.

    Layout: ``NN SSSSS <description>`` — 2-char network, 5-char station,
    then the server-supplied description (variable length).
    """
    if not rows:
        return "(no records)"
    lines = []
    for r in rows:
        net = r.get("network", "")[:2].ljust(2)
        sta = r.get("station", "")[:5].ljust(5)
        desc = r.get("description", "")
        lines.append(f"{net} {sta} {desc}".rstrip())
    return "\n".join(lines)


def format_gaps(rows: List[Dict[str, str]]) -> str:
    """Compact gaps listing. Fields besides NSLC vary by server, so we put
    the canonical ``NN SSSSS LL CCC`` prefix first and append remaining
    attributes as ``key=value`` pairs.
    """
    if not rows:
        return "(no records)"
    nslc_keys = {"network", "station", "location", "channel"}
    lines = []
    for r in rows:
        net = r.get("network", "")[:2].ljust(2)
        sta = r.get("station", "")[:5].ljust(5)
        loc = r.get("location", "")[:2].ljust(2)
        cha = r.get("channel", "")[:3].ljust(3)
        extras = " ".join(f"{k}={v}" for k, v in r.items() if k not in nslc_keys)
        lines.append(f"{net} {sta} {loc} {cha} {extras}".rstrip())
    return "\n".join(lines)


def format_connections(rows: List[Dict[str, str]]) -> str:
    """Compact connections listing. Fields are entirely server-dependent
    (and often redacted), so we just emit one row per connection as
    space-separated ``key=value`` pairs.
    """
    if not rows:
        return "(no records)"
    return "\n".join(" ".join(f"{k}={v}" for k, v in r.items()) for r in rows)


def format_id(d: Dict[str, str]) -> str:
    if not d:
        return "(empty response)"
    width = max(len(k) for k in d)
    return "\n".join(f"{k.ljust(width)} : {v}" for k, v in d.items())


TABLE_FORMATTERS = {
    "stations":    format_stations,
    "streams":     format_streams,
    "gaps":        format_gaps,
    "connections": format_connections,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    level, parser_fn = QUERIES[args.mode]

    try:
        xml = query_info(args.server, level=level, timeout=args.timeout)
    except Exception as e:
        print(f"Query failed: {e}", file=sys.stderr)
        return 1

    if args.output == "xml":
        print(xml)
        return 0

    parsed = parser_fn(xml)

    # ID is a single dict; everything else is a list of dicts.
    if args.mode == "id":
        if args.output == "json":
            print(json.dumps(parsed, indent=2))
        else:
            print(format_id(parsed))
        return 0

    if args.network or args.station:
        parsed = filter_records(parsed, network=args.network, station=args.station)

    if args.output == "json":
        print(json.dumps(parsed, indent=2))
    else:
        print(TABLE_FORMATTERS[args.mode](parsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
