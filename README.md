# SeedlinkPyUtils

Real-time [SeedLink](https://www.seiscomp.de/doc/apps/seedlink.html) tools in Python,
built on [ObsPy](https://docs.obspy.org). Currently provides an interactive trace +
spectrogram viewer with response removal, filter presets, dark mode, and fullscreen
support.

## Features

- Live waveform + synchronised spectrogram in a rolling time window
- Automatic instrument response removal via FDSN or a local StationXML file
  (falls back to raw counts if unavailable)
- Interactive filter selector (bandpasses and highpasses) applied to the waveform only,
  leaving the spectrogram broadband
- Light and dark themes
- Cross-platform fullscreen mode (Linux / macOS / Windows / WSL) with a TkAgg-targeted
  fallback for stubborn window managers
- Installable as a Python package with a `seedlink-py-viewer` console entry point

## Installation

### Conda (recommended)

```bash
git clone https://github.com/schaefferaj/SeedlinkPyUtils.git
cd SeedlinkPyUtils
conda env create -f environment.yml
conda activate seedlink-py-utils
```

This installs all scientific dependencies from `conda-forge` and then does a
pip editable install of the package itself.

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

After installation, the `seedlink-py-viewer` command is on your path.

```bash
# Basic: stream in NET.STA.LOC.CHA form (empty LOC uses double dots)
seedlink-py-viewer AM.RA382.00.EHZ
seedlink-py-viewer PQ.DAOB..HHZ

# Dark mode, fullscreen (press Esc to exit)
seedlink-py-viewer AM.RA382.00.EHZ --dark-mode --fullscreen

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

### As a Python API

```python
from seedlink_py_utils import ViewerConfig, run_viewer

cfg = ViewerConfig(
    nslc=("AM", "RA382", "00", "EHZ"),
    seedlink_server="seiscomp.hakai.org:18000",
    fdsn_server="http://seiscomp.hakai.org/fdsnws",
    buffer_seconds=300,
    dark_mode=True,
)
run_viewer(cfg)
```

## Configuration reference

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
| `--fullscreen`, `-f` | off | Fullscreen, no toolbar |
| `--dark-mode`, `-d` | off | Dark colour theme |

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
