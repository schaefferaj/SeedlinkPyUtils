"""Rolling trace buffer and SeedLink client worker for the real-time viewer.

The worker uses ObsPy's ``SLClient`` (rather than the simpler
``easyseedlink.create_client``) because we want to set a `begin_time` at
startup to backfill the display from the server's ring buffer. The server
replays packets from that timestamp and then transitions seamlessly into
live streaming.
"""

import threading
import time

from obspy import Stream, UTCDateTime
from obspy.clients.seedlink.slclient import SLClient
from obspy.clients.seedlink.slpacket import SLPacket


class TraceBuffer:
    """Thread-safe rolling buffer of incoming SeedLink packets."""

    def __init__(self, buffer_seconds: int, no_clock: bool = False):
        self.buffer_seconds = buffer_seconds
        self.no_clock = no_clock
        self._stream = Stream()
        self._lock = threading.Lock()

    def append(self, trace):
        with self._lock:
            self._stream += trace
            self._stream.merge(method=1, fill_value=0)
            if self.no_clock:
                latest_end = max(tr.stats.endtime for tr in self._stream)
                cutoff = latest_end - self.buffer_seconds
            else:
                cutoff = UTCDateTime() - self.buffer_seconds
            self._stream.trim(starttime=cutoff)
            # Drop accumulated provenance so ``stats.processing`` can't
            # grow past ObsPy's 100-entry warning threshold over a long
            # session. Each ``merge``/``trim`` logs an entry, and no
            # downstream consumer (viewer, mc-viewer, PPSD) reads this
            # list — it's pure metadata we don't need.
            for tr in self._stream:
                tr.stats.processing = []

    def latest(self, channel):
        """Return a copy of the most recent trace for `channel`, or None.

        For a buffer carrying multiple stations, prefer :meth:`latest_nslc`
        — selecting by channel alone can return the wrong station's trace
        when channel codes are shared (e.g. two stations both sending HHZ).
        """
        with self._lock:
            if len(self._stream) == 0:
                return None
            sel = self._stream.select(channel=channel)
            if len(sel) == 0:
                return None
            return sel[0].copy()

    def latest_nslc(self, net, sta, loc, cha):
        """Return a copy of the most recent trace matching the full NSLC,
        or None if no such trace is in the buffer yet."""
        with self._lock:
            if len(self._stream) == 0:
                return None
            sel = self._stream.select(
                network=net, station=sta, location=loc, channel=cha
            )
            if len(sel) == 0:
                return None
            return sel[0].copy()

    def __len__(self):
        with self._lock:
            return len(self._stream)


class _ViewerBufferClient(SLClient):
    """SLClient subclass that appends incoming data packets to a TraceBuffer."""

    def __init__(self, buffer: "TraceBuffer"):
        super().__init__()
        self._buffer = buffer

    def packet_handler(self, count, slpack):
        try:
            ptype = slpack.get_type()
        except Exception:
            return False

        # Skip INFO packets; treat everything else as data (same pattern as
        # the archiver — there is no TYPE_SLDATA constant in current ObsPy).
        if ptype in (SLPacket.TYPE_SLINF, SLPacket.TYPE_SLINFT):
            return False

        try:
            trace = slpack.get_trace()
        except Exception:
            return False

        self._buffer.append(trace)
        return False  # keep running


def _probe_server_time(server: str, streams) -> UTCDateTime:
    """Query INFO=STREAMS and return the latest end_time across *streams*.

    Raises on failure so the caller can fall back gracefully.
    """
    from .info import parse_streams, query_info
    xml = query_info(server, level="STREAMS", timeout=10.0)
    records = parse_streams(xml)
    best = None
    for net, sta, loc, cha in streams:
        for r in records:
            if (r.get("network", "").upper() == net.upper()
                    and r.get("station", "").upper() == sta.upper()):
                try:
                    et = UTCDateTime(r["end_time"])
                    if best is None or et > best:
                        best = et
                except Exception:
                    continue
    if best is None:
        raise ValueError("no matching streams in INFO response")
    return best


def start_seedlink_worker(server: str, streams, buffer: "TraceBuffer",
                          backfill_seconds: int = 0,
                          no_clock: bool = False):
    """Start a daemon thread running an ``SLClient`` worker feeding ``buffer``.

    Parameters
    ----------
    server : str
        SeedLink server ``host:port``.
    streams : sequence of (net, sta, loc, cha) tuples, or a single such tuple
        Each element subscribes to one NSLC (LOC/CHA may contain ? / *
        wildcards natively). For backward compatibility, a single tuple is
        also accepted and treated as a one-element list.
    buffer : TraceBuffer
    backfill_seconds : int
        If > 0, ask the server to replay packets from that many seconds
        before now before transitioning to live streaming, so the viewer
        opens with recent history already drawn. On reconnect after a
        network blip we skip backfill — we want live data back ASAP, not a
        second copy of the same history.
    no_clock : bool
        If True, the server's clock may differ significantly from the local
        clock. The worker probes ``INFO=STREAMS`` to discover the server's
        latest timestamp and uses that as the reference for backfill instead
        of ``UTCDateTime()``. Falls back to no backfill if the probe fails.
    """
    # Accept either a single NSLC tuple (single-channel viewer) or a list of
    # them (multi-channel viewer).
    if streams and isinstance(streams[0], str):
        streams = [streams]

    parts = []
    for (net, sta, loc, cha) in streams:
        loc_field = loc if loc else "  "  # two spaces for blank location
        parts.append(f"{net}_{sta}:{loc_field}{cha}")
    multiselect = ",".join(parts)

    initial_begin_time = None
    if backfill_seconds > 0:
        if no_clock:
            try:
                server_now = _probe_server_time(server, streams)
                initial_begin_time = server_now - backfill_seconds
                print(f"--no-clock: server latest packet at {server_now}, "
                      f"requesting backfill from {initial_begin_time}")
            except Exception as e:
                print(f"--no-clock: could not probe server time ({e}); "
                      "starting without backfill.")
        else:
            initial_begin_time = UTCDateTime() - backfill_seconds

    def worker():
        begin_time = initial_begin_time
        reconnect_wait = 10.0
        while True:
            try:
                client = _ViewerBufferClient(buffer)
                client.slconn.set_sl_address(server)
                client.multiselect = multiselect
                client.begin_time = begin_time
                client.initialize()
                client.run()
                return  # clean exit
            except Exception as e:
                print(f"SeedLink worker error: {e}. "
                      f"Reconnecting in {reconnect_wait:.0f}s...")
                begin_time = None  # do not re-request backfill on reconnect
                time.sleep(reconnect_wait)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t
