# CLAUDE.md

This file gives Claude Code project-specific context for working on **SeedlinkPyUtils**.
Read this before making changes.

## Project overview

SeedlinkPyUtils is a Python package providing real-time SeedLink tools built on ObsPy.
It currently exposes two CLIs:

- **`seedlink-py-viewer`** — interactive matplotlib viewer with live waveform + spectrogram,
  filter selector, dark mode, and cross-platform fullscreen.
- **`seedlink-py-archiver`** — long-running daemon that subscribes to one or more
  SeedLink streams and writes them into an SDS miniSEED archive. Uses ObsPy's
  `SLClient` with a state file for resume-on-restart.
- **`seedlink-py-info`** — one-shot CLI that issues SeedLink INFO requests
  (`-I/-L/-Q/-G/-C`, mirroring `slinktool`) and pretty-prints the result, with
  optional JSON/XML output. Talks the SeedLink protocol directly over a TCP
  socket (no ObsPy SLClient subclass needed for one-shot queries) and parses
  the XML response with the stdlib `xml.etree.ElementTree`.

Target users are seismologists and network operators (the author works at the Geological
Survey of Canada). Primary deployment is against a SeisComP fdsnws/seedlink server at
`seiscomp.hakai.org` for the Hakai SchoolShake (Raspberry Shake `AM` network) and
Hakai broadband (`PQ` network) stations, but everything is meant to also work against
standard FDSN/SeedLink servers like IRIS.

## Repository layout

```
SeedlinkPyUtils/
├── pyproject.toml              # packaging; defines three console scripts
├── environment.yml             # conda env with editable pip install of self
├── requirements.txt            # for plain-pip users
├── README.md
├── LICENSE                     # MIT
├── .gitignore
└── src/seedlink_py_utils/
    ├── __init__.py             # exports run_viewer, run_archiver, query_info, ViewerConfig
    ├── config.py               # ViewerConfig dataclass, THEMES, FILTERS
    ├── buffer.py               # TraceBuffer + easyseedlink worker (viewer)
    ├── processing.py           # inventory loading, response removal, filters
    ├── gui.py                  # HRadioButtons, theme helpers, fullscreen
    ├── viewer.py               # run_viewer() — wires the viewer together
    ├── cli.py                  # viewer CLI (seedlink-py-viewer)
    ├── sds.py                  # SDS path construction
    ├── archiver.py             # SDSArchiver (subclass of SLClient), run_archiver()
    ├── archiver_cli.py         # archiver CLI (seedlink-py-archiver)
    ├── info.py                 # query_info() + XML parsers for INFO responses
    ├── info_cli.py             # info CLI (seedlink-py-info)
    └── logging_setup.py        # rotating file + console logger
```

The `src/` layout is intentional — keeps editable installs honest and prevents
accidentally importing from the working directory instead of the installed package.

## Architecture notes

### Viewer
- **Threading model:** SeedLink runs in a daemon thread via `easyseedlink.create_client`,
  pushing packets into a thread-safe `TraceBuffer`. The matplotlib `FuncAnimation` polls
  the buffer on its redraw timer. Lock contention is minimal because the buffer copy is
  cheap and redraws are at 1 Hz by default.
- **Filter scope:** filters are applied **only to the waveform panel**. The spectrogram
  always uses the response-removed-but-unfiltered trace, so changing the filter selector
  doesn't change the spectrogram. This is intentional — the spectrogram is for context.
- **Response removal:** runs once per redraw on the latest buffer copy, output in m/s.
  Falls back to raw counts (with axis label updated) if no inventory is available.
- **Fullscreen:** `gui.go_fullscreen()` is TkAgg-targeted with a Qt/Wx/macOS fallback.
  Tk on Linux often silently ignores the first `-fullscreen` request; the fix retries
  via `w.after(...)` and falls back to `overrideredirect(True)` + manual sizing for
  stubborn WMs (i3, sway, GNOME on Wayland with strict policies).

### Archiver
- **SLClient over easyseedlink:** chosen for the state-file capability. On restart, the
  client tells the server "I last saw sequence N for stream X" and the server replays
  anything it still has in its ring buffer. This survives short outages without data
  loss.
