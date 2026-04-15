"""Command-line interface for the real-time SeedLink viewer."""

import argparse

from .config import FILTER_CLI_ALIASES, ViewerConfig
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
                   help="Stream in NET.STA.LOC.CHA format (e.g. AM.RA382.00.EHZ or PQ.DAOB..HHZ).")

    p.add_argument("--server", "-s", default="seiscomp.hakai.org:18000",
                   help="SeedLink server host:port.")
    p.add_argument("--fdsn", default="http://seiscomp.hakai.org/fdsnws",
                   help="FDSN web-service base URL for response metadata. "
                        "Set to '' to skip response removal (plot counts).")
    p.add_argument("--inventory", default=None,
                   help="Path to a local StationXML file (overrides --fdsn).")
    p.add_argument("--no-cache", action="store_true",
                   help="Do not read or write the on-disk inventory cache.")

    p.add_argument("--buffer", "-b", type=int, default=300,
                   help="Rolling buffer length in seconds.")
    p.add_argument("--redraw-ms", type=int, default=1000,
                   help="Redraw interval in milliseconds.")

    p.add_argument("--nperseg", type=int, default=512,
                   help="FFT window length in samples.")
    p.add_argument("--noverlap", type=int, default=400,
                   help="FFT window overlap in samples.")
    p.add_argument("--fmin", type=float, default=0.5,
                   help="Spectrogram minimum frequency (Hz).")
    p.add_argument("--fmax", type=float, default=50.0,
                   help="Spectrogram maximum frequency (Hz); clipped to Nyquist at runtime.")
    p.add_argument("--db-clip", type=parse_db_clip, default=(-180.0, -100.0),
                   metavar="LO,HI",
                   help="Spectrogram dB colour limits as 'LO,HI'.")
    p.add_argument("--cmap", default="magma",
                   help="Matplotlib colormap for the spectrogram.")

    p.add_argument("--water-level", type=float, default=60.0,
                   help="Water-level for response deconvolution.")
    p.add_argument("--pre-filt", type=parse_pre_filt, default=_DEFAULT_PRE_FILT,
                   action=_TrackIfSupplied, metavar="F1,F2,F3,F4",
                   help="Pre-filter cosine taper corners for response removal.\n"
                        "Auto-lowered to 0.005,0.01,45,50 when --filter is\n"
                        "'surface' or 'tele-p' (unless you pass this flag\n"
                        "explicitly — in which case your value wins).")

    p.add_argument("--filter", dest="filter_alias",
                   choices=list(FILTER_CLI_ALIASES.keys()), default=None,
                   metavar="NAME",
                   help=(
                       "Lock the waveform filter to one preset and hide the radio\n"
                       "buttons. Omit to keep the interactive selector. Options:\n"
                       "  Teleseismic: surface  (BP 0.02–0.1 Hz, surface waves)\n"
                       "               tele-p   (BP 0.5–2 Hz, teleseismic P)\n"
                       "  Regional:    regional (BP 1–10 Hz, Pg/Pn/Sg/Sn)\n"
                       "  Local:       bp1-25, bp3-25  (BP 1–25, 3–25 Hz)\n"
                       "               hp1, hp3, hp5   (HP 1 / 3 / 5 Hz)\n"
                       "  Off:         none\n"
                       "NB: the default --pre-filt (0.05,0.1,45,50) tapers content\n"
                       "below 0.05 Hz during response removal — which would mute\n"
                       "most of what 'surface' and 'tele-p' want to pass. Selecting\n"
                       "either of those auto-lowers --pre-filt to 0.005,0.01,45,50\n"
                       "unless you pass --pre-filt explicitly."
                   ))

    p.add_argument("--fullscreen", "-f", action="store_true",
                   help="Open fullscreen with no toolbar (press Esc to exit).")
    p.add_argument("--dark-mode", "-d", action="store_true",
                   help="Use a dark colour theme.")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

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
        fullscreen=args.fullscreen,
        dark_mode=args.dark_mode,
        filter_name=FILTER_CLI_ALIASES[args.filter_alias] if args.filter_alias else None,
    )
    run_viewer(cfg)


if __name__ == "__main__":
    main()
