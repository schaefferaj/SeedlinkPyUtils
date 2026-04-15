"""Inventory loading and signal processing (response removal, filtering)."""

import os

from obspy import read_inventory
from obspy.clients.fdsn import Client as FDSNClient

from .config import FILTERS, ViewerConfig


def _make_fdsn_client(base: str):
    """Build an FDSNClient for `base`, tolerant of both standard servers and
    SeisComP-behind-nginx setups.

    Tries service discovery first (works for IRIS and other conformant servers).
    If discovery fails, falls back to explicit service_mappings that append
    /fdsnws/ — which matches SeisComP's fdsnws server whether accessed directly
    or via an nginx location that proxies /fdsnws → upstream-at-/fdsnws (giving
    the doubled /fdsnws/fdsnws/ path from the outside).
    """
    # 1. Try standard discovery (works for IRIS, GFZ, etc.)
    try:
        return FDSNClient(base_url=base)
    except Exception as e:
        print(f"FDSN discovery failed on {base} ({e}); trying explicit /fdsnws mapping.")

    # 2. Fall back to explicit mappings with appended /fdsnws/.
    #    _discover_services=False is required — otherwise the constructor
    #    re-runs discovery and raises before service_mappings are consulted.
    return FDSNClient(
        base_url=base,
        service_mappings={
            "station":    f"{base}/fdsnws/station/1",
            "dataselect": f"{base}/fdsnws/dataselect/1",
            "event":      f"{base}/fdsnws/event/1",
        },
        _discover_services=False,
    )


def load_inventory(cfg: ViewerConfig):
    """Load a StationXML inventory for response removal.

    Priority: explicit --inventory file > on-disk cache > FDSN fetch.
    Returns None if inventory cannot be obtained (viewer falls back to counts).
    """
    net, sta, loc, cha = cfg.nslc
    # Strip SeedLink wildcards from the cache filename so pattern-style CHA
    # (e.g. 'HH?' from the multi-channel viewer) doesn't produce an illegal
    # filename on Windows. The FDSN query still uses the original pattern.
    cha_clean = cha.replace("?", "").replace("*", "")
    cache_path = f"./inv_{net}_{sta}_{cha_clean}.xml"

    try:
        if cfg.inventory_path:
            inv = read_inventory(cfg.inventory_path)
            print(f"Loaded inventory from {cfg.inventory_path}")
            return inv

        if not cfg.no_cache and os.path.exists(cache_path):
            inv = read_inventory(cache_path)
            print(f"Loaded cached inventory from {cache_path}")
            return inv

        if cfg.fdsn_server:
            base = cfg.fdsn_server.rstrip("/")
            print(f"Fetching response for {net}.{sta}.{loc}.{cha} from {base}...")

            fdsn = _make_fdsn_client(base)
            inv = fdsn.get_stations(
                network=net, station=sta, location=loc, channel=cha,
                level="response",
            )
            if not cfg.no_cache:
                inv.write(cache_path, format="STATIONXML")
                print(f"Fetched and cached inventory to {cache_path}")
            else:
                print("Fetched inventory (cache disabled).")
            return inv

        print("No inventory configured — plotting raw counts.")
        return None

    except Exception as e:
        print(f"Could not load inventory ({e}). Falling back to raw counts.")
        return None


def _combine_patterns(values):
    """Collapse a set of fixed-length strings into a single fnmatch/SeedLink
    wildcard pattern, with ``?`` in positions where the inputs disagree.
    Examples: ``{'HHZ', 'HHN', 'HHE'} → 'HH?'``,
    ``{'HHZ', 'EHZ'} → '?HZ'``, ``{'HHZ'} → 'HHZ'``."""
    values = list(values)
    if not values:
        return "*"
    if len(values) == 1:
        return values[0]
    n = max(len(v) for v in values)
    out = []
    for i in range(n):
        col = {v[i] if i < len(v) else "" for v in values}
        out.append(col.pop() if len(col) == 1 else "?")
    return "".join(out)


def load_inventory_multi(cfg: ViewerConfig, streams):
    """Load and merge inventories for multiple NSLC streams.

    Groups the ``streams`` list by ``(net, sta)`` and makes one FDSN call per
    station, with the LOC and CHA collapsed into wildcard patterns that
    cover every channel subscribed at that station. This avoids the trap of
    only fetching the first channel's response (which leaves the other
    components silently uncorrected).

    Returns the merged Inventory (via ObsPy's ``Inventory.__add__``), or
    None if no station's inventory could be loaded.
    """
    from collections import defaultdict
    from dataclasses import replace

    by_station = defaultdict(lambda: {"locs": set(), "chas": set()})
    for (n, s, l, c) in streams:
        by_station[(n, s)]["locs"].add(l)
        by_station[(n, s)]["chas"].add(c)

    combined = None
    for (net, sta), spec in by_station.items():
        loc = _combine_patterns(spec["locs"])
        cha = _combine_patterns(spec["chas"])
        inv = load_inventory(replace(cfg, nslc=(net, sta, loc, cha)))
        if inv is None:
            continue
        combined = inv if combined is None else combined + inv
    return combined


def remove_response_safe(tr, inventory, cfg: ViewerConfig):
    """Return a response-removed copy of `tr` in m/s, or demeaned raw on failure."""
    tr = tr.copy()
    tr.detrend("demean")
    if inventory is not None:
        try:
            tr.remove_response(
                inventory=inventory, output="VEL",
                pre_filt=cfg.pre_filt, water_level=cfg.water_level,
                taper=True, taper_fraction=0.05,
            )
        except Exception as e:
            print(f"Response removal failed: {e}")
    return tr


def apply_filter(tr, filter_name: str):
    """Apply a named filter preset to a copy of `tr`. Returns `tr` unchanged for 'None'.

    Before filtering we remove a linear trend and apply a small cosine taper
    to the edges. `remove_response_safe` already does a 5% taper when
    inventory is available, but when the fallback-to-counts path runs the
    trace has hard edges, and filtering a hard-edged trace produces
    impulse-response transients at the earliest sample that would otherwise
    dominate the autoscaled y-axis.
    """
    flt = FILTERS.get(filter_name)
    if flt is None:
        return tr
    tr = tr.copy()
    tr.detrend("linear")
    tr.taper(type="cosine", max_percentage=0.01)
    kind, kwargs = flt
    tr.filter(kind, **kwargs)
    return tr
