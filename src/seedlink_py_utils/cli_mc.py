"""CLI for the multi-channel / multi-station real-time SeedLink viewer.

Mirrors the single-channel viewer's CLI (same filter and picker options),
but accepts one or more positional streams and draws one waveform panel
per stream. Supports ``--expand-wildcards`` for NET/STA wildcard fan-out
via a one-shot ``INFO=STREAMS`` query at startup, same as the archiver.
"""

import argparse

from .cli import (
    _Formatter,
    _TrackIfSupplied,
    _LONG_PERIOD_FILTER_ALIASES,
    _DEFAULT_PRE_FILT,
    _LONG_PERIOD_PRE_FILT,
    parse_nslc,
    parse_pre_filt,
)
from .config import FILTER_CLI_ALIASES, ViewerConfig
from .info import expand_all_wildcards
from .picker import PICKER_PRESETS
from .viewer_mc import run_viewer_mc


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-mc-viewer",
        description=(
            "Real-time multi-channel / multi-station SeedLink viewer. One "
            "stacked waveform panel per stream; no spectrogram. Typical use "
            "cases: 3-component view of one station, or vertical-only across "
            "a selection of stations."
        ),
        formatter_class=_Formatter,
        epilog=(
            "Examples:\n"
            "  seedlink-py-mc-viewer IU.ANMO.00.BH?                # 3-component on one station\n"
            "\n"
            "  seedlink-py-mc-viewer CN.PGC..HHZ CN.NLLB..HHZ \\\n"
            "      CN.SADO..HHZ --picker local                     # explicit list, verticals\n"
            "\n"
            "  seedlink-py-mc-viewer 'CN.*..HHZ' --picker local    # every CN vertical\n"
            "\n"
            "Stream syntax: NET.STA.LOC.CHA with ? / * wildcards allowed in any field.\n"
            "Any wildcard triggers a one-shot INFO=STREAMS query at startup to expand\n"
            "into the concrete list of matching channels (one panel per match). Quote\n"
            "specs with wildcards to stop the shell from globbing them. Empty LOC is\n"
            "written as two dots, e.g. CN.PGC..HHZ."
        ),
    )

    p.add_argument("streams", nargs="+", type=parse_nslc, metavar="STREAM",
                   help="One or more streams in NET.STA.LOC.CHA form. Wildcards\n"
                        "(? / *) are allowed in any field and auto-expand via\n"
                        "INFO=STREAMS at startup (one panel per matched channel).")

    # ---- Multi-panel -----------------------------------------------------
    g_mc = p.add_argument_group("Multi-panel layout")
    g_mc.add_argument("--max-panels", type=int, default=8, metavar="N",
                      help="Upper bound on the total number of waveform panels.\n"
                           "If the (expanded) stream list exceeds this, the\n"
                           "viewer truncates with a warning.")

    # ---- Data source -----------------------------------------------------
    g_src = p.add_argument_group("Data source")
    g_src.add_argument("--server", "-s", default="rtserve.iris.washington.edu:18000",
                       help="SeedLink server host:port.")
    g_src.add_argument("--fdsn", default="https://service.earthscope.org",
                       help="FDSN web-service base URL for response metadata. "
                            "Set to '' to skip response removal (plot counts).")
    g_src.add_argument("--inventory", default=None,
                       help="Path to a local StationXML file (overrides --fdsn).")
    g_src.add_argument("--no-cache", action="store_true",
                       help="Do not read or write the on-disk inventory cache.")

    # ---- Display buffer --------------------------------------------------
    g_buf = p.add_argument_group("Display buffer")
    g_buf.add_argument("--buffer", "-b", type=int, default=300,
                       help="Rolling buffer length in seconds.")
    g_buf.add_argument("--redraw-ms", type=int, default=1000,
                       help="Redraw interval in milliseconds.")
    g_buf.add_argument("--no-backfill", action="store_false", dest="backfill",
                       default=True,
                       help="Start with empty panels instead of requesting the\n"
                            "last --buffer seconds from the server's ring buffer.")

    # ---- Response removal ------------------------------------------------
    g_resp = p.add_argument_group("Response removal")
    g_resp.add_argument("--water-level", type=float, default=60.0,
                        help="Water-level for response deconvolution.")
    g_resp.add_argument("--pre-filt", type=parse_pre_filt,
                        default=_DEFAULT_PRE_FILT,
                        action=_TrackIfSupplied, metavar="F1,F2,F3,F4",
                        help="Pre-filter cosine taper corners for response removal.\n"
                             "Auto-lowered to 0.005,0.01,45,50 when --filter is\n"
                             "'surface' or 'tele-p' (unless you pass this flag\n"
                             "explicitly — in which case your value wins).")

    # ---- Waveform filter -------------------------------------------------
    g_filt = p.add_argument_group("Waveform filter")
    g_filt.add_argument("--filter", dest="filter_alias",
                        choices=list(FILTER_CLI_ALIASES.keys()), default=None,
                        metavar="NAME",
                        help=(
                            "Lock the waveform filter to one preset and hide the\n"
                            "dropdown. Omit to keep the interactive selector.\n"
                            "See seedlink-py-viewer --help for the full preset list."
                        ))

    # ---- STA/LTA picker --------------------------------------------------
    g_pick = p.add_argument_group("STA/LTA picker")
    g_pick.add_argument("--picker", dest="picker_preset",
                        choices=list(PICKER_PRESETS.keys()), default=None,
                        metavar="PRESET",
                        help=(
                            "Enable the STA/LTA picker. Each panel runs an\n"
                            "independent picker with this preset; pick markers\n"
                            "appear on their own panel at the right time.\n"
                            "Presets:\n"
                            "  local      BP 2–10 Hz, STA 0.5 s / LTA 10 s, 3.5/1.5\n"
                            "  regional   BP 1–10 Hz, STA 2 s / LTA 30 s, 3.0/1.5\n"
                            "  tele-p     BP 0.5–2 Hz, STA 5 s / LTA 120 s, 2.5/1.5\n"
                            "Use --sta / --lta / --trigger-on / --trigger-off to\n"
                            "override individual fields of the chosen preset."
                        ))
    g_pick.add_argument("--sta", type=float, default=None, metavar="SEC",
                        help="Override STA window (seconds). Requires --picker.")
    g_pick.add_argument("--lta", type=float, default=None, metavar="SEC",
                        help="Override LTA window (seconds). Requires --picker.")
    g_pick.add_argument("--trigger-on", type=float, default=None, metavar="RATIO",
                        help="Override STA/LTA ratio to trigger on. Requires --picker.")
    g_pick.add_argument("--trigger-off", type=float, default=None, metavar="RATIO",
                        help="Override STA/LTA ratio to trigger off. Requires --picker.")

    # ---- Window / appearance ---------------------------------------------
    g_win = p.add_argument_group("Window / appearance")
    g_win.add_argument("--fullscreen", "-f", action="store_true",
                       help="Open fullscreen with no toolbar (press Esc to exit).")
    g_win.add_argument("--dark-mode", "-d", action="store_true",
                       help="Use a dark colour theme.")
    g_win.add_argument("--no-clock", action="store_true",
                       help="Ignore absolute timestamps — use the trace's own\n"
                            "endpoint as 'now'. Useful when the SeedLink source\n"
                            "has no NTP and its clock is wrong.")

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.picker_preset is None and any([
        args.sta is not None,
        args.lta is not None,
        args.trigger_on is not None,
        args.trigger_off is not None,
    ]):
        parser.error(
            "--sta / --lta / --trigger-on / --trigger-off require --picker PRESET "
            "(pick the closest preset and override the fields you want to change)."
        )

    # Expand any SeedLink wildcards (NET/STA/LOC/CHA) against INFO=STREAMS
    # so each matched channel gets its own panel. No-op when every spec is
    # already fully concrete.
    nslcs = args.streams
    as_strings = [f"{n}.{s}.{l}.{c}" for (n, s, l, c) in nslcs]
    try:
        expanded = expand_all_wildcards(args.server, as_strings)
    except Exception as e:
        parser.error(f"Could not expand wildcards via INFO=STREAMS: {e}")
    if len(expanded) != len(as_strings):
        print(f"Expanded {len(as_strings)} spec(s) to {len(expanded)} streams.")
    nslcs = [tuple(spec.split(".")) for spec in expanded]

    pre_filt = args.pre_filt
    if (args.filter_alias in _LONG_PERIOD_FILTER_ALIASES
            and not getattr(args, "pre_filt_set", False)):
        pre_filt = _LONG_PERIOD_PRE_FILT
        print(
            f"--filter {args.filter_alias}: auto-adjusting --pre-filt to "
            f"{','.join(str(x) for x in pre_filt)} for long-period content. "
            "Pass --pre-filt explicitly to override."
        )

    cfg = ViewerConfig(
        # nslc holds the first stream for compatibility with single-stream
        # code paths (e.g. load_inventory's cache filename); the mc-viewer
        # itself uses nslcs.
        nslc=nslcs[0],
        nslcs=nslcs,
        seedlink_server=args.server,
        fdsn_server=args.fdsn if args.fdsn else None,
        inventory_path=args.inventory,
        no_cache=args.no_cache,
        buffer_seconds=args.buffer,
        redraw_ms=args.redraw_ms,
        water_level=args.water_level,
        pre_filt=pre_filt,
        fullscreen=args.fullscreen,
        dark_mode=args.dark_mode,
        no_clock=args.no_clock,
        backfill_on_start=args.backfill,
        filter_name=FILTER_CLI_ALIASES[args.filter_alias] if args.filter_alias else None,
        picker_preset=args.picker_preset,
        picker_sta=args.sta,
        picker_lta=args.lta,
        picker_thr_on=args.trigger_on,
        picker_thr_off=args.trigger_off,
        max_panels=args.max_panels,
    )
    run_viewer_mc(cfg)


if __name__ == "__main__":
    main()
