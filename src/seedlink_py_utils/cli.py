"""Command-line interface for the real-time SeedLink viewer."""

import argparse

from .config import FILTER_CLI_ALIASES, ViewerConfig
from .picker import PICKER_PRESETS
from .viewer import run_viewer


def parse_nslc(s):
    """Parse a NET.STA.LOC.CHA string. Empty LOC (e.g. 'PQ.DAOB..HHZ') allowed."""
    parts = s.split(".")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"stream must be NET.STA.LOC.CHA (4 dot-separated fields), got {s!r}"
        )
    net, sta, loc, cha = parts
    if not (net and sta and cha):
        raise argparse.ArgumentTypeError(
            f"NET, STA, and CHA may not be empty in {s!r} (LOC may be empty)"
        )
    return net, sta, loc, cha


def parse_db_clip(s):
    try:
        lo, hi = (float(x) for x in s.split(","))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--db-clip must be 'LO,HI' (e.g. '-180,-100'), got {s!r}"
        )
    if lo >= hi:
        raise argparse.ArgumentTypeError(f"--db-clip LO must be < HI, got {s!r}")
    return (lo, hi)


def parse_pre_filt(s):
    try:
        vals = tuple(float(x) for x in s.split(","))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--pre-filt must be 4 comma-separated floats, got {s!r}"
        )
    if len(vals) != 4:
        raise argparse.ArgumentTypeError(
            f"--pre-filt needs exactly 4 values (f1,f2,f3,f4), got {s!r}"
        )
    return vals


class _Formatter(argparse.RawTextHelpFormatter,
                 argparse.ArgumentDefaultsHelpFormatter):
    """Preserve line breaks in argument help text while still showing defaults."""


class _TrackIfSupplied(argparse.Action):
    """Like the default store action, but also sets `<dest>_set = True` on the
    namespace so downstream code can tell whether the user passed the flag
    versus accepted the default."""
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_set", True)


