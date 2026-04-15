"""SeedLink server INFO queries — a Python port of the most-used parts of slinktool.

Wraps ObsPy's ``basic_client.Client.get_info()`` and parses the returned XML
into Python dictionaries that the CLI (or downstream code) can format as
tables, JSON, or filter further.

Levels supported:

- ``ID``           — server identification + version
- ``CAPABILITIES`` — server capability flags (info levels, dial-up, etc.)
- ``STATIONS``     — list of stations the server is offering
- ``STREAMS``      — per-channel detail (NSLC + sample rate + time range)
- ``GAPS``         — recent gaps in the server's ring buffer (server-dependent)
- ``CONNECTIONS``  — currently connected clients (server-dependent; many servers
  redact or refuse this for non-trusted clients)
- ``ALL``          — everything the server supports, in one document
"""

import fnmatch
import socket
import struct
from typing import Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET


VALID_LEVELS = ("ID", "CAPABILITIES", "STATIONS", "STREAMS", "GAPS",
                "CONNECTIONS", "ALL")

# SeedLink wire constants
SL_PACKET_SIZE = 520           # 8-byte SLHEAD + 512-byte miniSEED record
SL_HEAD_SIZE = 8
SL_RECORD_SIZE = 512
SL_INFO_SIG = b"SLINFO"        # signature for INFO response packets


def parse_server(server: str) -> Tuple[str, int]:
    """Split ``host:port`` (port optional, defaults to 18000)."""
    if ":" in server:
        host, port = server.rsplit(":", 1)
        return host, int(port)
    return server, 18000


def query_info(server: str, level: str = "ID", timeout: float = 30.0) -> str:
    """Send a SeedLink ``INFO`` request and return the concatenated XML response.

    SeedLink INFO responses are a sequence of 520-byte packets, each of which
    is an 8-byte SLHEAD (``"SLINFO *"`` for continuation, ``"SLINFO  "`` for
    terminator) followed by a 512-byte miniSEED record whose *data section* is
    raw XML text. We splice the data sections together until we hit the
    terminator and return the result as a single XML document.

    Parameters
    ----------
    server : str
        ``host:port`` (port defaults to 18000 if omitted).
    level : str
        One of :data:`VALID_LEVELS`.
    timeout : float
        Socket timeout in seconds (applies to connect and to every read).
    """
    level = level.upper()
    if level not in VALID_LEVELS:
        raise ValueError(f"level must be one of {VALID_LEVELS}, got {level!r}")

    host, port = parse_server(server)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.sendall(f"INFO {level}\r\n".encode("ascii"))

        xml_parts: List[str] = []
        while True:
            packet = _recv_exactly(sock, SL_PACKET_SIZE)
            if packet is None:
                break  # connection closed before terminator

            slhead = packet[:SL_HEAD_SIZE]
            msrecord = packet[SL_HEAD_SIZE:SL_PACKET_SIZE]

            if not slhead.startswith(SL_INFO_SIG):
                # Could be a keep-alive or unexpected data packet — skip
                continue

            xml_parts.append(_extract_xml_payload(msrecord))

            # Terminator packet has SLHEAD "SLINFO  " (two trailing spaces);
            # continuation packets have "SLINFO *". Check the last byte.
            if slhead[7:8] != b"*":
                break

        # Best-effort polite disconnect; ignore failures.
        try:
            sock.sendall(b"BYE\r\n")
        except OSError:
            pass

        return "".join(xml_parts)
    finally:
        sock.close()


