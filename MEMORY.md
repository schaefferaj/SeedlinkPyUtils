# MEMORY.md

Long-term context for SeedlinkPyUtils — design decisions, historical bug fixes, and
the reasoning behind non-obvious choices. This is the project's institutional memory.
Update it when you make a non-trivial decision or solve a non-obvious problem so the
next session (you or another contributor) doesn't have to re-derive it.

## Origin

Started April 2026 as a single script — a real-time SeedLink trace viewer for the
author (A. Schaeffer, GSC Pacific) to monitor SchoolShake stations on the Hakai
network. Evolved through several iterations:

1. Bare seedlink → matplotlib trace viewer
2. Added live spectrogram panel (scipy.signal.spectrogram, magma colormap)
3. Added FDSN response removal to plot in m/s
4. Added filter selector (radio buttons) — applied to waveform only, not spectrogram
5. Added dark mode and cross-platform fullscreen
6. Refactored monolithic script into installable `seedlink_py_utils` package
7. Renamed package/repo to `SeedlinkPyUtils` (Python-specific naming)
8. Added archiver as second tool (SLClient + SDS output)
9. Added `seedlink-py-info` for INFO queries (slinktool-style flags,
   `basic_client.Client.get_info` under the hood, defensive XML parsing
   to handle SeisComP/ringserver schema differences)

## Key design decisions

### Why `SLClient` for archiver, `easyseedlink` for viewer

The viewer's data needs are tolerant — if it misses 30 s during a network blip, the
display catches up. Simple push-callback via `easyseedlink.create_client` is enough.

The archiver must not lose data. `SLClient` exposes a state file (last sequence number
per stream) that lets the server backfill missed packets on reconnect, as long as the
gap fits within the server's ring buffer (typically ~hours to a day).

### Why direct `slpacket.get_raw_data()` writes vs Stream.write()

The naive approach `Stream.write(path, format='MSEED', flush=True)` works but
re-encodes the trace through ObsPy's miniSEED writer, which can change encoding
choices (Steim2 vs Steim1, record length, etc.) compared to what the server sent.
Appending raw packet bytes preserves bit-identical data and is faster. This is the
same approach `slarchive` (the SeisComP reference C tool) takes.

### Why filter affects waveform only, not spectrogram

A user testing a 3 Hz highpass to look for a small local event should still see the
broadband microseism band on the spectrogram for context. Filtering the spectrogram
along with the waveform would be misleading — it would show artificially nulled-out
low-frequency content. The decoupling is intentional.

### Why no colorbar on the spectrogram

The user's reference image (a SeisComP screenshot) didn't have one. The dB scale is
relative anyway (we're using power density in (m/s)²/Hz, but without absolute
calibration verification it's "log power, vibes-based units"). A colorbar would
suggest more precision than we have. If a user wants quantitative PSD they should
use `obspy.signal.PPSD` against full noise models.

### Why dataclass for ViewerConfig but plain function args for archiver

Viewer has 18+ tunables and gets called as `run_viewer(cfg)` from both the CLI and
the Python API — a dataclass is cleaner than a long kwargs signature. Archiver has
fewer parameters and is more procedural; explicit kwargs read better and avoid the
cognitive overhead of a second config class. If the archiver grows past ~10
parameters, refactor to `ArchiverConfig`.

## Bug fixes worth remembering

### FDSN behind nginx — the doubled `/fdsnws` saga

**Symptom:** `seedlink-py-viewer AM.RA382.00.EHZ --fdsn http://seiscomp.hakai.org/fdsnws`
failed with "No FDSN services could be discovered at...".

**Root cause:** Two layered:
1. `FDSNClient.__init__` runs service discovery on the base URL by probing
   `/version`, `/application.wadl`, etc. With nginx mapping `/fdsnws` → upstream,
   those probes hit `/fdsnws/version` which doesn't exist (the upstream serves
   `/fdsnws/version`, so the doubled path is needed). Discovery fails, constructor
   raises before `service_mappings` are consulted.