- **Direct miniSEED writes:** `slpacket.get_raw_data()` is appended to the SDS file as
  raw bytes. No round-trip through numpy — bit-identical to what the server sent. This
  matches what `slarchive` (the SeisComP reference tool) does.
- **SDS layout:** `<root>/<year>/<NET>/<STA>/<CHA>.D/<NET>.<STA>.<LOC>.<CHA>.D.<year>.<jday>`.
  Empty location codes appear as `..` in the filename (e.g. `PQ.DAOB..HHZ.D.2026.104`).
- **State save cadence:** every 60 seconds. Worst-case data loss on hard kill is ~1
  minute, which the server replays on next connect.
- **Reconnect loop:** `run_archiver()` wraps `SLClient.run()` in a try/except with
  configurable backoff. State is recovered on each reconnect.

### Info / discovery
- **One-shot, not streaming:** `seedlink-py-info` opens a connection, sends an
  INFO request, prints the parsed result, and exits. No daemon, no state file,
  no long-lived worker thread.
- **Talks the SeedLink wire protocol directly** (raw socket → `INFO LEVEL\r\n`
  → read 520-byte packets until terminator → splice the data sections of the
  miniSEED records back into one XML document). We do NOT use
  `obspy.clients.seedlink.basic_client.Client.get_info()` — that method is a
  metadata helper that takes FDSN-style `level='station'/'channel'/'response'`
  arguments, not the SeedLink `INFO` protocol command. See the bug-fix section
  in MEMORY.md.
- **XML parsing is intentionally defensive:** SeisComP, IRIS ringserver, and
  older SeedLink implementations don't all agree on attribute names (e.g.
  `name=` vs `station=` on `<station>` elements, `seedname=` vs `channel=` on
  `<stream>`). `info._attrib(elem, *names)` returns the first attribute that
  exists, so adding support for a new server flavour is usually one extra name
  in the alias list rather than a new code path.
- **`-G` and `-C` are server-dependent.** Many SeisComP installs disable GAPS
  reporting for performance, and most servers redact CONNECTIONS for
  non-trusted clients. An empty result here is normal, not a bug.

### Multiselect / wildcards
SeedLink natively supports `?` and `*` wildcards in **LOC and CHA only**. Wildcards in
NET or STA are not supported by the protocol — `build_multiselect()` raises a clear
ValueError if it sees them. To expand wildcards across stations, query the server's
`INFO=STREAMS` separately and pass an explicit list.

## Critical gotchas

### FDSN behind reverse proxy (`processing.py`)
The Hakai server has nginx mapping `http://seiscomp.hakai.org/fdsnws` →
`http://seiscomp.hakai.org:8080`, and the upstream SeisComP fdsnws serves at
`/fdsnws/...` internally. From the outside the working URL is
`http://seiscomp.hakai.org/fdsnws/fdsnws/station/1/query?...` (doubled `/fdsnws`).

The fix in `_make_fdsn_client()` is **try discovery first, fall back to explicit
service_mappings with appended `/fdsnws/`**:
1. `FDSNClient(base_url=base)` — works for IRIS, standard servers
2. On failure: `FDSNClient(base_url=base, service_mappings={...with /fdsnws appended...},
   _discover_services=False)` — works for SeisComP-behind-nginx

`_discover_services=False` is **required** in the fallback branch because the
constructor otherwise re-runs discovery and raises before the mappings are consulted.

When debugging FDSN issues, useful introspection:
- `fdsn._build_url("station", "query", {...})` returns the exact URL ObsPy will hit
- `logging.getLogger("obspy.clients.fdsn").setLevel(logging.DEBUG)` enables HTTP request
  logging

### Inventory cache
`load_inventory` writes/reads `./inv_<NET>_<STA>_<CHA>.xml` in the current working
directory. The cache short-circuits the FDSN fetch silently — when debugging FDSN
issues, **always `rm -f inv_*.xml` first** or pass `--no-cache`.

### Empty location codes
- CLI accepts `PQ.DAOB..HHZ` (double dot for empty LOC)
- SeedLink multiselect renders empty LOC as two spaces: `PQ_DAOB:  HHZ`
- SDS filenames render empty LOC as `..`: `PQ.DAOB..HHZ.D.2026.104`
- FDSN queries: pass `location=--` (sentinel) or omit the parameter; **don't** pass an
  empty string

