"""Real-time SeedLink archiver writing an SDS-structured miniSEED archive.

Uses the lower-level ``SLClient`` so a state file can be maintained: on
restart, the client sends the last sequence number it saw to the server
and the server backfills anything it still has buffered. This makes the
archiver resilient to short network outages and process restarts.
"""

import logging
import os
import time
from typing import Iterable, List, Optional

from obspy import Stream
from obspy.clients.seedlink.slclient import SLClient
from obspy.clients.seedlink.slpacket import SLPacket

from .sds import ensure_sds_dir, sds_path


# Module-level logger; the CLI configures handlers on the
# root "seedlink_py_utils" logger.
log = logging.getLogger("seedlink_py_utils.archiver")


def build_multiselect(streams: Iterable[str]) -> str:
    """Convert a list of NET.STA.LOC.CHA specs (with optional wildcards in LOC/CHA)
    into the SeedLink multiselect string "NET_STA:LOCCHA,NET_STA:LOCCHA,..."

    Wildcards ``?`` and ``*`` are allowed in LOC and CHA only (SeedLink's native
    support). For wildcards in NET or STA, expand them upstream (e.g. by
    querying the server's INFO=STREAMS) before calling this.

    Empty LOC is rendered as two spaces, matching the SeedLink convention for
    "blank" location codes in the LOCCHA field.
    """
    parts = []
    for s in streams:
        bits = s.split(".")
        if len(bits) != 4:
            raise ValueError(
                f"stream must be NET.STA.LOC.CHA (4 dot-separated fields), got {s!r}"
            )
        net, sta, loc, cha = bits
        if not (net and sta and cha):
            raise ValueError(
                f"NET, STA, and CHA may not be empty in {s!r} (LOC may be empty)"
            )
        if "*" in net or "?" in net or "*" in sta or "?" in sta:
            raise ValueError(
                f"Wildcards in NET/STA are not supported by SeedLink directly: {s!r}. "
                "Expand them by listing explicit NSLCs."
            )
        loc_field = loc if loc else "  "  # two spaces for blank location
        parts.append(f"{net}_{sta}:{loc_field}{cha}")
    return ",".join(parts)


class SDSArchiver(SLClient):
    """SLClient subclass that writes incoming packets to an SDS archive.

    Each incoming data packet becomes an append-write to the appropriate
    SDS daily file. We keep miniSEED records byte-identical to what the
    server sent (so no round-trip through numpy arrays) by writing
    SLPacket.get_raw_data() directly to disk.
    """

    def __init__(self, archive_root: str, state_file: Optional[str] = None,
                 state_save_interval: float = 60.0,
                 loglevel: str = "WARNING"):
        super().__init__(loglevel=loglevel)
        self.archive_root = archive_root
        self.state_file = state_file
        self.state_save_interval = state_save_interval
        self._last_state_save = time.time()
        self._packet_count = 0
        self._last_heartbeat = time.time()
        self._heartbeat_interval = 60.0  # seconds between INFO lines

    def packet_handler(self, count, slpack):
        """Called by SLClient for every packet. Writes miniSEED to SDS."""
        try:
            ptype = slpack.get_type()
        except Exception:
            return False

        # Ignore keepalives / info packets — only archive data
        if ptype not in (SLPacket.TYPE_SLDATA,):
            return False

        try:
            trace = slpack.get_trace()
        except Exception as e:
            log.warning(f"Could not decode packet #{count}: {e}")
            return False

        net = trace.stats.network
        sta = trace.stats.station
        loc = trace.stats.location
        cha = trace.stats.channel
        t0 = trace.stats.starttime

        path = sds_path(self.archive_root, net, sta, loc, cha, t0)
        try:
            ensure_sds_dir(path)
            # Write the raw miniSEED record(s) from this packet directly —
            # cheaper and bit-exact vs. re-encoding via Stream.write().
            raw = slpack.get_raw_data()
            with open(path, "ab") as f:
                f.write(raw)
            self._packet_count += 1
        except Exception as e:
            log.error(f"Failed to write {net}.{sta}.{loc}.{cha} to {path}: {e}")
            return False

        # Periodic state save so we can resume after a restart
        now = time.time()
        if self.state_file and (now - self._last_state_save) >= self.state_save_interval:
            try:
                self.slconn.save_state(self.state_file)
                self._last_state_save = now
                log.debug(f"State saved to {self.state_file}")
            except Exception as e:
                log.warning(f"Could not save state file: {e}")

        # Heartbeat
        if (now - self._last_heartbeat) >= self._heartbeat_interval:
            log.info(
                f"heartbeat: {self._packet_count} packets archived, "
                f"last={net}.{sta}.{loc}.{cha} @ {t0}"
            )
            self._last_heartbeat = now

        return False  # keep running


def run_archiver(
    server: str,
    streams: List[str],
    archive_root: str,
    state_file: Optional[str] = None,
    begin_time: Optional[str] = None,
    end_time: Optional[str] = None,
    reconnect_wait: float = 10.0,
    max_reconnects: Optional[int] = None,
):
    """Run the SeedLink-to-SDS archiver with automatic reconnection.

    Parameters
    ----------
    server : str
        SeedLink server as 'host:port'.
    streams : list of str
        NSLC specs like 'AM.RA382..EH?'. Wildcards allowed in LOC/CHA.
    archive_root : str
        Root directory of the SDS archive.
    state_file : str, optional
        Path to the SeedLink state file for resume on restart.
    begin_time, end_time : str, optional
        If set, operate in 'dial-up' mode: replay the given time window from
        the server's buffer instead of streaming live.
    reconnect_wait : float
        Seconds to wait between reconnection attempts.
    max_reconnects : int, optional
        Maximum number of reconnects (None = unlimited).
    """
    os.makedirs(archive_root, exist_ok=True)
    multiselect = build_multiselect(streams)

    log.info(f"SeedLink server:    {server}")
    log.info(f"Streams:            {multiselect}")
    log.info(f"Archive root:       {archive_root}")
    if state_file:
        log.info(f"State file:         {state_file}")

    attempt = 0
    while True:
        attempt += 1
        try:
            client = SDSArchiver(
                archive_root=archive_root,
                state_file=state_file,
                loglevel="WARNING",
            )
            client.slconn.set_sl_address(server)
            client.multiselect = multiselect

            # Time windows (optional). If None, run real-time.
            client.begin_time = begin_time
            client.end_time = end_time

            # Recover from previous session if state file exists
            if state_file and os.path.exists(state_file):
                try:
                    client.slconn.recover_state(state_file)
                    log.info(f"Recovered state from {state_file}")
                except Exception as e:
                    log.warning(f"Could not recover state ({e}); starting fresh.")

            client.initialize()
            log.info(f"Connected (attempt #{attempt}). Streaming...")
            client.run()
            log.info("SLClient.run() returned cleanly.")
            break  # normal end-time exit
        except KeyboardInterrupt:
            log.info("Interrupted by user. Saving state and exiting.")
            try:
                if state_file:
                    client.slconn.save_state(state_file)
            except Exception:
                pass
            break
        except Exception as e:
            log.error(f"SeedLink connection error: {e}")
            if max_reconnects is not None and attempt >= max_reconnects:
                log.error(f"Giving up after {attempt} attempts.")
                break
            log.info(f"Reconnecting in {reconnect_wait:.0f}s...")
            time.sleep(reconnect_wait)
