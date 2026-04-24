# Getting Started (for Dummies)

Zero-to-running in one sitting. This guide assumes you have **never used Python,
conda, or a terminal before**. By the end you'll have a live waveform window
open against a Raspberry Shake on your local network (hostname `rs.local`),
plotting raw counts (no instrument response removal, no internet required).

Works the same way on **Windows 10/11**, **Windows + WSL**, **macOS**, and
**Linux (Ubuntu/Debian/Fedora)** — where the steps differ there are separate
sections. Everything else is identical.

Two installation paths are covered:
- **Conda** — traditional, widely tested, works everywhere
- **uv** — newer, much faster, no conda needed

Pick whichever you're more comfortable with. Both produce the same result.

---

## What you'll end up with

A single command, run in a terminal, that opens a live scrolling-trace +
spectrogram window looking at your Shake:

```
seedlink-py-viewer AM.RXXXX.00.EHZ --server rs.local:18000 --fdsn ''
```

(Where `RXXXX` is your Shake's station code — we'll find it in step 5.)

The `--fdsn ''` at the end is the "no inventory" switch: it tells the viewer
to skip response removal and just plot raw counts. That means no internet
connection and no metadata server is needed — the Shake on your LAN is the
only thing you need to reach.

---

## Prerequisites

- A computer on the same network as the Raspberry Shake (wired or Wi-Fi).
- The Shake powered on and reachable at `rs.local`. You can confirm this
  before going further by opening a terminal (see step 1) and running:

  ```
  ping rs.local
  ```

  You should see replies. Press `Ctrl+C` to stop. If `rs.local` doesn't
  resolve, you'll need your Shake's IP address instead — check your Shake's
  admin page or your router's connected-devices list, and substitute that IP
  for `rs.local` in every command below.
- About 2 GB of free disk space for the Python scientific stack.

---

## 1. Open a terminal

You'll be typing commands into a terminal (a.k.a. command prompt / shell).

| Platform | How to open |
|---|---|
| **Windows (native conda)** | After step 2, use **"Anaconda Prompt (Miniconda3)"** from the Start menu. |
| **Windows + WSL** | Open **Windows Terminal** or **Command Prompt**, type `wsl`, press Enter. You're now in Linux. |
| **macOS** | Open **Terminal.app** (Applications → Utilities → Terminal, or ⌘-Space and type "terminal"). |
| **Linux** | Open your distribution's terminal (usually `Ctrl+Alt+T` on Ubuntu/Debian). |

Every command block in this guide is something you type (or paste) into that
terminal and press Enter.

---

## 2. Install Python + a package manager

Pick **one** of the two paths below.

### Path A: Conda

Miniconda gives you Python plus the `conda` package manager, which handles
the scientific libraries this tool depends on.

#### Windows (native)