2. The Hakai nginx setup specifically requires `/fdsnws/fdsnws/` — the proxy maps
   `/fdsnws` to `:8080` and SeisComP at :8080 also serves at `/fdsnws/`. From outside,
   you need both.

**Fix:** `_make_fdsn_client()` in `processing.py` tries discovery first (works for
IRIS), and on failure builds an `FDSNClient` with `_discover_services=False` and
explicit `service_mappings` that include the extra `/fdsnws/` segment. Both Hakai
and IRIS work without the user needing to know which mode they need.

**Don't forget:** `_discover_services=False` is private API but has been stable in
ObsPy for years. If a future ObsPy version breaks it, the alternative is to use
`urllib.request.urlopen` directly to fetch the StationXML and pass to
`read_inventory(BytesIO(...))` — bypasses `FDSNClient` entirely. We discussed this
approach but rejected it because it loses ObsPy's automatic redirect/retry handling
and feels like overcorrecting.

### TkAgg fullscreen silently ignored on Linux

**Symptom:** `--fullscreen` flag had no error but window stayed normal-sized.

**Root cause:** `w.attributes("-fullscreen", True)` is a *request* to the WM. Some
WMs ignore requests sent before the window is fully mapped. GNOME-on-Wayland and
i3/sway are the usual culprits.

**Fix:** `gui.go_fullscreen()` does three things:
1. Calls `w.update_idletasks()` + `w.update()` to force the window to be realized
2. Tries `-fullscreen` and verifies the attribute reads back True
3. Re-runs the request via `w.after(100, ...)` and `w.after(500, ...)` to give the
   WM time to settle
4. Falls back to `w.overrideredirect(True)` + manual size-to-screen if all of the
   above fail — this strips window decorations entirely

The `overrideredirect` fallback isn't true fullscreen (the WM doesn't know about it,
so multi-monitor focus might behave oddly), but it's visually identical for this
use case.

### `basic_client.Client.get_info()` is NOT the SeedLink INFO command

**Symptom:** Initial implementation of `seedlink-py-info` raised
`Invalid option for 'level': 'STREAMS'` (and same for `STATIONS`, `ID`, …)
on every query.

**Root cause:** ObsPy has two unrelated APIs that both happen to be called
"get info" for SeedLink:

1. `obspy.clients.seedlink.basic_client.Client.get_info()` — a *metadata*
   helper that takes FDSN-style `level='station' | 'channel' | 'response'`
   arguments and (under the hood) builds a station list. It does NOT speak
   the SeedLink protocol's `INFO` command.
2. The actual SeedLink wire-protocol `INFO LEVEL` command (where LEVEL is
   `ID`, `STATIONS`, `STREAMS`, `GAPS`, `CONNECTIONS`, etc.), which returns
   a chain of 520-byte packets whose miniSEED data sections concatenate
   into an XML document.