def _recv_exactly(sock: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly `n` bytes or return None if the connection closes first."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def _extract_xml_payload(record: bytes) -> str:
    """Return the text data section of a miniSEED record carrying INFO XML.

    SeedLink INFO miniSEED records use a text encoding (blockette 1000,
    encoding 0) where the "samples" are ASCII bytes. The Fixed Section of
    Data Header puts the sample count at bytes 30-31 (uint16, big-endian)
    and the data-section offset at bytes 44-45 (uint16, big-endian).
    """
    if len(record) < 48:
        return ""
    nsamples = struct.unpack(">H", record[30:32])[0]
    data_offset = struct.unpack(">H", record[44:46])[0]
    if not (0 < data_offset < len(record)):
        return ""
    end = min(data_offset + nsamples, len(record))
    return record[data_offset:end].decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# XML parsers — defensive about schema variants between SeisComP / ringserver /
# IRIS-DMC because slinktool servers don't all agree on attribute names.
# ---------------------------------------------------------------------------

def _strip_ns(tag: str) -> str:
    """Strip any XML namespace prefix from an element tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _attrib(elem, *names, default=""):
    """Return the first attribute that exists on `elem` from `names`, else default."""
    for n in names:
        if n in elem.attrib:
            return elem.attrib[n]
    return default


def parse_id(xml_str: str) -> Dict[str, str]:
    """Parse an INFO=ID (or =CAPABILITIES) response.

    Returns a dict of root-element attributes, e.g.
    ``{"software": "SeedLink v3.3", "organization": "...", "started": "..."}``.
    """
    root = ET.fromstring(xml_str)
    return dict(root.attrib)


def parse_stations(xml_str: str) -> List[Dict[str, str]]:
    """Parse INFO=STATIONS into a list of {network, station, description, ...}."""
    root = ET.fromstring(xml_str)
    out = []
    for st in root.iter():
        if _strip_ns(st.tag) != "station":
            continue
        out.append({
            "network": _attrib(st, "network"),
            "station": _attrib(st, "name", "station"),
            "description": _attrib(st, "description"),
            "begin_seq": _attrib(st, "begin_seq"),
            "end_seq": _attrib(st, "end_seq"),
            "stream_check": _attrib(st, "stream_check"),
        })
    return out


def parse_streams(xml_str: str) -> List[Dict[str, str]]:
    """Parse INFO=STREAMS into a list of NSLC + sample-rate + time-range records."""
    root = ET.fromstring(xml_str)
    out = []
    for st in root.iter():
        if _strip_ns(st.tag) != "station":
            continue
        net = _attrib(st, "network")
        sta = _attrib(st, "name", "station")
        for ch in st:
            if _strip_ns(ch.tag) != "stream":
                continue
            out.append({
                "network": net,
                "station": sta,
                "location": _attrib(ch, "location"),
                "channel": _attrib(ch, "seedname", "channel"),
                "type": _attrib(ch, "type"),
                "begin_time": _attrib(ch, "begin_time"),
                "end_time": _attrib(ch, "end_time"),
            })
    return out


def parse_gaps(xml_str: str) -> List[Dict[str, str]]:
    """Parse INFO=GAPS. Schema varies between server implementations — we
    return whatever ``<gap>`` elements expose as attributes, plus the parent
    station identification."""
    root = ET.fromstring(xml_str)
    out = []
    for st in root.iter():
        if _strip_ns(st.tag) != "station":
            continue
        net = _attrib(st, "network")
        sta = _attrib(st, "name", "station")
        for ch in st.iter():
            if _strip_ns(ch.tag) != "gap":
                continue
            row = {"network": net, "station": sta}
            row.update(ch.attrib)
            out.append(row)
    return out


def parse_connections(xml_str: str) -> List[Dict[str, str]]:
    """Parse INFO=CONNECTIONS. Many servers redact this for untrusted clients,
    in which case the result will be empty."""
    root = ET.fromstring(xml_str)
    out = []
    for el in root.iter():
        if _strip_ns(el.tag) not in ("client", "connection", "station_access"):
            continue
        out.append(dict(el.attrib))
    return out


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def filter_records(records: List[Dict[str, str]],
                   network: Optional[str] = None,
                   station: Optional[str] = None) -> List[Dict[str, str]]:
    """Client-side filter on network and/or station code (exact match, case-insensitive)."""
    out = records
    if network:
        n = network.upper()
        out = [r for r in out if r.get("network", "").upper() == n]
    if station:
        s = station.upper()
        out = [r for r in out if r.get("station", "").upper() == s]
    return out


def _has_wildcard(s: str) -> bool:
    return "?" in s or "*" in s


def expand_all_wildcards(server: str,
                         streams: Iterable[str],
                         timeout: float = 30.0) -> List[str]:
    """Expand wildcards in ALL four NSLC fields via one ``INFO=STREAMS`` query.

    Unlike :func:`expand_stream_wildcards` (which expands NET/STA only and
    leaves LOC/CHA for SeedLink's native wildcard support), this one resolves
    every pattern field into concrete NSLCs by matching against the server's
    full stream list. Use it when the caller needs to know the list of matched
    streams up front (e.g. the mc-viewer allocates one waveform panel per
    stream, so wildcards have to be expanded before the figure is built).

    Returns a list of fully-concrete ``NET.STA.LOC.CHA`` strings, deduplicated
    and sorted. Raises ``ValueError`` if any wildcarded spec matches zero
    streams on the server.
    """
    streams = list(streams)
    any_wild = any(
        any(_has_wildcard(x) for x in s.split("."))
        for s in streams if s.count(".") == 3
    )
    if not any_wild:
        return streams

    xml = query_info(server, level="STREAMS", timeout=timeout)
    available = parse_streams(xml)

    out: List[str] = []
    for spec in streams:
        bits = spec.split(".")
        if len(bits) != 4:
            raise ValueError(
                f"stream must be NET.STA.LOC.CHA (4 dot-separated fields), got {spec!r}"
            )
        net, sta, loc, cha = bits

        if not any(_has_wildcard(f) for f in (net, sta, loc, cha)):
            out.append(spec)
            continue

        matched = {
            (r.get("network", ""), r.get("station", ""),
             r.get("location", ""), r.get("channel", ""))
            for r in available
            if fnmatch.fnmatchcase(r.get("network", ""), net)
            and fnmatch.fnmatchcase(r.get("station", ""), sta)
            and fnmatch.fnmatchcase(r.get("location", ""), loc)
            and fnmatch.fnmatchcase(r.get("channel", ""), cha)
        }
        if not matched:
            raise ValueError(
                f"No streams on {server} match {spec!r}"
            )
        for nslc in sorted(matched):
            out.append(".".join(nslc))
    return out


def expand_stream_wildcards(server: str,
                            streams: Iterable[str],
                            timeout: float = 30.0) -> List[str]:
    """Expand ``?`` / ``*`` wildcards in NET and STA fields by querying the
    server's INFO=STREAMS, leaving LOC/CHA wildcards alone (SeedLink handles
    those natively).

    Each input is ``NET.STA.LOC.CHA``. If neither NET nor STA contains a
    wildcard, the spec passes through unchanged. Otherwise we ask the server
    what it offers and emit one explicit ``NET.STA.LOC.CHA`` per matching
    station, with the original LOC/CHA preserved.

    Raises ValueError if any wildcarded spec matches zero stations on the
    server — silent zero-match is almost always a typo and would otherwise
    leave the archiver subscribed to nothing.
    """
    streams = list(streams)
    if not any(
        _has_wildcard(s.split(".")[0]) or _has_wildcard(s.split(".")[1])
        for s in streams if s.count(".") == 3
    ):
        return streams

    xml = query_info(server, level="STREAMS", timeout=timeout)
    available = sorted({(r["network"], r["station"]) for r in parse_streams(xml)})

    out: List[str] = []
    for spec in streams:
        bits = spec.split(".")
        if len(bits) != 4:
            raise ValueError(
                f"stream must be NET.STA.LOC.CHA (4 dot-separated fields), got {spec!r}"
            )
        net, sta, loc, cha = bits
        if not (_has_wildcard(net) or _has_wildcard(sta)):
            out.append(spec)
            continue
        matched = [(n, st) for (n, st) in available
                   if fnmatch.fnmatchcase(n, net) and fnmatch.fnmatchcase(st, sta)]
        if not matched:
            raise ValueError(
                f"No stations on {server} match {spec!r} "
                f"(network={net!r}, station={sta!r})"
            )
        for (n, st) in matched:
            out.append(f"{n}.{st}.{loc}.{cha}")
    return out
