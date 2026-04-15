# CLAUDE.md

This file gives Claude Code project-specific context for working on **SeedlinkPyUtils**.
Read this before making changes.

## Project overview

SeedlinkPyUtils is a Python package providing real-time SeedLink tools built on ObsPy.
It currently exposes five CLIs:

- **`seedlink-py-viewer`** — interactive matplotlib viewer with live waveform + spectrogram,
  filter selector, dark mode, and cross-platform fullscreen.
- **`seedlink-py-mc-viewer`** — multi-channel variant (stacked waveform panels, no
  spectrogram) for 3-component viewing. Same filter and picker options as the
  single-channel viewer; picks are drawn across every panel with the picker
  running on the vertical component.
- **`seedlink-py-archiver`** — long-running daemon that subscribes to one or more
  SeedLink streams and writes them into an SDS miniSEED archive. Uses ObsPy's
  `SLClient` with a state file for resume-on-restart.
- **`seedlink-py-info`** — one-shot CLI that issues SeedLink INFO requests
  (`-I/-L/-Q/-G/-C`, mirroring `slinktool`) and pretty-prints the result, with
  optional JSON/XML output. Talks the SeedLink protocol directly over a TCP
  socket (no ObsPy SLClient subclass needed for one-shot queries) and parses
  the XML response with the stdlib `xml.etree.ElementTree`.
- **`seedlink-py-dashboard`** — live terminal dashboard that polls
  `INFO=STREAMS` every `--interval` seconds and renders a per-NSLC latency
  table with OK / LAG / STALE classification. Complement to
  `seedlink-py-info` (snapshot vs live). ANSI-colour TTY output; auto-falls-
  back to plain text when stdout is not a TTY. Reuses `info.query_info` +
  `info.parse_streams` — no new protocol code.

Target users are seismologists and network operators (the author works at the Geological
Survey of Canada). Primary deployment is against a SeisComP fdsnws/seedlink server at
`seiscomp.hakai.org` for the Hakai SchoolShake (Raspberry Shake `AM` network) and
Hakai broadband (`PQ` network) stations, but everything is meant to also work against
standard FDSN/SeedLink servers like IRIS.

## Repository layout

```
SeedlinkPyUtils/
├── pyproject.toml              # packaging; defines five console scripts
├── environment.yml             # conda env with the scientific stack (user does `pip install [-e] .` after)
├── requirements.txt            # for plain-pip users
├── README.md
├── LICENSE                     # MIT
├── .gitignore
└── src/seedlink_py_utils/
    ├── __init__.py             # exports run_viewer, run_archiver, query_info, ViewerConfig
    ├── config.py               # ViewerConfig dataclass, THEMES, FILTERS
    ├── buffer.py               # TraceBuffer + SLClient worker (viewer)
    ├── processing.py           # inventory loading, response removal, filters
    ├── gui.py                  # HRadioButtons, theme helpers, fullscreen
    ├── viewer.py               # run_viewer() — wires the viewer together
    ├── viewer_mc.py            # run_viewer_mc() — multi-channel variant
    ├── cli.py                  # viewer CLI (seedlink-py-viewer)
    ├── cli_mc.py               # mc-viewer CLI (seedlink-py-mc-viewer)
    ├── sds.py                  # SDS path construction
    ├── archiver.py             # SDSArchiver (subclass of SLClient), run_archiver()
    ├── archiver_cli.py         # archiver CLI (seedlink-py-archiver)
    ├── info.py                 # query_info() + XML parsers for INFO responses
    ├── info_cli.py             # info CLI (seedlink-py-info)
    ├── dashboard.py            # DashboardConfig, classify/render/run_dashboard
    ├── dashboard_cli.py        # dashboard CLI (seedlink-py-dashboard)
    ├── picker.py               # STA/LTA picker presets + runtime helper
    └── logging_setup.py        # rotating file + console logger
```

The `src/` layout is intentional — keeps editable installs honest and prevents
accidentally importing from the working directory instead of the installed package.

## Architecture notes

### Viewer
- **Threading model:** SeedLink runs in a daemon thread via an `SLClient` subclass
  (`_ViewerBufferClient` in `buffer.py`), pushing packets into a thread-safe
  `TraceBuffer`. The matplotlib `FuncAnimation` polls the buffer on its redraw
  timer. Lock contention is minimal because the buffer copy is cheap and redraws
  are at 1 Hz by default.
