# SeedlinkPyUtils

Real-time [SeedLink](https://www.seiscomp.de/doc/apps/seedlink.html) tools in Python,
built on [ObsPy](https://docs.obspy.org). Provides:

- **`seedlink-py-viewer`** — interactive trace + spectrogram viewer
- **`seedlink-py-archiver`** — robust SLClient-based archiver that writes an
  [SDS](https://www.seiscomp.de/seiscomp3/doc/applications/slarchive/SDS.html)
  miniSEED archive
- **`seedlink-py-info`** — query a SeedLink server for stations, streams, gaps,
  and active connections (a Python port of `slinktool`'s INFO queries)

## Features

### Viewer (`seedlink-py-viewer`)
- Live waveform + synchronised spectrogram in a rolling time window
- Automatic instrument response removal via FDSN or a local StationXML file
  (falls back to raw counts if unavailable)
- Interactive filter selector (bandpasses and highpasses) applied to the waveform only,
  leaving the spectrogram broadband
- Light and dark themes
- Cross-platform fullscreen mode (Linux / macOS / Windows / WSL) with a TkAgg-targeted
  fallback for stubborn window managers

### Archiver (`seedlink-py-archiver`)
- Robust `SLClient`-based connection with state file for resume-on-restart — no data
  loss across short outages if the server still has it buffered
- Multiple streams per invocation, with SeedLink wildcards (`?`, `*`) in LOC and CHA
  natively, and in NET/STA via `--expand-wildcards` (one extra INFO=STREAMS query)
- Writes standard SDS layout: one file per day per channel, appended in real time
- Writes raw miniSEED records byte-identically (no round-trip through numpy)
- Automatic reconnection with configurable backoff
- Rotating log file (10 MB × 5 backups) with console heartbeat

### Info / discovery (`seedlink-py-info`)
- `slinktool`-style flags: `-I` server id, `-L` stations, `-Q` streams,
  `-G` gaps, `-C` connections
- Client-side filtering by `--network` and `--station`
- Output as a human-readable table (default), JSON (`--json`), or raw XML (`--xml`)

## Installation

### Conda (recommended)

Two steps — create the environment with the scientific stack from `conda-forge`,
then install this package into it yourself so you choose between a regular or
editable install:

```bash
git clone https://github.com/schaefferaj/SeedlinkPyUtils.git
cd SeedlinkPyUtils

# 1. Create and activate the environment (scientific dependencies only)
conda env create -f environment.yml
conda activate seedlink-py-utils

# 2. Install the package itself
pip install .          # regular install (pinned snapshot of the current tree)
# or
pip install -e .       # editable install — code changes take effect on next run
```

The editable mode is what you want if you're hacking on the package; the regular
install is the right default for a production machine.

### Plain pip

```bash
git clone https://github.com/schaefferaj/SeedlinkPyUtils.git
cd SeedlinkPyUtils
pip install -e .
```

Or directly from GitHub:

```bash
pip install git+https://github.com/schaefferaj/SeedlinkPyUtils.git
```

## Usage

### Viewer

After installation, the `seedlink-py-viewer` command is on your path.

```bash
# Basic: stream in NET.STA.LOC.CHA form (empty LOC uses double dots)
seedlink-py-viewer AM.RA382.00.EHZ
seedlink-py-viewer PQ.DAOB..HHZ

# Dark mode, fullscreen (press Esc to exit)
seedlink-py-viewer AM.RA382.00.EHZ --dark-mode --fullscreen

# Lock to a preset filter — hides the radio-button strip
seedlink-py-viewer PQ.DAOB..HHZ --filter hp3

# Teleseismic P-wave view on a broadband — --pre-filt is auto-lowered for
# 'surface' and 'tele-p' so the response removal doesn't mute the band
seedlink-py-viewer IU.ANMO.00.BHZ --filter tele-p

# Point at a different SeedLink server and FDSN for metadata
seedlink-py-viewer IU.ANMO.00.BHZ \
    --server rtserve.iris.washington.edu:18000 \
    --fdsn https://service.iris.edu \
    --buffer 600 --fmax 20

# Skip response removal and just plot counts
seedlink-py-viewer AM.RA382.00.EHZ --fdsn ''

# Use a local StationXML instead of fetching
seedlink-py-viewer PQ.DAOB..HHZ --inventory ./my_inventory.xml
```

Run `seedlink-py-viewer --help` for the full list of options.

### Archiver

The archiver runs as a long-lived process that subscribes to one or more streams and
writes them into an SDS miniSEED archive. It uses ObsPy's `SLClient` with a state
file, so after a restart or network outage it resumes from the last sequence number
and the server will backfill anything it still has buffered.

```bash
# Single station, three channels
seedlink-py-archiver AM.RA382..EH? --archive /data/sds

# Multiple stations, blank locations, with state file and rotating log
seedlink-py-archiver AM.RA382..EH? AM.RA481..EH? PQ.DAOB..HH? \
    --server seiscomp.hakai.org:18000 \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log

# Replay a historical window from the server's ring buffer
seedlink-py-archiver AM.RA382..EHZ \
    --archive /data/sds \
    --begin-time 2026-04-14T12:00:00 \
    --end-time   2026-04-14T13:00:00

# Subscribe to every station in the AM network (single-quote to stop the shell
# from globbing the asterisk before argparse sees it)
seedlink-py-archiver 'AM.*..EH?' --archive /data/sds --expand-wildcards
```

**Stream syntax.** `NET.STA.LOC.CHA`, with `?` and `*` wildcards allowed in LOC and CHA
natively (SeedLink's own multiselect). Empty LOC is written as two dots (e.g.
`PQ.DAOB..HHZ`). Wildcards in NET or STA are *not* part of the SeedLink protocol —
the `--expand-wildcards` flag works around this by issuing a one-shot `INFO=STREAMS`
query at startup and substituting the matching explicit station list before
subscribing. Quote any wildcard spec on the command line so the shell doesn't
expand it as a filename glob.

**SDS layout.** The archive is organised as:

```
<archive>/<YEAR>/<NET>/<STA>/<CHA>.D/<NET>.<STA>.<LOC>.<CHA>.D.<YEAR>.<JDAY>
```

One file per NSLC per day, appended as packets arrive. This is the standard SeisComP /
SLarchive layout and is readable by ObsPy's `SDSClient` and most SEED-aware tools.

**Running as a service.** A minimal systemd unit file for production use:

```ini
[Unit]
Description=SeedLink to SDS archiver
After=network.target

[Service]
Type=simple
User=seismo
ExecStart=/opt/conda/envs/seedlink-py-utils/bin/seedlink-py-archiver \
    AM.RA382..EH? AM.RA481..EH? PQ.DAOB..HH? \
    --server seiscomp.hakai.org:18000 \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

Run `seedlink-py-archiver --help` for the full list of options.

### Info / discovery

`seedlink-py-info` queries the server for what's available — the same kinds of
INFO requests that SeisComP's `slinktool` exposes. The flag set mirrors `slinktool`
so existing muscle memory transfers.

```bash
# Server identification + capabilities
seedlink-py-info -I

# All stations the server is offering
seedlink-py-info -L

# All streams (NSLC + sample-rate + time range), filtered to one network
seedlink-py-info -Q --network AM

# Streams for one station as JSON, against IRIS instead of the default
seedlink-py-info -Q --station ANMO --json rtserve.iris.washington.edu:18000

# Recent gaps (server-dependent — many SeisComP installs disable this)
seedlink-py-info -G

# Active client connections (often redacted by the server)
seedlink-py-info -C
```

The default server is `seiscomp.hakai.org:18000`, matching the archiver. Pass any
other `host:port` as a positional argument.

Run `seedlink-py-info --help` for the full list of options.


### As a Python API

```python
# Viewer
from seedlink_py_utils import ViewerConfig, run_viewer

cfg = ViewerConfig(
    nslc=("AM", "RA382", "00", "EHZ"),
    seedlink_server="seiscomp.hakai.org:18000",
    fdsn_server="http://seiscomp.hakai.org/fdsnws",
    buffer_seconds=300,
    dark_mode=True,
)
run_viewer(cfg)

# Archiver
from seedlink_py_utils import run_archiver
from seedlink_py_utils.logging_setup import setup_logger

setup_logger(log_file="/var/log/slarchiver.log")
run_archiver(
    server="seiscomp.hakai.org:18000",
    streams=["AM.RA382..EH?", "PQ.DAOB..HH?"],
    archive_root="/data/sds",
    state_file="/var/lib/slarchiver/state.txt",
)

# Info / discovery
from seedlink_py_utils import query_info
from seedlink_py_utils.info import parse_streams, filter_records

xml = query_info("seiscomp.hakai.org:18000", level="STREAMS")
streams = filter_records(parse_streams(xml), network="AM")
for s in streams:
    print(s["network"], s["station"], s["location"], s["channel"])
```

## Viewer configuration reference

| Flag | Default | Description |
|---|---|---|
| `stream` (positional) | — | `NET.STA.LOC.CHA`, e.g. `AM.RA382.00.EHZ` |
| `--server`, `-s` | `seiscomp.hakai.org:18000` | SeedLink server `host:port` |
| `--fdsn` | `http://seiscomp.hakai.org/fdsnws` | FDSN-WS base URL (empty string to disable) |
| `--inventory` | — | Local StationXML file (overrides `--fdsn`) |
| `--no-cache` | off | Skip the on-disk inventory cache |
| `--buffer`, `-b` | `300` | Rolling buffer length (seconds) |
| `--redraw-ms` | `1000` | Redraw interval (ms) |
| `--nperseg` | `512` | FFT window length (samples) |
| `--noverlap` | `400` | FFT window overlap (samples) |
| `--fmin` / `--fmax` | `0.5` / `50.0` | Spectrogram frequency range (Hz) |
| `--db-clip` | `-180,-100` | Spectrogram dB colour limits |
| `--cmap` | `magma` | Matplotlib colormap |
| `--water-level` | `60` | Deconvolution water level |
| `--pre-filt` | `0.05,0.1,45,50` | Response pre-filter corners |
| `--filter` | — | Lock to a preset filter and hide the radio buttons (see *Filter presets* below). Omit for the interactive selector. |
| `--fullscreen`, `-f` | off | Fullscreen, no toolbar |
| `--dark-mode`, `-d` | off | Dark colour theme |

### Filter presets

Grouped by use case. The CLI alias is what you pass to `--filter`; the canonical
name is what appears on the radio-button strip in interactive mode.

**Teleseismic (long-period, broadband / GSN-style instruments):**

| Alias | Preset | Use case |
|---|---|---|
| `surface` | BP 0.02–0.1 Hz | Rayleigh/Love surface waves; primary microseism band |
| `tele-p` | BP 0.5–2 Hz | Teleseismic P (classic WWSSN short-period band) |

**Regional:**

| Alias | Preset | Use case |
|---|---|---|
| `regional` | BP 1–10 Hz | Regional earthquakes (Pg/Pn/Sg/Sn) |

**Local (high-frequency, Raspberry Shake / short-period):**

| Alias | Preset | Use case |
|---|---|---|
| `bp1-25` | BP 1–25 Hz | Local events, wideband view |
| `bp3-25` | BP 3–25 Hz | Local events, high-frequency emphasis |
| `hp1` | HP 1 Hz | Remove microseism and DC |
| `hp3` | HP 3 Hz | Remove ocean/urban low-frequency noise |
| `hp5` | HP 5 Hz | Aggressive HP for very noisy sites |

**Off:**

| Alias | Preset | Use case |
|---|---|---|
| `none` | None | Response-removed trace without extra filtering |

> **Caveat for long-period presets.** The default `--pre-filt 0.05,0.1,45,50`
> tapers out content below 0.05 Hz during response removal, which would mute
> most of what `surface` (BP 0.02–0.1 Hz) wants to pass. The CLI auto-lowers
> `--pre-filt` to `0.005,0.01,45,50` when you pick `surface` or `tele-p`, and
> prints a note on startup. Pass `--pre-filt` explicitly to override (your
> value always wins).

## Archiver configuration reference

| Flag | Default | Description |
|---|---|---|
| `streams` (positional, 1+) | — | One or more `NET.STA.LOC.CHA` (wildcards in LOC/CHA) |
| `--server`, `-s` | `seiscomp.hakai.org:18000` | SeedLink server `host:port` |
| `--archive`, `-a` | — (required) | SDS archive root directory |
| `--state-file` | — | SeedLink state file for resume on restart |
| `--begin-time` | — | Replay window start (ISO 8601) |
| `--end-time` | — | Replay window end (ISO 8601) |
| `--reconnect-wait` | `10` | Seconds between reconnect attempts |
| `--max-reconnects` | unlimited | Cap on reconnect attempts |
| `--expand-wildcards` | off | Expand `?` / `*` in NET/STA via `INFO=STREAMS` at startup |
| `--log-file` | — | Path to rotating log file (10 MB × 5 backups) |
| `--log-level` | `INFO` | DEBUG / INFO / WARNING / ERROR |

## Info configuration reference

| Flag | Default | Description |
|---|---|---|
| `server` (positional) | `seiscomp.hakai.org:18000` | SeedLink server `host:port` |
| `-I`, `--id` | — | Server identification + version |
| `-L`, `--stations` | — | List stations |
| `-Q`, `--streams` | — | List streams (NSLC + sample-rate + time range) |
| `-G`, `--gaps` | — | List recent gaps (server-dependent) |
| `-C`, `--connections` | — | List active client connections (often redacted) |
| `--network`, `-n` | — | Filter by network code |
| `--station`, `-S` | — | Filter by station code |
| `--json` | off | Emit parsed records as JSON |
| `--xml` | off | Emit raw XML response |
| `--timeout` | `30` | Socket timeout (seconds) |

Exactly one of `-I/-L/-Q/-G/-C` is required.

## Notes

**FDSN behind an nginx reverse proxy.** If your FDSN service is mounted at a path like
`/fdsnws` via a reverse proxy, you need to include that path in `--fdsn`
(e.g. `http://seiscomp.hakai.org/fdsnws`). The package uses `service_mappings` so the
URL is used exactly as given, without appending a second `/fdsnws/`.

**Inventory caching.** On first run the StationXML response is fetched and cached as
`./inv_<NET>_<STA>_<CHA>.xml`. Delete that file (or use `--no-cache`) to force a refresh.

**Fullscreen on Linux with TkAgg.** Some window managers silently ignore the first
fullscreen request. The script retries via Tk's own event loop and falls back to
`overrideredirect` if needed. For the most reliable fullscreen across platforms, install
PyQt and run with `MPLBACKEND=QtAgg`.

## License

MIT — see [LICENSE](LICENSE).