Raspberry Shake (AM network) stations sometimes use empty LOC even when the user calls
them "00" — if metadata fetch returns 404 for `location=00`, try empty location.

### Matplotlib RadioButtons API change at 3.7
Pre-3.7: per-button `Circle` patches in `self.circles`.
Post-3.7: single `PathCollection` (scatter) in `self._buttons`.
`HRadioButtons._relayout_horizontal` handles both — use `hasattr` checks, not version
sniffing, when adding new widget customisation.

### Editable install gotcha
`pip install -e .` makes Python re-import from source on each launch, but does **not**
hot-reload a running process. Edits require restarting the running script. To verify
the installed module points at your edited source:
```bash
python3 -c "import seedlink_py_utils.processing as p; print(p.__file__)"
```

## Style and conventions

- **Python 3.9+** required (uses `dict | None` style sparingly; mostly `Optional[X]`).
- **Type hints** on public functions and dataclasses; loose elsewhere.
- **Logging:** archiver and library code use `logging` (logger name
  `seedlink_py_utils.<module>`). Viewer uses `print()` because its output is for
  interactive users, not log aggregation.
- **No global state for config** — pass `ViewerConfig` (or explicit args) into functions.
  The one exception is `current_filter` in `viewer.py` which lives inside `run_viewer`'s
  closure.
- **Docstrings:** numpy style (Parameters / Returns sections) for anything non-trivial.
- **Imports:** stdlib → third-party → local, separated by blank lines, alphabetised
  within each group.
- **No reformatting churn:** if you touch a file, don't reflow imports or strings that
  weren't related to your change.

## Testing

There are no automated tests yet. Manual smoke tests:

```bash
# Viewer against a local Raspberry Shake on Hakai
seedlink-py-viewer AM.RA382.00.EHZ -f -d

# Viewer against IRIS (verifies discovery branch still works)
seedlink-py-viewer IU.ANMO.00.BHZ \
    --server rtserve.iris.washington.edu:18000 \
    --fdsn https://service.iris.edu

# Archiver dry-run for ~1 minute
mkdir -p /tmp/sds_test
seedlink-py-archiver AM.RA382..EH? \
    --archive /tmp/sds_test \
    --state-file /tmp/sl_test_state.txt \
    --log-file /tmp/sl_test.log
# Ctrl-C, then:
ls -R /tmp/sds_test
```

If we add `pytest`, candidates for first tests:
- `archiver.build_multiselect` — pure function, easy unit tests with the existing
  hand-rolled assertions in MEMORY.md
- `sds.sds_path` — pure function
- `processing._make_fdsn_client` — mock urlopen, verify fallback triggers correctly

## Conventions for new utilities

If adding a new tool to the package:
1. New module under `src/seedlink_py_utils/` (e.g. `recorder.py`)
2. Public function exposed via `__init__.py`
3. CLI in a separate module (e.g. `recorder_cli.py`) with `main(argv=None)` entry point
4. Register in `pyproject.toml` `[project.scripts]`
5. Add a section to README under usage
6. Update this CLAUDE.md if architecture or gotchas change

Good candidates for future tools:
- Real-time multi-channel viewer (3-component, all channels of one station)
- PSD / PPSD live monitor (uses `obspy.signal.PPSD`)
- Stream availability dashboard (queries `INFO=STREAMS` periodically)
- Auto-restart wrapper for the archiver with watchdog and stale-data alerting

## Server context cheat sheet

| Network | Where | SeedLink | FDSN |
|---|---|---|---|
| AM (Raspberry Shake) | Hakai SchoolShake schools | `seiscomp.hakai.org:18000` | `http://seiscomp.hakai.org/fdsnws` |
| PQ (Hakai broadband) | Hakai stations | `seiscomp.hakai.org:18000` | `http://seiscomp.hakai.org/fdsnws` |
| IU, etc. (GSN) | IRIS | `rtserve.iris.washington.edu:18000` | `https://service.iris.edu` |
| CN (CNSN) | NRCan | `earthquakescanada.nrcan.gc.ca:18000` (verify) | `https://www.earthquakescanada.nrcan.gc.ca` (verify) |