- **Startup backfill:** on first connect the worker sets `begin_time =
  now - buffer_seconds`, so the server replays recent history from its ring
  buffer and then transitions seamlessly into live streaming. The viewer opens
  pre-populated instead of empty. `--no-backfill` skips this for a live-only
  start. On reconnect after a network blip we do NOT re-request backfill —
  that would duplicate packets and the user just wants live data back.
- **Filter scope:** filters are applied **only to the waveform panel**. The spectrogram
  always uses the response-removed-but-unfiltered trace, so changing the filter selector
  doesn't change the spectrogram. This is intentional — the spectrogram is for context.
- **Filter selection mode:** if `cfg.filter_name` (CLI: `--filter NAME`) is set, the
  viewer locks the waveform to that preset and no filter widget is shown. If left
  unset (interactive), the filter selector is a native dropdown packed above the
  matplotlib canvas:
  - **TkAgg** → `ttk.Combobox` packed `before=canvas_widget` in the Tk window
  - **QtAgg / PyQt5/6 / PySide2/6** → `QComboBox` inside a `QToolBar` added to
    the `QMainWindow` via `addToolBar(TopToolBarArea, ...)`
  - **Other backends** → fallback to a legacy horizontal `RadioButtons` strip
    inserted as an extra gridspec row at the top
  `create_filter_dropdown` in `gui.py` is the dispatcher; it dispatches by
  `matplotlib.get_backend()` and returns `None` on unsupported backends (which
  is the fallback trigger). The native paths are preferred because they stay
  out of the figure's data area. CLI aliases are ASCII (`bp3-25`, `hp3`, etc.)
  since the canonical `FILTERS` keys have spaces and an en-dash —
  `FILTER_CLI_ALIASES` in `config.py` is the mapping.
- **Filter/picker naming is aligned.** Every `PICKER_PRESETS` key
  (`local`, `regional`, `tele-p`) also exists as a `--filter` alias in
  `FILTER_CLI_ALIASES`, and the picker's detection band matches the
  corresponding filter's band. So `--filter regional` and `--picker regional`
  both operate on BP 1–10 Hz; `--filter tele-p` and `--picker tele-p` both on
  BP 0.5–2 Hz; `--filter local` and `--picker local` both on BP 2–10 Hz. When
  adding or editing a preset, keep the two sides in sync (or rename the
  picker preset if divergence is genuinely needed).
- **STA/LTA picker:** when `cfg.picker_preset` is set (CLI: `--picker PRESET`), a
  CFT strip is inserted above the waveform and pick markers are drawn as red
  vertical axvlines on the waveform panel. The gridspec is built row-by-row
  (radio? + cft? + wf + sp) based on which features are active. The picker has
  its own detection filter band — **independent of the display filter** — so
  switching `--filter` doesn't change what the picker triggers on. Recomputation
  runs on every redraw (recursive_sta_lta is O(N)); if we ever need to go
  faster, the honest move is incremental STA/LTA on the streaming buffer, not
  vectorising what's there.
- **Response removal:** runs once per redraw on the latest buffer copy, output in m/s.
  Falls back to raw counts (with axis label updated) if no inventory is available.
- **Fullscreen:** `gui.go_fullscreen()` is TkAgg-targeted with a Qt/Wx/macOS fallback.
  Tk on Linux often silently ignores the first `-fullscreen` request; the fix retries
  via `w.after(...)` and falls back to `overrideredirect(True)` + manual sizing for
  stubborn WMs (i3, sway, GNOME on Wayland with strict policies).