# Filter aliases that want a long-period-friendly pre-filter. Response removal
# with the standard 0.05,0.1,45,50 taper strips most of the content these
# bandpasses want to show, so the CLI lowers --pre-filt automatically when one
# of these is selected and the user hasn't overridden it.
_LONG_PERIOD_FILTER_ALIASES = {"surface", "tele-p"}
_DEFAULT_PRE_FILT = (0.05, 0.1, 45.0, 50.0)
_LONG_PERIOD_PRE_FILT = (0.005, 0.01, 45.0, 50.0)


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-viewer",
        description="Real-time SeedLink trace + spectrogram viewer.",
        formatter_class=_Formatter,
    )
    p.add_argument("stream", type=parse_nslc,
                   help="Stream in NET.STA.LOC.CHA format "
                        "(e.g. AM.RA382.00.EHZ or PQ.DAOB..HHZ).")

    # ---- Data source ------------------------------------------------------
    g_src = p.add_argument_group("Data source")
    g_src.add_argument("--server", "-s", default="seiscomp.hakai.org:18000",
                       help="SeedLink server host:port.")
    g_src.add_argument("--fdsn", default="http://seiscomp.hakai.org/fdsnws",
                       help="FDSN web-service base URL for response metadata. "
                            "Set to '' to skip response removal (plot counts).")
    g_src.add_argument("--inventory", default=None,
                       help="Path to a local StationXML file (overrides --fdsn).")
    g_src.add_argument("--no-cache", action="store_true",
                       help="Do not read or write the on-disk inventory cache.")

    # ---- Display buffer ---------------------------------------------------
    g_buf = p.add_argument_group("Display buffer")
    g_buf.add_argument("--buffer", "-b", type=int, default=300,
                       help="Rolling buffer length in seconds.")
    g_buf.add_argument("--redraw-ms", type=int, default=1000,
                       help="Redraw interval in milliseconds.")
    g_buf.add_argument("--no-backfill", action="store_false", dest="backfill",
                       default=True,
                       help="Start with an empty display instead of requesting the\n"
                            "last --buffer seconds from the server's ring buffer\n"
                            "(default: backfill on, so the viewer opens with\n"
                            "recent history already drawn).")

    # ---- Spectrogram ------------------------------------------------------
    g_spec = p.add_argument_group("Spectrogram")
    g_spec.add_argument("--nperseg", type=int, default=512,
                        help="FFT window length in samples.")
    g_spec.add_argument("--noverlap", type=int, default=400,
                        help="FFT window overlap in samples.")
    g_spec.add_argument("--fmin", type=float, default=0.5,
                        help="Spectrogram minimum frequency (Hz).")
    g_spec.add_argument("--fmax", type=float, default=50.0,
                        help="Spectrogram maximum frequency (Hz); "
                             "clipped to Nyquist at runtime.")
    g_spec.add_argument("--db-clip", type=parse_db_clip, default=(-180.0, -100.0),
                        action=_TrackIfSupplied, metavar="LO,HI",
                        help="Spectrogram dB colour limits as 'LO,HI'.\n"
                             "Auto-switched to counts-appropriate values (0,60)\n"
                             "when no inventory is available (i.e. plotting raw\n"
                             "counts instead of m/s), unless you pass this flag\n"
                             "explicitly.")
    g_spec.add_argument("--cmap", default="magma",
                        help="Matplotlib colormap for the spectrogram.")

    # ---- Response removal -------------------------------------------------
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

    # ---- Waveform filter --------------------------------------------------
    g_filt = p.add_argument_group("Waveform filter")
    g_filt.add_argument("--filter", dest="filter_alias",
                        choices=list(FILTER_CLI_ALIASES.keys()), default=None,
                        metavar="NAME",
                        help=(
                            "Lock the waveform filter to one preset and hide the\n"
                            "dropdown. Omit to keep the interactive selector.\n"
                            "Options:\n"
                            "  Teleseismic: surface  (BP 0.02–0.1 Hz, surface waves)\n"
                            "               tele-p   (BP 0.5–2 Hz, teleseismic P)\n"
                            "  Regional:    regional (BP 1–10 Hz, Pg/Pn/Sg/Sn)\n"
                            "  Local:       local   (BP 2–10 Hz, standard local band)\n"
                            "               bp1-25, bp3-25  (BP 1–25, 3–25 Hz wideband)\n"
                            "               hp1, hp3, hp5   (HP 1 / 3 / 5 Hz)\n"
                            "  Off:         none\n"
                            "NB: the default --pre-filt (0.05,0.1,45,50) tapers content\n"
                            "below 0.05 Hz during response removal — which would mute\n"
                            "most of what 'surface' and 'tele-p' want to pass. Selecting\n"
                            "either of those auto-lowers --pre-filt to 0.005,0.01,45,50\n"
                            "unless you pass --pre-filt explicitly."
                        ))

    # ---- STA/LTA picker ---------------------------------------------------
    g_pick = p.add_argument_group("STA/LTA picker")
    g_pick.add_argument("--picker", dest="picker_preset",
                        choices=list(PICKER_PRESETS.keys()), default=None,
                        metavar="PRESET",
                        help=(
                            "Enable the STA/LTA picker with a preset. Each preset\n"
                            "bundles STA/LTA windows, trigger thresholds, and a\n"
                            "detection band matching the --filter alias of the same\n"
                            "name (so --picker regional and --filter regional use\n"
                            "the same band). Picks show as red vertical lines on\n"
                            "the waveform, with the CFT drawn in a strip above.\n"
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

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    # Picker override flags require a preset to hang off of (the preset
    # supplies the detection filter band).
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

    # Auto-adjust --pre-filt for long-period filter presets when the user
    # hasn't supplied one explicitly. The _TrackIfSupplied action records
    # pre_filt_set=True on the namespace if --pre-filt was given.
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
        nslc=args.stream,
        seedlink_server=args.server,
        fdsn_server=args.fdsn if args.fdsn else None,
        inventory_path=args.inventory,
        no_cache=args.no_cache,
        buffer_seconds=args.buffer,
        redraw_ms=args.redraw_ms,
        nperseg=args.nperseg,
        noverlap=args.noverlap,
        fmin=args.fmin,
        fmax=args.fmax,
        db_clip=args.db_clip,
        cmap=args.cmap,
        water_level=args.water_level,
        pre_filt=pre_filt,
        db_clip_set=getattr(args, "db_clip_set", False),
        fullscreen=args.fullscreen,
        dark_mode=args.dark_mode,
        backfill_on_start=args.backfill,
        filter_name=FILTER_CLI_ALIASES[args.filter_alias] if args.filter_alias else None,
        picker_preset=args.picker_preset,
        picker_sta=args.sta,
        picker_lta=args.lta,
        picker_thr_on=args.trigger_on,
        picker_thr_off=args.trigger_off,
    )
    run_viewer(cfg)


if __name__ == "__main__":
    main()
