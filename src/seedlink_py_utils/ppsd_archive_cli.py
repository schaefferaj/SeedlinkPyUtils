"""Command-line interface for the headless PPSD archiver daemon."""

# Force the Agg matplotlib backend BEFORE anything imports pyplot. On
# headless servers (the primary deployment target for this daemon) the
# auto-selected GUI backend will fail to create figures — Agg always
# works and is what we want for save-to-PNG rendering anyway. Must be
# set before .ppsd_archive is imported, which is why it lives here in
# the CLI entry point rather than deeper in the module tree. Pairs
# with the lazy-loading __init__.py so the package import itself
# doesn't pull in matplotlib.
import matplotlib
matplotlib.use("Agg")

import argparse  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402

from .logging_setup import setup_logger  # noqa: E402
from .ppsd_archive import PERIODS, PPSDArchiveConfig, run_ppsd_archive  # noqa: E402


class _Formatter(argparse.RawTextHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve line breaks in argument help text while still showing defaults."""


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-ppsd-archive",
        description=(
            "Headless PPSD archiver. Subscribes to one or more SeedLink "
            "streams and maintains a master PPSD per NSLC. On a schedule "
            "(default every 30 min), renders per-bucket PNGs to disk for "
            "each requested --period (daily, weekly, monthly, quarterly, "
            "yearly — combine any subset). One master NPZ per NSLC is "
            "persisted and reloaded on restart so long-running histograms "
            "survive reboots and crashes. Requires an instrument response "
            "per NSLC — any NSLC whose response cannot be loaded is logged "
            "and skipped."
        ),
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  # Weekly PPSDs for a single station, defaults everywhere\n"
            "  seedlink-py-ppsd-archive IU.ANMO.00.BHZ --output-root /data/ppsd\n"
            "\n"
            "  # Daily + weekly + monthly for every PQ vertical\n"
            "  seedlink-py-ppsd-archive 'PQ.*..HHZ' \\\n"
            "      --output-root /data/ppsd \\\n"
            "      --period daily weekly monthly \\\n"
            "      --expand-wildcards\n"
            "\n"
            "  # Long-running Hakai SchoolShake fleet, with rotating log\n"
            "  seedlink-py-ppsd-archive 'AM.*..EH?' \\\n"
            "      --server seiscomp.hakai.org:18000 \\\n"
            "      --fdsn http://seiscomp.hakai.org/fdsnws \\\n"
            "      --output-root /data/ppsd \\\n"
            "      --period weekly monthly \\\n"
            "      --expand-wildcards \\\n"
            "      --log-file /var/log/ppsd-archive.log\n"
            "\n"
            "Output layout:\n"
            "  <output-root>/<NET>.<STA>/<NSLC>.npz                (master state)\n"
            "  <output-root>/<NET>.<STA>/<period>/<NSLC>_<key>.png (one per bucket)\n"
            "\n"
            "Bucket keys:\n"
            "  daily      YYYY-MM-DD\n"
            "  weekly     YYYY-Www     (ISO year + ISO week)\n"
            "  monthly    YYYY-MM\n"
            "  quarterly  YYYY-Qn      (Q1=Jan-Mar, ...)\n"
            "  yearly     YYYY\n"
            "\n"
            "Ctrl-C or SIGTERM triggers a final render + NPZ flush before exit."
        ),
    )

    p.add_argument("streams", nargs="+",
                   help="One or more NSLC streams in NET.STA.LOC.CHA form.\n"
                        "? / * wildcards are allowed in LOC and CHA natively;\n"
                        "wildcards in NET or STA require --expand-wildcards.")

    # ---- Output --------------------------------------------------------
    g_out = p.add_argument_group("Output")
    g_out.add_argument("--output-root", required=True,
                       help="Root directory for per-NSLC folders, master\n"
                            "NPZs, and per-bucket PNGs.")
    g_out.add_argument("--period", nargs="+", choices=PERIODS,
                       default=["weekly"], metavar="NAME",
                       help=f"One or more periods to render "
                            f"({', '.join(PERIODS)}). Multiple may be\n"
                            f"combined; each NSLC's master PPSD feeds\n"
                            f"every requested period.")
    g_out.add_argument("--render-interval", type=float, default=1800.0,
                       metavar="SEC",
                       help="Seconds between PNG re-render cycles.\n"
                            "PPSDs change slowly; the default 1800 s\n"
                            "(30 min) keeps disk churn modest.")

    # ---- Data source ---------------------------------------------------
    g_src = p.add_argument_group("Data source")
    g_src.add_argument("--server", "-s",
                       default="rtserve.iris.washington.edu:18000",
                       help="SeedLink server host:port.")
    g_src.add_argument("--fdsn", default="https://service.earthscope.org",
                       help="FDSN web-service base URL for response metadata.\n"
                            "Pass '' to disable (requires --inventory then).")
    g_src.add_argument("--inventory", default=None,
                       help="Path to a local StationXML file (overrides --fdsn).")
    g_src.add_argument("--no-cache", action="store_true",
                       help="Do not read or write the on-disk inventory cache.")

    # ---- PPSD segmentation --------------------------------------------
    g_ppsd = p.add_argument_group("PPSD segmentation")
    g_ppsd.add_argument("--ppsd-length", type=float, default=3600.0,
                        metavar="SEC",
                        help="Length of each PPSD segment in seconds.\n"
                             "Default 3600 matches McNamara & Buland (2004);\n"
                             "changing this breaks comparability with\n"
                             "the overlaid NLNM/NHNM.")
    g_ppsd.add_argument("--overlap", type=float, default=0.5, metavar="FRAC",
                        help="Overlap between consecutive PPSD segments (0-1).")

    # ---- Wildcards ----------------------------------------------------
    g_wild = p.add_argument_group("Wildcards")
    g_wild.add_argument("--expand-wildcards", action="store_true",
                        help="Expand ? / * in NET and STA via a one-shot\n"
                             "INFO=STREAMS query at startup. Quote\n"
                             "wildcarded specs so the shell doesn't\n"
                             "glob them (e.g. 'PQ.*..HHZ').")

    # ---- Appearance ---------------------------------------------------
    g_app = p.add_argument_group("Appearance")
    g_app.add_argument("--cmap", default="pqlx",
                       help="Colormap for the 2-D histogram. Default 'pqlx'\n"
                            "matches ObsPy's PPSD.plot default (historical\n"
                            "PQLX colour scheme). Any matplotlib cmap name\n"
                            "(viridis, magma, cividis, etc.) also works.")
    g_app.add_argument("--no-noise-models", dest="show_noise_models",
                       action="store_false", default=True,
                       help="Disable the Peterson NLNM/NHNM overlay.")

    # ---- Logging ------------------------------------------------------
    g_log = p.add_argument_group("Logging")
    g_log.add_argument("--log-file",
                       help="Path to a rotating log file (10 MB x 5 backups).")
    g_log.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Logging verbosity.")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logger(
        name="seedlink_py_utils",
        log_file=args.log_file,
        level=getattr(logging, args.log_level),
    )

    if not args.fdsn and not args.inventory:
        parser.error(
            "PPSD requires an instrument response. Pass --fdsn with a base "
            "URL (default https://service.earthscope.org) or --inventory "
            "path/to/station.xml."
        )

    if not (0.0 <= args.overlap < 1.0):
        parser.error(f"--overlap must be in [0, 1), got {args.overlap}")

    if args.ppsd_length <= 0:
        parser.error(f"--ppsd-length must be positive, got {args.ppsd_length}")

    if args.render_interval <= 0:
        parser.error(f"--render-interval must be positive, got {args.render_interval}")

    cfg = PPSDArchiveConfig(
        streams=args.streams,
        output_root=args.output_root,
        seedlink_server=args.server,
        fdsn_server=args.fdsn if args.fdsn else None,
        inventory_path=args.inventory,
        no_cache=args.no_cache,
        periods=tuple(args.period),
        ppsd_length=args.ppsd_length,
        overlap=args.overlap,
        render_interval=args.render_interval,
        expand_wildcards=args.expand_wildcards,
        show_noise_models=args.show_noise_models,
        cmap=args.cmap,
    )
    try:
        run_ppsd_archive(cfg)
    except KeyboardInterrupt:
        # Signal handler inside run_ppsd_archive triggers a clean exit,
        # but if KeyboardInterrupt escapes (e.g. during startup before
        # the handler is installed), land it here.
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
