"""Command-line interface for the real-time PPSD monitor."""

import argparse
import sys

from .cli import parse_nslc
from .ppsd import PPSDConfig, run_ppsd


class _Formatter(argparse.RawTextHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve line breaks in argument help text while still showing defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-ppsd",
        description=(
            "Real-time Probabilistic Power Spectral Density (PPSD) monitor. "
            "Feeds a live SeedLink stream into obspy.signal.PPSD and "
            "re-renders the accumulating 2-D histogram with Peterson's "
            "NLNM/NHNM noise models overlaid. Requires an instrument "
            "response (FDSN or local StationXML) — PPSD is unitless "
            "without it."
        ),
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-ppsd IU.ANMO.00.BHZ                      # defaults (IRIS + EarthScope)\n"
            "\n"
            "  seedlink-py-ppsd CN.PGC..HHZ --dark-mode --fullscreen\n"
            "\n"
            "  seedlink-py-ppsd CN.PGC..HHZ --max-hours 24          # sliding 24h window\n"
            "\n"
            "  seedlink-py-ppsd IU.ANMO.00.BHZ --inventory station.xml \\\n"
            "      --backfill-hours 6                               # populate quickly from local XML\n"
            "\n"
            "Note: PPSD follows McNamara & Buland (2004) with 3600 s segments\n"
            "and 50 %% overlap by default — so the first PSD may take up to an\n"
            "hour of wall-clock time to land (backfill from the server's ring\n"
            "buffer is best-effort and often delivers only minutes, not hours).\n"
            "For faster feedback while testing, pass --ppsd-length 300 (5 min\n"
            "segments); but non-standard segments break comparability with\n"
            "the overlaid NLNM/NHNM noise models."
        ),
    )

    p.add_argument("stream", type=parse_nslc,
                   help="Stream in NET.STA.LOC.CHA format "
                        "(e.g. IU.ANMO.00.BHZ or CN.PGC..HHZ).")

    # ---- Data source ------------------------------------------------------
    g_src = p.add_argument_group("Data source")
    g_src.add_argument("--server", "-s", default="rtserve.iris.washington.edu:18000",
                       help="SeedLink server host:port.")
    g_src.add_argument("--fdsn", default="https://service.earthscope.org",
                       help="FDSN web-service base URL for response metadata.\n"
                            "Pass '' to disable (requires --inventory then).")
    g_src.add_argument("--inventory", default=None,
                       help="Path to a local StationXML file (overrides --fdsn).")
    g_src.add_argument("--no-cache", action="store_true",
                       help="Do not read or write the on-disk inventory cache.")

    # ---- PPSD segmentation ------------------------------------------------
    g_ppsd = p.add_argument_group("PPSD segmentation")
    g_ppsd.add_argument("--ppsd-length", type=float, default=3600.0,
                        metavar="SEC",
                        help="Length of each PPSD segment in seconds.\n"
                             "Default 3600 matches McNamara & Buland (2004);\n"
                             "shorter values are faster to populate but\n"
                             "break comparability with NLNM/NHNM.")
    g_ppsd.add_argument("--overlap", type=float, default=0.5, metavar="FRAC",
                        help="Overlap between consecutive PPSD segments (0-1).")
    g_ppsd.add_argument("--max-hours", type=float, default=None, metavar="HOURS",
                        help="Sliding-window cap: show only PSDs from the\n"
                             "last N hours (default: accumulate forever).")

    # ---- Startup & refresh ------------------------------------------------
    g_buf = p.add_argument_group("Startup & refresh")
    g_buf.add_argument("--backfill-hours", type=float, default=2.0, metavar="HOURS",
                       help="Request this many hours of history from the\n"
                            "server's ring buffer at startup. Best-effort:\n"
                            "many servers cap the replay window to whatever\n"
                            "is in their ring buffer (often minutes, not\n"
                            "hours), so the first full PPSD segment may\n"
                            "still take most of --ppsd-length in wall-clock\n"
                            "time. 0 disables.")
    g_buf.add_argument("--redraw-ms", type=int, default=10000,
                       help="Matplotlib redraw interval in milliseconds.\n"
                            "PPSD changes slowly — 10 s is plenty.")

    # ---- Appearance -------------------------------------------------------
    g_app = p.add_argument_group("Appearance")
    g_app.add_argument("--cmap", default="pqlx",
                       help="Colormap for the 2-D histogram. Default 'pqlx'\n"
                            "matches ObsPy's own PPSD.plot default (the\n"
                            "historical PQLX colour scheme). Any matplotlib\n"
                            "cmap name (viridis, magma, cividis, etc.) also\n"
                            "works.")
    g_app.add_argument("--no-noise-models", dest="show_noise_models",
                       action="store_false", default=True,
                       help="Disable the Peterson NLNM/NHNM overlay.")
    g_app.add_argument("--fullscreen", "-f", action="store_true",
                       help="Open fullscreen with no toolbar (press Esc to exit).")
    g_app.add_argument("--dark-mode", "-d", action="store_true",
                       help="Use a dark colour theme.")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # Response is required — PPSD output is unitless without it and can't
    # be compared against Peterson's noise models.
    if not args.fdsn and not args.inventory:
        parser.error(
            "PPSD requires an instrument response. Pass --fdsn with a base URL "
            "(default https://service.earthscope.org) or --inventory path/to/station.xml."
        )

    if not (0.0 <= args.overlap < 1.0):
        parser.error(f"--overlap must be in [0, 1), got {args.overlap}")

    if args.ppsd_length <= 0:
        parser.error(f"--ppsd-length must be positive, got {args.ppsd_length}")

    cfg = PPSDConfig(
        nslc=args.stream,
        seedlink_server=args.server,
        fdsn_server=args.fdsn if args.fdsn else None,
        inventory_path=args.inventory,
        no_cache=args.no_cache,
        ppsd_length=args.ppsd_length,
        overlap=args.overlap,
        max_hours=args.max_hours,
        backfill_hours=args.backfill_hours,
        redraw_ms=args.redraw_ms,
        show_noise_models=args.show_noise_models,
        cmap=args.cmap,
        fullscreen=args.fullscreen,
        dark_mode=args.dark_mode,
    )
    run_ppsd(cfg)


if __name__ == "__main__":
    sys.exit(main())