The constructor accepted our uppercase strings without error (because
`get_info` doesn't validate at construction time) but raised inside
`get_info` because `STREAMS` isn't one of `(station, channel, response)`.

**Fix:** Don't use `basic_client` for this — talk the SeedLink protocol
directly via a TCP socket. `info.query_info()` opens a connection, sends
`INFO LEVEL\r\n`, reads packets until it sees the terminator SLHEAD
(`"SLINFO  "` with two trailing spaces, vs `"SLINFO *"` for continuation
packets), extracts each packet's data section using the miniSEED data-offset
field at FSDH bytes 44-45, and concatenates. ~50 lines, no protocol
dependency beyond the stdlib.

**Don't forget:** if a future ObsPy adds a real SeedLink-INFO wrapper, by
all means migrate — but don't be fooled by the name `get_info` again.

### matplotlib RadioButtons API change

In matplotlib 3.7, the per-button `Circle` patches were consolidated into a single
`PathCollection` accessed via `self._buttons` (scatter-based). `HRadioButtons` uses
`hasattr(self, "_buttons")` then `hasattr(self, "circles")` to support both. Don't
use version sniffing (`matplotlib.__version__`) — the attribute check is more
robust against forks and patched releases.

## Conventions and idioms

### Empty location codes

Internal representations vary by interface, so be explicit:
- **CLI input:** `PQ.DAOB..HHZ` (double dot)
- **In code:** `loc = ""` (empty string)
- **SeedLink multiselect:** `PQ_DAOB:  HHZ` (two literal spaces)
- **SDS filename:** `..` (double dot, same as CLI)
- **FDSN query parameter:** `location=--` (double dash sentinel) or omit entirely

The `build_multiselect()` and `sds_path()` functions handle the conversion. New code
that touches NSLC should follow the same conventions.

### Inventory caching

Cache file: `./inv_<NET>_<STA>_<CHA>.xml` in CWD. The `--no-cache` flag bypasses
both read and write. **When debugging FDSN, always clear the cache first** — the
cache will silently mask FDSN bugs by serving stale data.

The cache is intentionally per-channel, not per-station. This is wasteful for
3-component sites but keeps the logic simple and makes single-channel viewer use
work without fetching unnecessary metadata.

### Logging vs printing

- **Library code in viewer path** (`processing.py`, `viewer.py`, `gui.py`,
  `buffer.py`): `print()`. The viewer is interactive; users see the messages.
- **Library code in archiver path** (`archiver.py`, `logging_setup.py`): `logging`.
  The archiver runs as a service; logs go to file with rotation.
- **Don't mix:** if you add a function used by both (e.g. shared inventory loading),
  pass a logger or a print-like callable rather than picking one and forcing it
  on the other side.

## Things explicitly considered and rejected

### A YAML/JSON config file for the viewer

Tempting because the CLI is getting long. Rejected because:
- The current set of flags isn't *that* long once you know your defaults
- Most users will alias their common invocation in their shell rc
- Adding a config file means precedence rules (file vs CLI vs env), help text gets
  more complex, and we'd need a config-validation layer

If the CLI grows another 10+ flags, revisit. A `--config CONFIG_FILE` flag that
loads YAML and uses CLI values as overrides is the obvious shape.

### A web-based viewer (Bokeh/Plotly Dash/Streamlit)

Would be nice for headless server monitoring. Rejected for v1 because matplotlib is
already a dependency, web frameworks add significant install weight, and the use
case (a single seismologist watching a few stations) is well-served by a desktop
window. If multi-user remote monitoring becomes a need, that's a separate project,
not a feature of this one.

### Auto-discovery of streams via `INFO=STREAMS`

For the archiver, we considered letting users specify `AM.*..EH?` and having the
client expand the wildcard against the server's available streams. Rejected for v1
because:
- Adds an extra round-trip and parsing layer at startup
- Users who want this can `seiscomp-fdsnws-stationlist` once and paste the list
- The `SLClient` multiselect approach is well-understood and matches `slarchive`

Worth adding later behind a `--expand-wildcards` flag if users ask.

## Testing notes (manual)

When changing core paths, always smoke-test against:
1. Hakai (`AM.RA382.00.EHZ`) — exercises the nginx /fdsnws fallback path
2. IRIS (`IU.ANMO.00.BHZ`) — exercises the standard FDSN discovery path
3. A station with empty location code (`PQ.DAOB..HHZ`) — exercises empty-LOC handling

If response removal breaks for one of these, that's the regression test.

For the archiver, run for ≥2 minutes to catch the 60-second state save, then Ctrl-C
and verify:
- An SDS file was written at the expected path
- `python3 -c "from obspy import read; print(read('path/to/file'))"` parses it
- A second invocation with the same `--state-file` doesn't re-fetch already-archived
  packets (check the log for "Recovered state from..." message)

## Open questions / future work

- Should the viewer support multiple channels in stacked panels? (User has asked for
  3-component view)
- Should the archiver compute basic QC metrics (gaps, latency, completeness) and emit
  them somewhere? Currently only logs heartbeats.
- The `WATER_LEVEL=60` default for response removal is conservative; could be tuned
  per-instrument-type. Not worth doing until a user asks.