1. Go to <https://www.anaconda.com/download/success#miniconda>
2. Download the **Miniconda3 Windows 64-bit** installer (`.exe`).
3. Run the installer. Accept the defaults — in particular:
   - Install for **Just Me** (not All Users).
   - Leave **"Add Miniconda3 to my PATH"** UNchecked (the warning is real;
     you'll use the Anaconda Prompt instead, which knows where conda is).
4. When the installer finishes, open **Anaconda Prompt (Miniconda3)** from
   the Start menu. All further Windows commands go in this window.

#### Windows + WSL (Ubuntu)

WSL gives you a real Linux environment inside Windows. If you don't have
WSL yet:

```
wsl --install
```

Restart your computer when prompted, then open WSL from the Start menu or
by typing `wsl` in Windows Terminal. You'll set up a username/password on
first launch.

Then install Miniconda inside WSL:

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o miniconda.sh
bash miniconda.sh -b -p ~/miniconda3
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

#### macOS

1. Go to <https://www.anaconda.com/download/success#miniconda>
2. Download the **Miniconda3 macOS 64-bit pkg** installer that matches your
   Mac:
   - Apple Silicon (M1 / M2 / M3 / M4): **Apple Silicon** version
   - Intel Mac: **Intel** version

   Not sure? Click the Apple logo → **About This Mac**. "Chip: Apple
   M*n*" = Apple Silicon. "Processor: Intel" = Intel.
3. Run the `.pkg` installer and accept the defaults.
4. Close and reopen **Terminal.app** so it picks up the new `conda` command.

#### Linux (Ubuntu / Debian / Fedora / etc.)

```bash
curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o miniconda.sh
bash miniconda.sh -b -p ~/miniconda3
~/miniconda3/bin/conda init bash
source ~/.bashrc
```

#### Verify conda

```
conda --version
```

You should see something like `conda 24.x.x`. If "command not found", close
the terminal, reopen it, and try again.

---

### Path B: uv (faster alternative, no conda needed)

[uv](https://docs.astral.sh/uv/) is a fast Python package manager from the
makers of `ruff`. It installs dependencies 10–50× faster than conda or pip.
You still need Python itself — uv handles the rest.

#### Install uv

**All platforms** (Linux, macOS, WSL):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc   # or restart your terminal
```

**Windows (native PowerShell)**:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

#### Verify uv

```
uv --version
```

You should see `uv 0.x.x`. If you also need Python, uv can install it:

```bash
uv python install 3.12
```

---

## 3. Install Git (optional)

Git is optional — you can download the code as a ZIP instead (see step 4).

| Platform | How to install |
|---|---|
| **Windows (native)** | Download from <https://git-scm.com/download/win>, accept defaults. Or skip Git and use the ZIP download in step 4. |
| **WSL / Ubuntu / Debian** | `sudo apt update && sudo apt install -y git` |
| **Fedora / RHEL** | `sudo dnf install -y git` |
| **macOS** | Run `git --version` — macOS will offer to install Xcode Command Line Tools. Click Install. |

---

## 4. Download the code

### With Git

```bash
cd ~
git clone https://github.com/schaefferaj/SeedlinkPyUtils.git
cd SeedlinkPyUtils
```

### Without Git (ZIP download)

1. Go to <https://github.com/schaefferaj/SeedlinkPyUtils/archive/refs/heads/master.zip>
2. Extract the ZIP somewhere convenient.
3. In your terminal, navigate into the extracted folder:
   ```bash
   cd ~/SeedlinkPyUtils-master    # adjust the path if you put it elsewhere
   ```

You should see files like `README.md`, `pyproject.toml`, and a `src` folder
when you run `ls` (Linux/Mac/WSL) or `dir` (Windows).

---

## 5. Create the environment and install the package

Pick the path matching your choice in step 2.

### Path A: Conda

Run these three commands **one at a time**, waiting for each to finish:

```bash
conda env create -f environment.yml
conda activate seedlink-py-utils
pip install .
```

What each one does:

1. **`conda env create -f environment.yml`** — creates an isolated Python
   environment named `seedlink-py-utils` with the scientific stack. Answer
   `y` if it asks to proceed.
2. **`conda activate seedlink-py-utils`** — switches your terminal into that
   environment. Your prompt will now have `(seedlink-py-utils)` at the
   front. You'll need to run this command again every time you open a new
   terminal.
3. **`pip install .`** — installs this package into the environment. After
   this, the command `seedlink-py-viewer` is available to you.

> **Trouble with `conda env create`?** Some older conda versions fail with
> `Malformed version string "~"`. Fix: update conda first with
> `conda update -n base conda`, then retry. Or switch to the uv path below.

### Path B: uv

```bash
uv venv
source .venv/bin/activate    # Linux / macOS / WSL
# .venv\Scripts\activate     # Windows native (PowerShell)

uv pip install .
```

What each one does:

1. **`uv venv`** — creates a lightweight virtual environment in `.venv/`.
2. **`source .venv/bin/activate`** — activates it. Your prompt will show
   `(.venv)` at the front. You'll need to run this again in new terminals.
3. **`uv pip install .`** — installs the package and all dependencies
   (ObsPy, NumPy, SciPy, Matplotlib). Typically finishes in under a minute.

### Verify

```
seedlink-py-viewer --help
```

You should see a big help screen listing all the flags. If so, you're
ready to go.

---

## 6. Find your Shake's station code

Raspberry Shakes publish data under network `AM` and a station code like
`R1A2B` (a unique 5-character ID printed on the bottom of the device and
shown on the Shake's admin page). If you already know it, skip this step.

If you don't, ask the Shake itself:

```
seedlink-py-info -L rs.local:18000
```

You should get a short table listing one station, something like:

```
Network  Station  Description
AM       R1A2B    Raspberry Shake Station
```

Take note of that station code — `R1A2B` in the example above. You'll
substitute your own code into the final command.

While you're here, you can also list the actual streams (channels) the
Shake is offering:

```
seedlink-py-info -Q rs.local:18000
```

A classic RS1D Shake offers `EHZ` (a single vertical channel). A Shake &
Boom adds `HDF` (pressure). A 4D or RS3D adds `EHN` and `EHE`. We'll use
`EHZ` below because every Shake has it.

---

## 7. Run the viewer (counts only, no inventory)

Finally — the payoff. Substitute your own station code for `RXXXX`:

```
seedlink-py-viewer AM.RXXXX.00.EHZ --server rs.local:18000 --fdsn ''
```

Breakdown:

- **`AM.RXXXX.00.EHZ`** — the stream, in `NET.STA.LOC.CHA` format. Shakes
  always use network `AM`, location `00`, and channel `EHZ` for the
  vertical short-period geophone.
- **`--server rs.local:18000`** — talk to the Shake on your LAN instead of
  the default IRIS server. Port `18000` is the Shake's SeedLink port.
- **`--fdsn ''`** — the "no inventory" switch. Two single quotes with
  nothing between them (on Windows `--fdsn ""` with double quotes also
  works). Tells the viewer not to go looking for an instrument-response
  file — it'll plot raw counts on the y-axis and label the spectrogram
  accordingly.

A window should pop up within a few seconds showing the last ~5 minutes of
data scrolling by, with a waveform panel on top and a spectrogram
underneath. The filter dropdown at the top of the window lets you try
different bandpasses on the waveform; none of them need an inventory
because filtering happens after the signal is already on screen.

To close the viewer, just close the window (or press `Ctrl+C` in the
terminal).

> **WSL note:** GUI windows from WSL require WSLg (built into Windows 11
> and recent Windows 10 updates). If the window doesn't appear, try
> installing an X server like [VcXsrv](https://sourceforge.net/projects/vcxsrv/)
> and setting `export DISPLAY=:0` before running the viewer. Or run
> natively on Windows using the conda path.

---

## Want a fullscreen dark-mode view?

Add `-f -d`:

```
seedlink-py-viewer AM.RXXXX.00.EHZ --server rs.local:18000 --fdsn '' -f -d
```

Press `Esc` to exit fullscreen.

---

## Every day after the first time

Once everything is installed, the daily routine is much shorter:

1. Open a terminal.
2. Activate the environment:
   ```bash
   # Conda users:
   conda activate seedlink-py-utils

   # uv users:
   cd ~/SeedlinkPyUtils       # wherever you cloned/extracted it
   source .venv/bin/activate
   ```
3. Run the viewer:
   ```
   seedlink-py-viewer AM.RXXXX.00.EHZ --server rs.local:18000 --fdsn ''
   ```

That's it. You don't reinstall anything; the environment sticks around
until you delete it.

---

## Common snags

**"conda: command not found" (or the Windows equivalent).**
Your terminal hasn't picked up Miniconda yet. Close it and open a fresh
one — on Windows that means the "Anaconda Prompt (Miniconda3)" shortcut
specifically, not a regular Command Prompt. On Linux/WSL, try
`source ~/.bashrc`.

**"uv: command not found".**
Close and reopen your terminal, or run `source ~/.bashrc`. The uv
installer adds itself to your PATH but the current session may not see it.

**`conda env create` fails with "Malformed version string".**
Your conda is too old. Run `conda update -n base conda` and retry. Or
switch to the uv path — it avoids conda entirely.

**"seedlink-py-viewer: command not found" after step 5.**
You probably forgot to activate the environment. Run the activate command
and try again. Your prompt should have `(seedlink-py-utils)` or `(.venv)`
at the front when you're in the right environment.

**`rs.local` doesn't resolve.**
mDNS (the thing that makes `.local` names work) isn't always reliable,
especially on Windows or over VPN. Find your Shake's IP address (router
admin page, or the Shake's own admin page at `http://rs.local/` if *that*
works in a browser) and use the IP instead of `rs.local` in every command.

**WSL: "cannot open display" or no window appears.**
WSL needs WSLg (Windows 11) or an X server (Windows 10) to show GUI
windows. See the note in step 7. Alternatively, install natively on
Windows using the conda path.

**The viewer window opens but stays empty.**
Check that port 18000 is reachable from your computer. In the terminal:
```
seedlink-py-info -I rs.local:18000
```
If that hangs or errors out, the problem is the network path to the Shake,
not the viewer. Firewalls (especially corporate ones) sometimes block
non-HTTP ports.

**"I want response-removed data, not counts."**
That needs a StationXML file for your Shake. The Raspberry Shake
organisation publishes them via FDSN at `https://fdsnws.raspberryshakedata.com/`.
Drop the `--fdsn ''` and pass `--fdsn https://fdsnws.raspberryshakedata.com`
instead. That's outside the scope of this guide, but the README's
"Viewer" section has full details.

---

## Where to next

- Run `seedlink-py-viewer --help` to see all options (dark mode, custom
  buffer length, STA/LTA event picker, filter presets, etc.).
- `README.md` in this repo has the full usage reference for the viewer
  and the other tools (`seedlink-py-mc-viewer`, `seedlink-py-archiver`,
  `seedlink-py-info`, `seedlink-py-dashboard`, `seedlink-py-ppsd`,
  `seedlink-py-ppsd-archive`).
- The multi-channel viewer is a natural next step if you have a 3D/4D
  Shake:
  ```
  seedlink-py-mc-viewer AM.RXXXX.00.EH? --server rs.local:18000 --fdsn ''
  ```
  That opens three stacked panels (EHZ / EHN / EHE), one per component.
