# SeedlinkPyUtils

Real-time [SeedLink](https://www.seiscomp.de/doc/apps/seedlink.html) tools in Python,
built on [ObsPy](https://docs.obspy.org). Provides:

- **`seedlink-py-viewer`** — interactive single-channel trace + spectrogram viewer
- **`seedlink-py-mc-viewer`** — multi-channel / multi-station stacked
  waveform viewer (3-component of one station, or one channel across a set
  of stations), with optional per-panel STA/LTA picker
- **`seedlink-py-archiver`** — robust SLClient-based archiver that writes an
  [SDS](https://www.seiscomp.de/seiscomp3/doc/applications/slarchive/SDS.html)
  miniSEED archive
- **`seedlink-py-info`** — query a SeedLink server for stations, streams, gaps,
  and active connections (a Python port of `slinktool`'s INFO queries)
- **`seedlink-py-dashboard`** — live operator dashboard showing per-stream
  latency with OK / LAG / STALE classification; polls `INFO=STREAMS` on a
  schedule
- **`seedlink-py-ppsd`** — live Probabilistic Power Spectral Density
  monitor; feeds a SeedLink stream into `obspy.signal.PPSD` and
  re-renders the accumulating 2-D histogram with Peterson's NLNM/NHNM
  noise models overlaid
- **`seedlink-py-ppsd-archive`** — headless daemon that maintains per-NSLC
  master PPSDs on disk (NPZ) and renders per-bucket PNGs on a schedule
  (daily / weekly / monthly / quarterly / yearly, any combination).
  Multi-channel, wildcard-expanding, survives restarts, suitable as a
  long-running service

## Features

### Viewer (`seedlink-py-viewer`)
- Live waveform + synchronised spectrogram in a rolling time window
- Startup backfill from the server's ring buffer so the display opens with
  recent history already drawn (disable with `--no-backfill` for a live-only
  start)
- Automatic instrument response removal via FDSN or a local StationXML file
  (falls back to raw counts if unavailable)
- Interactive filter selector (bandpasses and highpasses) applied to the waveform only,
  leaving the spectrogram broadband
- Optional STA/LTA picker with `local` / `regional` / `tele-p` presets —
  adds a CFT strip above the waveform and red vertical markers at trigger
  onsets; STA, LTA, and trigger thresholds individually overridable
- Light and dark themes
- Cross-platform fullscreen mode (Linux / macOS / Windows / WSL) with a TkAgg-targeted
  fallback for stubborn window managers

### Multi-channel viewer (`seedlink-py-mc-viewer`)
- One stacked waveform panel per subscribed NSLC stream
- Supports any combination of stations and channels. Wildcards (`?`, `*`)
  in any NSLC field auto-expand via a one-shot `INFO=STREAMS` query at
  startup, with one panel per matched channel:
  - 3-component of one station: `IU.ANMO.00.BH?` → 3 panels (BHZ / BHN / BHE)
  - Vertical-only across a set: `CN.PGC..HHZ CN.NLLB..HHZ CN.SADO..HHZ`
  - Network sweep: `'CN.*..HHZ'` → N panels (every CN vertical)
- Shares the same filter and picker presets as the single-channel viewer
- Independent picker per panel when `--picker` is given — per-station
  triggers appear as red markers on their own panel at the right time
- No spectrogram — the focus is cross-panel correlation, not spectral
  context; use the single-channel viewer when you want a spectrogram
- `--max-panels` caps the total number of panels (default 8)

### Archiver (`seedlink-py-archiver`)
- Robust `SLClient`-based connection with state file for resume-on-restart — no data
  loss across short outages if the server still has it buffered
- Multiple streams per invocation, with SeedLink wildcards (`?`, `*`) in LOC and CHA
  natively, and in NET/STA via `--expand-wildcards` (one extra INFO=STREAMS query)
- Writes standard SDS layout: one file per day per channel, appended in real time
- Writes raw miniSEED records byte-identically (no round-trip through numpy)
- Automatic reconnection with configurable backoff
- Rotating log file (10 MB × 5 backups) with console heartbeat
- Optional per-NSLC stale-stream watchdog (`--monitor`) with log + Slack-webhook
  alerts and a `--exit-on-all-stale` hook for systemd-supervised restart

### Info / discovery (`seedlink-py-info`)
- `slinktool`-style flags: `-I` server id, `-L` stations, `-Q` streams,
  `-G` gaps, `-C` connections
- Client-side filtering by `--network` and `--station`
- Output as a human-readable table (default), JSON (`--json`), or raw XML (`--xml`)

### Stream availability dashboard (`seedlink-py-dashboard`)
- Live terminal dashboard: polls `INFO=STREAMS` every `--interval` seconds
  and renders a per-NSLC latency table
- Status classification OK / LAG / STALE / UNKNOWN with ANSI colour
  (auto-disabled when stdout is not a TTY)
- Tunable thresholds (`--ok-threshold`, `--stale-threshold`)
- Same `--network` / `--station` client-side filtering as `seedlink-py-info`
- `--once` for scripted / cron use (single snapshot, no screen clear)
- Auto-fits to the terminal — tables larger than the window truncate
  from the bottom with a `... N more rows hidden (X OK, Y LAG)` notice.
  Pairs well with `--sort-by-status` (STALE rows stay visible; OK rows
  drop off first). Pagination only kicks in for interactive live mode;
  `--once` and redirected output emit the full table.
- Resilient to transient poll failures — a network blip shows as
  `Poll failed: …` and the loop continues
- Optional transition alerting (`--alert`): log + Slack-compatible webhook
  on STALE transitions and recoveries (LAG <-> OK transitions are logged
  only). See [docs/slack-webhook.md](docs/slack-webhook.md) for webhook setup

### Probabilistic PSD monitor (`seedlink-py-ppsd`)
- Live SeedLink stream feeds `obspy.signal.PPSD` directly — response
  removal happens inside PPSD so the plot is in correct absolute units
- PQLX colormap by default (matches ObsPy's own `PPSD.plot` default);
  any matplotlib cmap name works via `--cmap`
- Peterson NLNM/NHNM overlay on every frame (toggle with `--no-noise-models`)
- Coverage strip along the bottom of the figure showing when
  PSD segments landed (dense = continuous, gaps = missing data)
- McNamara & Buland (2004) defaults: 3600 s segments, 50 % overlap —
  `--ppsd-length` and `--overlap` expose the knobs but deviating breaks
  comparability with published noise models
- Startup backfill from the server's ring buffer so the histogram is
  non-empty within minutes instead of hours (`--backfill-hours`, default 2)
- Optional sliding-window cap (`--max-hours`) — older PSDs drop off the
  histogram, useful for "last 24 h" operator views
- Same dark-mode / fullscreen / Esc-to-exit ergonomics as the viewer

### Headless PPSD archiver (`seedlink-py-ppsd-archive`)
- Long-running daemon: subscribes to N streams, maintains one master
  PPSD per NSLC, renders per-bucket PNGs on a schedule
- Multiple period types per invocation: `--period daily weekly monthly`
  renders three sets of PNGs per NSLC, all driven by the same master PPSD
- Survives restarts: master PPSDs are serialized to `.npz` on every
  render cycle, reloaded on next startup
- Wildcard expansion via `--expand-wildcards` (one `INFO=STREAMS` query at
  startup, same mechanism as the archiver)
- Soft-fail per-NSLC: stations whose response can't be loaded are
  logged and skipped; the daemon continues with the rest
- Per-NSLC folders, per-period subfolders, NSLC.png naming convention
- Rotating log file (10 MB × 5 backups) with per-render completeness
  summary for out-of-band fleet monitoring
- SIGTERM / Ctrl-C triggers a final flush before exit

## Installation

> **New to Python / conda / the terminal?** See [GETTING_STARTED.md](GETTING_STARTED.md)
> for a zero-to-running walkthrough on Windows and macOS, targeting a Raspberry
> Shake on your local network.

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
seedlink-py-viewer IU.ANMO.00.BHZ
seedlink-py-viewer CN.PGC..HHZ

# Dark mode, fullscreen (press Esc to exit)
seedlink-py-viewer IU.ANMO.00.BHZ --dark-mode --fullscreen

# Lock to a preset filter — hides the dropdown selector
seedlink-py-viewer CN.PGC..HHZ --filter hp3

# Teleseismic P-wave view on a broadband — --pre-filt is auto-lowered for
# 'surface' and 'tele-p' so the response removal doesn't mute the band
seedlink-py-viewer IU.ANMO.00.BHZ --filter tele-p

# STA/LTA picker with the teleseismic-P preset
seedlink-py-viewer IU.ANMO.00.BHZ --picker tele-p

# Picker with manual STA/LTA override on top of a preset
seedlink-py-viewer IU.ANMO.00.BHZ --picker local --sta 0.3 --lta 8

# Local Raspberry Shake on your LAN — no inventory needed (plots counts)
# Use rs.local or the Shake's IP address if rs.local doesn't resolve
seedlink-py-viewer AM.RXXXX.00.EHZ --server rs.local:18000 --fdsn ''

# Use a local StationXML instead of fetching from FDSN
seedlink-py-viewer CN.PGC..HHZ --inventory ./my_inventory.xml
```

Run `seedlink-py-viewer --help` for the full list of options.

### Multi-channel viewer

`seedlink-py-mc-viewer` takes one or more positional streams and draws one
waveform panel per stream. Works for both 3-component one-station views and
one-channel-across-multiple-stations views. Filter and picker options are
shared with the single-channel viewer; spectrogram-specific options do not
apply.

```bash
# Three components of one station
seedlink-py-mc-viewer IU.ANMO.00.BH?

# Verticals from a hand-picked set of stations with a local picker
seedlink-py-mc-viewer CN.PGC..HHZ CN.NLLB..HHZ CN.SADO..HHZ \
    --picker local

# Every CN vertical — wildcards auto-expand via INFO=STREAMS
seedlink-py-mc-viewer 'CN.*..HHZ' --picker local

# Tele-P-band view of IU.ANMO with picker on
seedlink-py-mc-viewer IU.ANMO.00.BH? --filter tele-p --picker tele-p
```

Each panel runs its own picker instance (same preset, independent pick
state) so a trigger on one station doesn't appear on another. The total
panel count is capped by `--max-panels` (default 8).

Run `seedlink-py-mc-viewer --help` for the full list of options.

### Archiver

The archiver runs as a long-lived process that subscribes to one or more streams and
writes them into an SDS miniSEED archive. It uses ObsPy's `SLClient` with a state
file, so after a restart or network outage it resumes from the last sequence number
and the server will backfill anything it still has buffered.

```bash
# Single station, three channels
seedlink-py-archiver IU.ANMO.00.BH? --archive /data/sds

# Multiple stations with state file and rotating log
seedlink-py-archiver CN.PGC..HH? CN.NLLB..HH? \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log

# Replay a historical window from the server's ring buffer
seedlink-py-archiver IU.ANMO.00.BHZ \
    --archive /data/sds \
    --begin-time 2026-04-14T12:00:00 \
    --end-time   2026-04-14T13:00:00

# Subscribe to every station in the CN network (single-quote to stop the shell
# from globbing the asterisk before argparse sees it)
seedlink-py-archiver 'CN.*..HH?' --archive /data/sds --expand-wildcards
```

**Stream syntax.** `NET.STA.LOC.CHA`, with `?` and `*` wildcards allowed in LOC and CHA
natively (SeedLink's own multiselect). Empty LOC is written as two dots (e.g.
`CN.PGC..HHZ`). Wildcards in NET or STA are *not* part of the SeedLink protocol —
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

**Monitoring (`--monitor`).** The archiver has an optional in-process
stale-stream watchdog. When enabled, it tracks per-NSLC packet arrivals and
alerts on state transitions:

- `HEALTHY → STALE` — no packets for `--stale-timeout` seconds (default 300).
  Fires once per transition, not every tick.
- `STALE → HEALTHY` — a previously-silent channel is flowing again.
- `UNKNOWN → HEALTHY` — first packet of an NSLC. Logged at INFO, no webhook
  (it's a startup event, not an alert).

Alerts always go to the logger. With `--webhook URL` they also POST a JSON
body to any Slack-compatible incoming webhook (the `text` field renders in
Slack; extra fields `event`, `nslc`, `age_seconds`, etc. are there for
consumers that want structured data).

```bash
# Bare minimum: enable the watchdog with defaults
seedlink-py-archiver IU.ANMO.00.BH? --archive /data/sds --monitor

# Production: state file, rotating log, Slack alerts, systemd-friendly exit
seedlink-py-archiver CN.PGC..HH? CN.NLLB..HH? \
    --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log \
    --monitor --stale-timeout 300 \
    --webhook "$SLACK_WEBHOOK_URL" \
    --exit-on-all-stale
```

See [docs/slack-webhook.md](docs/slack-webhook.md) for step-by-step Slack
setup and [docs/systemd.md](docs/systemd.md) for pairing `--exit-on-all-stale`
with a systemd `Restart=on-failure` unit (the recommended deployment pattern
for process-level auto-restart).

**Running as a service.** See [docs/systemd.md](docs/systemd.md) for a
complete systemd unit, sandboxing options, and notes on webhook secret
handling. The short version:

```ini
[Service]
ExecStart=/opt/conda/envs/seedlink-py-utils/bin/seedlink-py-archiver \
    CN.PGC..HH? --archive /data/sds \
    --state-file /var/lib/slarchiver/state.txt \
    --log-file /var/log/slarchiver.log \
    --monitor --webhook ${SLACK_WEBHOOK_URL}
Restart=always
RestartSec=30
KillSignal=SIGINT
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
seedlink-py-info -Q --network CN

# Streams for one station as JSON
seedlink-py-info -Q --station ANMO --json

# Recent gaps (server-dependent — many SeisComP installs disable this)
seedlink-py-info -G

# Active client connections (often redacted by the server)
seedlink-py-info -C
```

The default server is `rtserve.iris.washington.edu:18000`. Pass any other
`host:port` as a positional argument.

Run `seedlink-py-info --help` for the full list of options.

### Stream availability dashboard

`seedlink-py-dashboard` is the live complement to `seedlink-py-info`. It polls
`INFO=STREAMS` on a schedule and shows a coloured per-NSLC latency table with
OK / LAG / STALE status. Leave it running in a spare terminal or on a side
monitor to keep an eye on which streams are actually flowing.

```bash
# Default server (IRIS), 30 s interval
seedlink-py-dashboard

# Just CN stations, faster polling
seedlink-py-dashboard --network CN --interval 10

# One station's channels
seedlink-py-dashboard --station ANMO

# Verticals only — one row per station (channels at a station usually share
# latency, so this is the compact fleet-overview view)
seedlink-py-dashboard --channel BHZ

# Wildcards in the channel filter (quote to stop the shell from globbing)
seedlink-py-dashboard --network CN --channel 'HH?'
seedlink-py-dashboard --channel '*Z'

# Single snapshot — scriptable, no screen clear
seedlink-py-dashboard --once

# Focus attention on problems — STALE rows at the top, OK at the bottom
# (alphabetical by NSLC within each status group)
seedlink-py-dashboard --sort-by-status

# Tighter thresholds (strict "should be near real-time")
seedlink-py-dashboard --ok-threshold 30 --stale-threshold 300

# Slack alerts when streams go STALE or recover (webhook setup: docs/slack-webhook.md)
seedlink-py-dashboard --alert --webhook "$SLACK_WEBHOOK_URL"

# Alerts on a specific network, no TTY rendering (headless alerting service)
seedlink-py-dashboard --network CN --alert --webhook "$SLACK_WEBHOOK_URL" --once
```

**Status bands (defaults).** `OK` < 60 s, `LAG` 60–600 s, `STALE` > 600 s,
`UNKNOWN` when the server's `end_time` for a stream is empty or unparseable.
Colours: green / yellow / red / dim; auto-disabled when stdout is not a TTY
(so `> log.txt` or `| tee` produce a clean growing log).

**Resilience.** A transient poll failure (network blip, server briefly
refusing `INFO`) is reported inline as `Poll failed: …` and the loop
continues — the dashboard survives a flaky connection without needing a
restart.

**Pagination.** When the table has more rows than fit in the current
terminal window, the dashboard truncates from the bottom and adds a
dim `... N more rows hidden (X OK, Y LAG)` notice so you know something
was cut and what kind of rows got dropped. Pagination only kicks in
for the interactive live mode — `--once` and redirected output stay
unconstrained (`seedlink-py-dashboard --once > log.txt` gives the
full table). The pagination pairs nicely with `--sort-by-status`
(when it lands): STALE rows are at the top of the sort order, so they
stay visible while healthy OK rows are the first to drop off when the
table is clipped.

Run `seedlink-py-dashboard --help` for the full list of options.

### Probabilistic PSD monitor

`seedlink-py-ppsd` feeds a live SeedLink stream into
[`obspy.signal.PPSD`](https://docs.obspy.org/packages/autogen/obspy.signal.spectral_estimation.PPSD.html)
and re-renders the accumulating 2-D histogram of power spectral density
vs. period, with the Peterson NLNM/NHNM noise-model curves overlaid.
Useful for leaving up on a side monitor to track station noise
performance in real time.

```bash
# Defaults: IRIS SeedLink + EarthScope FDSN, McNamara-standard 3600 s segments
seedlink-py-ppsd IU.ANMO.00.BHZ

# Dark mode, fullscreen (press Esc to exit)
seedlink-py-ppsd CN.PGC..HHZ -d -f

# Sliding 24 h window — older PSDs drop off the histogram
seedlink-py-ppsd CN.PGC..HHZ --max-hours 24

# Use a local StationXML instead of fetching from FDSN
seedlink-py-ppsd CN.PGC..HHZ --inventory ./my_inventory.xml

# More aggressive backfill to populate the histogram faster at startup
# (server ring buffer must actually cover this window; most do)
seedlink-py-ppsd IU.ANMO.00.BHZ --backfill-hours 6

# Skip the NLNM/NHNM overlay
seedlink-py-ppsd IU.ANMO.00.BHZ --no-noise-models
```

**Instrument response is required.** PPSD output is in physical units
(acceleration power in dB re 1 (m/s²)²/Hz) which only makes sense with
the response removed. The CLI errors out at startup if `--fdsn ''` is
set with no `--inventory` to fall back on.

**Time-to-populate.** With the default 3600 s segments, the first PSD
needs one full hour of contiguous data to land. Startup backfill
(`--backfill-hours`, default 2) asks the server's ring buffer for
history, but **delivery is best-effort** — many SeedLink servers (IRIS
included, in our testing) only replay what's in their ring buffer at
the moment of request, often a few minutes rather than the requested
hours. So for the default settings, expect to wait roughly one hour of
wall-clock time before the first segment lands.

While accumulating, the figure shows a progress message —
`"Buffered: 12.0 / 60 min (20%)"` — so you can see how far along you
are. After a day or two of running the histogram converges toward the
station's long-term noise profile.

**For testing or quick checks**, pass `--ppsd-length 300` (5 min
segments) — first PSD lands in 5 minutes, useful for verifying the
pipeline works against a new station before committing to a long
session. Non-standard segment lengths produce valid PSDs but they're
no longer directly comparable to the overlaid NLNM/NHNM curves.

**Don't change `--ppsd-length` casually.** Peterson's NLNM/NHNM and
most published station noise curves assume the McNamara & Buland (2004)
methodology (3600 s segments, 50 % overlap, 13 sub-segments per
segment, 75 % sub-overlap). Changing `--ppsd-length` or `--overlap`
produces valid PSDs but they're no longer directly comparable to the
overlaid noise models.

Run `seedlink-py-ppsd --help` for the full list of options.

### Headless PPSD archiver

`seedlink-py-ppsd-archive` is the long-running, multi-channel daemon
counterpart to `seedlink-py-ppsd`. It subscribes to any number of
streams (NET/STA wildcards via `--expand-wildcards`), maintains a
master `PPSD` per NSLC, and on a schedule renders per-bucket PNG
histograms to disk. Each NSLC's master PPSD is persisted to `.npz` on
every render cycle, so the full histogram survives restarts and
crashes with at most `--render-interval` worth of new data lost.

```bash
# Simplest case: weekly PPSDs for one station, defaults everywhere
seedlink-py-ppsd-archive IU.ANMO.00.BHZ --output-root /data/ppsd

# Daily + weekly + monthly at once for every CN vertical
seedlink-py-ppsd-archive 'CN.*..HHZ' \
    --output-root /data/ppsd \
    --period daily weekly monthly \
    --expand-wildcards

# Long-running fleet on a non-default SeedLink server, with rotating log
seedlink-py-ppsd-archive 'CN.*..HH?' \
    --server seedlink.example.org:18000 \
    --fdsn https://fdsn.example.org \
    --output-root /data/ppsd \
    --period weekly monthly \
    --expand-wildcards \
    --log-file /var/log/ppsd-archive.log
```

**Output layout.** For each fully-resolved NSLC:

```
<output-root>/
└── <NET>.<STA>/
    ├── <NET>.<STA>.<LOC>.<CHA>.npz              # master PPSD state
    ├── daily/<NET>.<STA>.<LOC>.<CHA>_2026-04-15.png
    ├── weekly/<NET>.<STA>.<LOC>.<CHA>_2026-W16.png
    └── monthly/<NET>.<STA>.<LOC>.<CHA>_2026-04.png
```

One `.npz` per NSLC, shared across all active periods (they just
re-bin the same master PSD list into different time windows). One
`.png` per NSLC × period × bucket.

**Bucket keys (UTC):**

| Period | Key format | Boundaries |
|---|---|---|
| `daily` | `YYYY-MM-DD` | Midnight UTC to midnight UTC |
| `weekly` | `YYYY-Www` | ISO week — Monday 00:00 UTC to next Monday |
| `monthly` | `YYYY-MM` | First of month to first of next month |
| `quarterly` | `YYYY-Qn` | Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec |
| `yearly` | `YYYY` | Jan 1 to next Jan 1 |

**Instrument response is required per NSLC.** At startup, the daemon
fetches a response for every resolved NSLC (FDSN or local inventory).
Any NSLC whose response can't be loaded is logged as a WARNING and
skipped — the daemon keeps going with the rest. If **all** NSLCs fail
response loading, the daemon exits with an error.

**Running as a service.** Minimal systemd unit:

```ini
[Unit]
Description=SeedLink PPSD archiver
After=network.target

[Service]
Type=simple
User=seismo
ExecStart=/opt/conda/envs/seedlink-py-utils/bin/seedlink-py-ppsd-archive \
    'CN.*..HHZ' \
    --output-root /data/ppsd \
    --period weekly monthly \
    --expand-wildcards \
    --log-file /var/log/ppsd-archive.log
Restart=always
RestartSec=30
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

The `TimeoutStopSec=60` gives the daemon enough time to flush the
final NPZ + PNGs on SIGTERM before systemd escalates to SIGKILL.

Run `seedlink-py-ppsd-archive --help` for the full list of options.


### As a Python API

```python
# Viewer
from seedlink_py_utils import ViewerConfig, run_viewer

cfg = ViewerConfig(
    nslc=("IU", "ANMO", "00", "BHZ"),
    buffer_seconds=300,
    dark_mode=True,
    picker_preset="local",     # optional — enables STA/LTA picker
)
run_viewer(cfg)

# Archiver
from seedlink_py_utils import MonitorConfig, run_archiver
from seedlink_py_utils.logging_setup import setup_logger

setup_logger(log_file="/var/log/slarchiver.log")
run_archiver(
    server="rtserve.iris.washington.edu:18000",
    streams=["CN.PGC..HH?", "CN.NLLB..HH?"],
    archive_root="/data/sds",
    state_file="/var/lib/slarchiver/state.txt",
    monitor_config=MonitorConfig(            # optional
        stale_timeout=300,
        webhook_url="https://hooks.slack.com/services/...",
        exit_on_all_stale=True,              # for systemd-supervised deploys
    ),
)

# Info / discovery
from seedlink_py_utils import query_info
from seedlink_py_utils.info import parse_streams, filter_records

xml = query_info("rtserve.iris.washington.edu:18000", level="STREAMS")
streams = filter_records(parse_streams(xml), network="CN")
for s in streams:
    print(s["network"], s["station"], s["location"], s["channel"])

# Stream availability dashboard
from seedlink_py_utils import DashboardConfig, run_dashboard

run_dashboard(DashboardConfig(
    interval=10.0,
    network="CN",
    channel="HHZ",     # one row per station
))

# Probabilistic PSD monitor
from seedlink_py_utils import PPSDConfig, run_ppsd

run_ppsd(PPSDConfig(
    nslc=("IU", "ANMO", "00", "BHZ"),
    max_hours=24,         # optional sliding window
    dark_mode=True,
))

# Headless PPSD archiver (long-running)
from seedlink_py_utils import PPSDArchiveConfig, run_ppsd_archive

run_ppsd_archive(PPSDArchiveConfig(
    streams=["CN.*..HHZ"],
    output_root="/data/ppsd",
    periods=("daily", "weekly", "monthly"),
    expand_wildcards=True,
))
```

## Viewer configuration reference

| Flag | Default | Description |
|---|---|---|
| `stream` (positional) | — | `NET.STA.LOC.CHA`, e.g. `IU.ANMO.00.BHZ` |
| `--server`, `-s` | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
| `--fdsn` | `https://service.earthscope.org` | FDSN-WS base URL (empty string to disable) |
| `--inventory` | — | Local StationXML file (overrides `--fdsn`) |
| `--no-cache` | off | Skip the on-disk inventory cache |
| `--buffer`, `-b` | `300` | Rolling buffer length (seconds) |
| `--redraw-ms` | `1000` | Redraw interval (ms) |
| `--no-backfill` | off (i.e. backfill on) | Start empty instead of requesting `--buffer` seconds of history |
| `--nperseg` | `512` | FFT window length (samples) |
| `--noverlap` | `400` | FFT window overlap (samples) |
| `--fmin` / `--fmax` | `0.5` / `50.0` | Spectrogram frequency range (Hz) |
| `--db-clip` | `-180,-100` | Spectrogram dB colour limits |
| `--cmap` | `magma` | Matplotlib colormap |
| `--water-level` | `60` | Deconvolution water level |
| `--pre-filt` | `0.05,0.1,45,50` | Response pre-filter corners |
| `--filter` | — | Lock to a preset filter and hide the dropdown selector (see *Filter presets* below). Omit for the interactive selector. |
| `--picker` | — | Enable the STA/LTA picker with one of `local` / `regional` / `tele-p` (see *Picker presets* below). |
| `--sta` / `--lta` | (preset) | Override STA / LTA window (seconds). Requires `--picker`. |
| `--trigger-on` / `--trigger-off` | (preset) | Override STA/LTA ratio thresholds. Requires `--picker`. |
| `--fullscreen`, `-f` | off | Fullscreen, no toolbar |
| `--dark-mode`, `-d` | off | Dark colour theme |

### Filter presets

Grouped by use case. The CLI alias is what you pass to `--filter`; the canonical
name is what appears in the interactive dropdown (where each entry reads
``<name> (<alias>)``, e.g. `BP 1–10 Hz (regional)`).

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
| `local` | BP 2–10 Hz | Standard local-event band (matches the `local` picker) |
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

### Picker presets

When `--picker PRESET` is given, the viewer runs a recursive STA/LTA on every
redraw tick and marks trigger onsets as red vertical lines on the waveform.
A small CFT strip appears above the waveform showing the STA/LTA ratio,
with a red dashed line at the "trigger on" threshold and an amber dotted
line at "trigger off". Each preset carries its own detection filter; this is
independent of `--filter` (which only affects what you see on the waveform
panel) so the picker behaves consistently regardless of display settings.

| Preset | STA | LTA | Trigger on / off | Detection filter | Tuned for |
|---|---|---|---|---|---|
| `local` | 0.5 s | 10 s | 3.5 / 1.5 | BP 2–10 Hz | Local events, short-period instruments |
| `regional` | 2 s | 30 s | 3.0 / 1.5 | BP 1–10 Hz | Regional earthquakes (Pg/Pn/Sg/Sn) |
| `tele-p` | 5 s | 120 s | 2.5 / 1.5 | BP 0.5–2 Hz | Teleseismic P on broadbands |

Each picker preset name matches a `--filter` alias of the same name with the
same band — `--picker regional` and `--filter regional` both work on BP 1–10 Hz,
`--picker tele-p` and `--filter tele-p` both on BP 0.5–2 Hz, and so on. Pick
them together for a coherent workflow, or use `--filter` to look at a different
band than the picker triggers on.

`--sta`, `--lta`, `--trigger-on`, `--trigger-off` override the corresponding
field of whichever preset you chose; the detection filter is preset-locked
(pick the closest preset for your target band).

## Archiver configuration reference

| Flag | Default | Description |
|---|---|---|
| `streams` (positional, 1+) | — | One or more `NET.STA.LOC.CHA` (wildcards in LOC/CHA) |
| `--server`, `-s` | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
| `--archive`, `-a` | — (required) | SDS archive root directory |
| `--state-file` | — | SeedLink state file for resume on restart |
| `--begin-time` | — | Replay window start (ISO 8601) |
| `--end-time` | — | Replay window end (ISO 8601) |
| `--reconnect-wait` | `10` | Seconds between reconnect attempts |
| `--max-reconnects` | unlimited | Cap on reconnect attempts |
| `--expand-wildcards` | off | Expand `?` / `*` in NET/STA via `INFO=STREAMS` at startup |
| `--monitor` | off | Enable the per-NSLC stale-stream watchdog |
| `--stale-timeout` | `300` | Seconds without a packet before an NSLC is classified STALE |
| `--monitor-interval` | `60` | Seconds between watchdog checks (must be `< --stale-timeout`) |
| `--webhook` | — | Slack-compatible incoming-webhook URL for alerts |
| `--webhook-timeout` | `10` | Per-request webhook POST timeout (seconds) |
| `--exit-on-all-stale` | off | Exit status 2 when every registered NSLC is STALE (pairs with systemd) |
| `--hostname` | host FQDN | Label used in alert text |
| `--log-file` | — | Path to rotating log file (10 MB × 5 backups) |
| `--log-level` | `INFO` | DEBUG / INFO / WARNING / ERROR |

## Info configuration reference

| Flag | Default | Description |
|---|---|---|
| `server` (positional) | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
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

## Dashboard configuration reference

| Flag | Default | Description |
|---|---|---|
| `server` (positional) | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
| `--interval` | `30` | Poll interval in seconds |
| `--once` | off | Run one poll and exit (no screen clear) |
| `--timeout` | `30` | Per-poll socket timeout (seconds) |
| `--ok-threshold` | `60` | Latency below this is OK (green) |
| `--stale-threshold` | `600` | Latency above this is STALE (red); between = LAG (yellow) |
| `--network`, `-n` | — | Filter by network code (exact match, case-insensitive) |
| `--station`, `-S` | — | Filter by station code (exact match, case-insensitive) |
| `--channel`, `-c` | — | Filter by channel code; supports `?` / `*` wildcards (e.g. `EHZ`, `HH?`, `*Z`) |
| `--sort-by-status` | off | Group rows by status: STALE, LAG, UNKNOWN, OK — alphabetical by NSLC within each group |
| `--alert` | off | Enable transition alerts (log + optional webhook on STALE transitions) |
| `--webhook` | — | Slack-compatible incoming-webhook URL for STALE / recovery alerts |
| `--webhook-timeout` | `10` | Per-request webhook POST timeout (seconds) |
| `--hostname` | host FQDN | Label used in alert text |
| `--no-color` | off | Disable ANSI colour (auto-disabled when stdout isn't a TTY) |

## PPSD configuration reference

| Flag | Default | Description |
|---|---|---|
| `stream` (positional) | — | `NET.STA.LOC.CHA`, e.g. `IU.ANMO.00.BHZ` |
| `--server`, `-s` | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
| `--fdsn` | `https://service.earthscope.org` | FDSN-WS base URL (empty string requires `--inventory`) |
| `--inventory` | — | Local StationXML file (overrides `--fdsn`) |
| `--no-cache` | off | Skip the on-disk inventory cache |
| `--ppsd-length` | `3600` | Length of each PPSD segment (seconds) |
| `--overlap` | `0.5` | Overlap between consecutive segments (0–1) |
| `--max-hours` | — | Sliding-window cap; accumulate forever if unset |
| `--backfill-hours` | `2.0` | Hours of ring-buffer history to request at startup (`0` disables) |
| `--redraw-ms` | `10000` | Matplotlib redraw interval (ms) |
| `--cmap` | `viridis` | Matplotlib colormap for the 2-D histogram |
| `--no-noise-models` | off | Disable the Peterson NLNM/NHNM overlay |
| `--fullscreen`, `-f` | off | Fullscreen, no toolbar |
| `--dark-mode`, `-d` | off | Dark colour theme |

## PPSD archiver configuration reference

| Flag | Default | Description |
|---|---|---|
| `streams` (positional, 1+) | — | One or more `NET.STA.LOC.CHA`; wildcards in LOC/CHA native, in NET/STA require `--expand-wildcards` |
| `--output-root` | — (required) | Root directory for NPZs and PNGs |
| `--period` | `weekly` | One or more of `daily weekly monthly quarterly yearly` |
| `--render-interval` | `1800` | Seconds between re-render cycles |
| `--server`, `-s` | `rtserve.iris.washington.edu:18000` | SeedLink server `host:port` |
| `--fdsn` | `https://service.earthscope.org` | FDSN-WS base URL |
| `--inventory` | — | Local StationXML file (overrides `--fdsn`) |
| `--no-cache` | off | Skip the on-disk inventory cache |
| `--ppsd-length` | `3600` | PPSD segment length (seconds) |
| `--overlap` | `0.5` | Segment overlap (0–1) |
| `--expand-wildcards` | off | Expand `?` / `*` in NET/STA via `INFO=STREAMS` at startup |
| `--cmap` | `pqlx` | Colormap for the 2-D histogram |
| `--no-noise-models` | off | Disable Peterson NLNM/NHNM overlay |
| `--log-file` | — | Rotating log file (10 MB × 5 backups) |
| `--log-level` | `INFO` | DEBUG / INFO / WARNING / ERROR |

## Notes

**FDSN behind an nginx reverse proxy.** If your FDSN service is mounted at a path like
`/fdsnws` via a reverse proxy, you need to include that path in `--fdsn`. The package
tries standard FDSN service discovery first, then falls back to explicit
`service_mappings` with the path as given.

**Inventory caching.** On first run the StationXML response is fetched and cached as
`./inv_<NET>_<STA>_<CHA>.xml`. Delete that file (or use `--no-cache`) to force a refresh.

**Fullscreen on Linux with TkAgg.** Some window managers silently ignore the first
fullscreen request. The script retries via Tk's own event loop and falls back to
`overrideredirect` if needed. For the most reliable fullscreen across platforms, install
PyQt and run with `MPLBACKEND=QtAgg`.

## License

MIT — see [LICENSE](LICENSE).
