"""Rolling trace buffer and SeedLink client worker."""

import threading

from obspy import Stream, UTCDateTime
from obspy.clients.seedlink.easyseedlink import create_client


class TraceBuffer:
    """Thread-safe rolling buffer of incoming SeedLink packets."""

    def __init__(self, buffer_seconds: int):
        self.buffer_seconds = buffer_seconds
        self._stream = Stream()
        self._lock = threading.Lock()

    def append(self, trace):
        with self._lock:
            self._stream += trace
            self._stream.merge(method=1, fill_value=0)
            cutoff = UTCDateTime() - self.buffer_seconds
            self._stream.trim(starttime=cutoff)

    def latest(self, channel):
        """Return a copy of the most recent trace for `channel`, or None."""
        with self._lock:
            if len(self._stream) == 0:
                return None
            sel = self._stream.select(channel=channel)
            if len(sel) == 0:
                return None
            return sel[0].copy()

    def __len__(self):
        with self._lock:
            return len(self._stream)


def start_seedlink_worker(server: str, nslc, buffer: TraceBuffer):
    """Start a daemon thread running the SeedLink client and feeding `buffer`."""
    net, sta, _loc, cha = nslc

    def on_data(trace):
        buffer.append(trace)

    def worker():
        client = create_client(server, on_data=on_data)
        client.select_stream(net, sta, cha)
        client.run()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t