### Multi-channel / multi-station viewer
- **One panel per resolved NSLC.** Streams are explicit (positional args)
  rather than discovered. Wildcards in any NSLC field auto-expand at
  startup via `info.expand_all_wildcards` (one-shot `INFO=STREAMS` query,
  fnmatch against the server's stream list), so by the time
  `run_viewer_mc` runs, every NSLC in `cfg.nslcs` is concrete. This is a
  stronger expansion than the archiver's `info.expand_stream_wildcards`,
  which expands NET/STA only and leaves LOC/CHA for SeedLink's native
  wildcards — the mc-viewer needs per-channel concreteness up-front so it
  can pre-allocate one panel per match.
- **Per-panel picker state.** Each panel carries its own `picks` list and
  `pick_artists`. This matters for multi-station views — a trigger on
  station A should not appear as a marker on station B's panel at the same
  absolute time, because each panel's x-axis is "seconds before now" and
  the onset times are station-specific. The picker preset (STA/LTA window,
  thresholds, detection band) is shared across panels; only the pick list
  is per-panel.
- **No CFT strip.** Deliberate: the value of this view is cross-panel
  visual correlation, and a single CFT strip wouldn't make sense with N
  different triggers running. Picker preset + band are named in the
  figure title instead.
- **Inventory is merged across stations.** `processing.load_inventory_multi`
  loads per unique (NET, STA) pair (reusing the per-station cache) and
  combines them with ObsPy's `Inventory.__add__`. `remove_response(inv)`
  on each Trace then matches by NSLC automatically, so one Inventory
  object services every panel.
- **Buffer lookups by full NSLC.** `TraceBuffer.latest_nslc(net, sta, loc,
  cha)` selects the right trace when multiple stations share a channel code
  (the old `latest(channel)` would return whichever station's trace came
  first in the Stream). The mc-viewer always uses `latest_nslc`; the
  single-channel viewer still uses `latest(channel)`.
- **Inventory cache filename sanitisation.** `load_inventory` strips `?`
  and `*` from the CHA field when constructing the cache filename, because
  Windows rejects those characters in filenames and the mc-viewer routinely
  passes `HH?` through. The FDSN query itself still uses the original
  wildcarded CHA (FDSN handles it natively).
- **Internal buffer is padded by `1 / pre_filt[0]` when response removal
  is active.** Constant-Q deconvolution with a pre_filt cosine taper
  produces a time-domain ringing artifact on the low-frequency side whose
  length scales with `1/f1`. Rather than show that ramp on every panel,
  the mc-viewer allocates `TraceBuffer(buffer_seconds + ceil(1/f1))` and
  requests backfill for the same length, while keeping
  `set_xlim(-buffer_seconds, 0)` — so the tapered region silently falls
  off the left edge. This is conditional on `inventory is not None`
  (counts mode applies no taper, no padding needed) and on `pre_filt[0] > 0`
  (degenerate guard). The picker's `cutoff = now - buffer_seconds` already
  drops picks from the padded region, so padding also gives STA/LTA a
  longer clean lead-in for free. Only the mc-viewer does this currently;
  the single-channel viewer has the same taper issue but has not been
  ported yet (would be a one-liner if asked for).

### Archiver
- **SLClient with state file:** the state file records the last sequence number
  per stream. On restart the client tells the server "I last saw sequence N for
  stream X" and the server replays anything it still has in its ring buffer —
  surviving short outages without data loss. The viewer also uses SLClient (for
  the backfill-on-start feature) but doesn't maintain a state file, since
  viewer history doesn't need to survive a process restart.
- **Direct miniSEED writes:** `slpack.msrecord` is appended to the SDS file as
  raw bytes. No round-trip through numpy — bit-identical to what the server sent.
  This matches what `slarchive` (the SeisComP reference tool) does.
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

### Stream availability dashboard
- **Terminal-first, not matplotlib.** The use case is "SSH into a box and
  leave it running; glance at it on a side monitor" — that's a TTY
  dashboard (htop-style), not a figure. Matplotlib would add weight for no
  gain here. A matplotlib heatmap mode is a reasonable future addition
  but was explicitly skipped for v1.
- **Reuses `info.query_info` + `info.parse_streams`.** No new protocol
  code. The dashboard module is purely presentation + polling loop on top
  of the existing one-shot INFO machinery. If `info` grows new server-
  flavour attribute aliases, the dashboard gets them for free.
- **Latency = `now - end_time`.** INFO=STREAMS gives us a per-NSLC
  `end_time` (the latest record the server has); subtracting from
  `UTCDateTime.now()` is the definition of stream latency we classify
  against. Negative values (server clock ahead of ours, or NTP skew)
  classify as OK, not a degenerate bucket — data is flowing, we just have
  clock skew.
- **Separation of pure logic and I/O.** `classify`, `_fmt_latency`,
  `compute_rows`, and `render` are deterministic and take only plain
  data; only `run_dashboard` opens the socket. That split exists so the
  presentation logic is unit-testable without a live server.
- **Auto-downgrade when stdout is not a TTY.** `run_dashboard` checks
  `sys.stdout.isatty()` up front and disables colour + screen-clear when
  running under a pipe / redirect. So `seedlink-py-dashboard > log.txt`
  produces a readable growing log rather than a sea of `\x1b[...` bytes.
- **Per-poll failures don't kill the loop.** A network blip / server
  temporarily refusing INFO shows up as a one-line `Poll failed: …` in
  place of the frame and the loop continues. The dashboard is expected
  to run for days across flaky connections.
- **`UNKNOWN` is for unparseable `end_time`, not zero latency.**
  A stream the server is silent on still has an `end_time` (timestamp of
  the last packet it ever saw); that just classifies as `STALE` via the
  large latency. `UNKNOWN` only fires when `end_time` is missing or
  `UTCDateTime()` can't parse it — a schema surprise, not an operational
  state.

### Multiselect / wildcards
SeedLink natively supports `?` and `*` wildcards in **LOC and CHA only** — these are
sent verbatim in the `SELECT` command. Wildcards in NET or STA are not part of the
protocol (the `STATION` command takes a literal `NET STA` pair), so
`build_multiselect()` raises a clear ValueError if it sees one.

To work around the protocol limit, the archiver supports `--expand-wildcards`
(library: `run_archiver(expand_wildcards=True)`), which calls
`info.expand_stream_wildcards()`. That helper issues one `INFO=STREAMS` query at
startup, matches each wildcarded `NET.STA` against the server's reply with
`fnmatch.fnmatchcase`, and substitutes the explicit station list before
`build_multiselect()` runs. LOC/CHA wildcards in the same spec are preserved (still
handled natively by SeedLink). A wildcard that matches zero stations raises rather
than silently subscribing to nothing.

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

### ObsPy archiver API landmines

All documented in detail in MEMORY.md, but this is the short list so you
don't re-fall into them when editing `archiver.py`:

- **`SLPacket.TYPE_SLDATA` does not exist** — the `packet_handler` gate
  should filter out INFO packets (`TYPE_SLINF`, `TYPE_SLINFT`) and treat
  everything else as data.
- **`SLPacket.get_raw_data()` does not exist** — raw 512-byte miniSEED
  record is the attribute `slpack.msrecord`.
- **`SLClient.__init__(loglevel=...)` is deprecated and ignored** in ObsPy
  ≥1.4 — pass nothing, configure logging via `logging_setup.py`.
- **`slconn.save_state(statefile)` and `recover_state(statefile)` ignore
  their parameter** — they both read `self.statefile`. Workaround: set
  `client.slconn.statefile = state_file` once before using the connection.
- **Call `client.initialize()` BEFORE `recover_state`.** `recover_state`
  matches the state file against `slconn.streams`, which stays empty until
  `initialize()` parses `multiselect`. Wrong order → silent "no matching
  streams" → archiver starts from the live tip and the state file is
  effectively ignored.

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
- PSD / PPSD live monitor (uses `obspy.signal.PPSD`)
- Auto-restart wrapper for the archiver with watchdog and stale-data alerting
- Matplotlib heatmap mode for the dashboard (longitudinal view across many
  stations at a glance; complements the TTY table)

## Server context cheat sheet

| Network | Where | SeedLink | FDSN |
|---|---|---|---|
| AM (Raspberry Shake) | Hakai SchoolShake schools | `seiscomp.hakai.org:18000` | `http://seiscomp.hakai.org/fdsnws` |
| PQ (Hakai broadband) | Hakai stations | `seiscomp.hakai.org:18000` | `http://seiscomp.hakai.org/fdsnws` |
| IU, etc. (GSN) | IRIS | `rtserve.iris.washington.edu:18000` | `https://service.iris.edu` |
| CN (CNSN) | NRCan | `earthquakescanada.nrcan.gc.ca:18000` (verify) | `https://www.earthquakescanada.nrcan.gc.ca` (verify) |
