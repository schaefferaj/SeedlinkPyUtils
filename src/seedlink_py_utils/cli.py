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


def build_parser():
    p = argparse.ArgumentParser(
        prog="seedlink-py-viewer",
        description="Real-time SeedLink trace + spectrogram viewer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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
    p.add_argument("--pre-filt", type=parse_pre_filt, default=(0.05, 0.1, 45.0, 50.0),
                   metavar="F1,F2,F3,F4",
                   help="Pre-filter cosine taper corners for response removal.")

    p.add_argument("--filter", dest="filter_alias",
                   choices=list(FILTER_CLI_ALIASES.keys()), default=None,
                   help="Lock the waveform filter to one preset and hide the "
                        "radio buttons. Omit to keep the interactive selector.")

    p.add_argument("--fullscreen", "-f", action="store_true",
                   help="Open fullscreen with no toolbar (press Esc to exit).")
    p.add_argument("--dark-mode", "-d", action="store_true",
                   help="Use a dark colour theme.")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
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
        pre_filt=args.pre_filt,
        fullscreen=args.fullscreen,
        dark_mode=args.dark_mode,
        filter_name=FILTER_CLI_ALIASES[args.filter_alias] if args.filter_alias else None,
    )
    run_viewer(cfg)


if __name__ == "__main__":
    main()
