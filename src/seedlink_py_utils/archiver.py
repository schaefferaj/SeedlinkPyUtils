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

from .info import expand_stream_wildcards
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
                "Pass --expand-wildcards (CLI) or expand_wildcards=True "
                "(run_archiver) to expand them via INFO=STREAMS at startup, or "
                "list the explicit NSLCs."
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
                 state_save_interval: float = 60.0):
        super().__init__()
        self.archive_root = archive_root
        self.state_file = state_file
        self.state_save_interval = state_save_interval
        self._last_state_save = time.time()
        self._packet_count = 0
        self._last_heartbeat = time.time()
        self._heartbeat_interval = 60.0  # seconds between INFO lines
        self._save_state_fail_count = 0

    def packet_handler(self, count, slpack):
        """Called by SLClient for every packet. Writes miniSEED to SDS."""
        try:
            ptype = slpack.get_type()
        except Exception:
            return False

        # ObsPy's SLPacket doesn't expose a TYPE_SLDATA constant; instead the
        # idiom (from obspy's own SLClient.packet_handler) is to skip INFO
        # packets explicitly and treat anything else as data.
        if ptype in (SLPacket.TYPE_SLINF, SLPacket.TYPE_SLINFT):
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
            # ObsPy exposes the 512-byte record as the `msrecord` attribute;
            # older code paths used a `get_raw_data()` method that no longer
            # exists.
            raw = slpack.msrecord
            with open(path, "ab") as f:
                f.write(raw)
            self._packet_count += 1
        except Exception as e:
            log.error(f"Failed to write {net}.{sta}.{loc}.{cha} to {path}: {e}")
            return False

        # Periodic state save so we can resume after a restart. We always
        # advance _last_state_save whether the save succeeded or not — a failing
        # save that retriggers on every packet just floods the log.
        now = time.time()
        if self.state_file and (now - self._last_state_save) >= self.state_save_interval:
            self._last_state_save = now
            self._save_state_once()

        # Heartbeat
        if (now - self._last_heartbeat) >= self._heartbeat_interval:
            log.info(
                f"heartbeat: {self._packet_count} packets archived, "
                f"last={net}.{sta}.{loc}.{cha} @ {t0}"
            )
            self._last_heartbeat = now

        return False  # keep running

    def _save_state_once(self):
        """Call ``slconn.save_state`` and emit log lines that won't spam if the
        call keeps failing. ObsPy's save_state can both raise a TypeError *and*
        print to stderr on failure, so we treat the return value and exceptions
        uniformly and log only on state changes."""
        try:
            ok = self.slconn.save_state(self.state_file)
            failed = (ok is False)
            err = None
        except Exception as e:
            failed = True
            err = e

        if not failed:
            if self._save_state_fail_count:
                log.info(
                    f"State saved to {self.state_file} "
                    f"(after {self._save_state_fail_count} failed attempts)."
                )
            else:
                log.debug(f"State saved to {self.state_file}")
            self._save_state_fail_count = 0
            return

        self._save_state_fail_count += 1
        if self._save_state_fail_count == 1:
            log.warning(
                f"slconn.save_state({self.state_file!r}) failed "
                f"({err if err else 'returned False'}). "
                "Further failures will be suppressed; state recovery on restart "
                "may not work."
            )


def run_archiver(
    server: str,
    streams: List[str],
    archive_root: str,
    state_file: Optional[str] = None,
    begin_time: Optional[str] = None,
    end_time: Optional[str] = None,
    reconnect_wait: float = 10.0,
    max_reconnects: Optional[int] = None,
    expand_wildcards: bool = False,
):
    """Run the SeedLink-to-SDS archiver with automatic reconnection.

    Parameters
    ----------
    server : str
        SeedLink server as 'host:port'.
    streams : list of str
        NSLC specs like 'AM.RA382..EH?'. Wildcards allowed in LOC/CHA, and
        also in NET/STA when ``expand_wildcards=True``.
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
    expand_wildcards : bool
        If True, expand ``?`` / ``*`` in NET and STA fields by querying
        ``INFO=STREAMS`` once at startup and substituting the matching
        explicit station list. SeedLink does not support NET/STA wildcards
        natively, so this is the only way to subscribe to e.g. all stations
        in a network without listing them by hand.
    """
    os.makedirs(archive_root, exist_ok=True)
    if expand_wildcards:
        original_count = len(streams)
        streams = expand_stream_wildcards(server, streams)
        log.info(f"Expanded {original_count} wildcard spec(s) to {len(streams)} streams.")
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
            )
            client.slconn.set_sl_address(server)
            # ObsPy bug workaround: SeedLinkConnection.save_state/recover_state
            # take a `statefile` parameter in their signature but internally
            # read `self.statefile`. Setting the attribute directly is the only
            # way to make them work. See MEMORY.md.
            if state_file:
                client.slconn.statefile = state_file
            client.multiselect = multiselect

            # Time windows (optional). If None, run real-time.
            client.begin_time = begin_time
            client.end_time = end_time

            # initialize() reads self.multiselect and populates slconn.streams.
            # We must call it BEFORE recover_state, because recover_state
            # matches the station entries in the file against slconn.streams
            # and silently skips ones with no match. Called the other way
            # round, every line in the state file is "no matching streams"
            # and we start from the live tip instead of resuming.
            client.initialize()

            # Recover from previous session. On first run the file won't exist
            # yet; we touch an empty one so recover_state still runs, because
            # that call is also what primes ObsPy's internal bookkeeping for
            # the later save_state calls.
            if state_file:
                if not os.path.exists(state_file):
                    open(state_file, "a").close()
                    log.info(f"Created empty state file: {state_file}")
                try:
                    n = client.slconn.recover_state(state_file)
                    log.info(f"Recovered state from {state_file} ({n} stream(s)).")
                except Exception as e:
                    log.warning(f"Could not recover state ({e}); starting fresh.")
            log.info(f"Connected (attempt #{attempt}). Streaming...")
            client.run()
            log.info("SLClient.run() returned cleanly.")
            break  # normal end-time exit
        except KeyboardInterrupt:
            log.info("Interrupted by user. Saving state and exiting.")
            if state_file:
                client._save_state_once()
            break
        except Exception as e:
            log.error(f"SeedLink connection error: {e}")
            if max_reconnects is not None and attempt >= max_reconnects:
                log.error(f"Giving up after {attempt} attempts.")
                break
            log.info(f"Reconnecting in {reconnect_wait:.0f}s...")
            time.sleep(reconnect_wait)
