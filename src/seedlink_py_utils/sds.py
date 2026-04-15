"""SDS (SeisComP Data Structure) archive path utilities.

Layout:
    <ARCHIVE>/<YEAR>/<NET>/<STA>/<CHA>.D/<NET>.<STA>.<LOC>.<CHA>.D.<YEAR>.<JDAY>

One file per NSLC per day. Files grow as miniSEED records are appended.
"""

import os
from obspy import UTCDateTime


def sds_path(archive_root: str, net: str, sta: str, loc: str, cha: str,
             t: UTCDateTime) -> str:
    """Return the SDS file path for (N,S,L,C) on the day containing `t`."""
    year = t.year
    jday = t.julday
    return os.path.join(
        archive_root,
        f"{year:04d}",
        net,
        sta,
        f"{cha}.D",
        f"{net}.{sta}.{loc}.{cha}.D.{year:04d}.{jday:03d}",
    )


def ensure_sds_dir(path: str):
    """Create the parent directory of `path` if it doesn't exist."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
