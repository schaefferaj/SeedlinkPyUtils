"""Inventory loading and signal processing (response removal, filtering)."""

import os

from obspy import read_inventory
from obspy.clients.fdsn import Client as FDSNClient

from .config import FILTERS, ViewerConfig


def load_inventory(cfg: ViewerConfig):
    """Load a StationXML inventory for response removal.

    Priority: explicit --inventory file > on-disk cache > FDSN fetch.
    Returns None if inventory cannot be obtained (viewer falls back to counts).
    """
    net, sta, loc, cha = cfg.nslc
    cache_path = f"./inv_{net}_{sta}_{cha}.xml"

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
            # service_mappings avoids ObsPy appending '/fdsnws/' a second time
            # when the base URL already includes it (e.g. behind nginx).
            fdsn = FDSNClient(
                base_url=base,
                service_mappings={
                    "station":    f"{base}/station/1",
                    "dataselect": f"{base}/dataselect/1",
                    "event":      f"{base}/event/1",
                },
            )
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
    """Apply a named filter preset to a copy of `tr`. Returns `tr` unchanged for 'None'."""
    flt = FILTERS.get(filter_name)
    if flt is None:
        return tr
    tr = tr.copy()
    kind, kwargs = flt
    tr.filter(kind, **kwargs)
    return tr
