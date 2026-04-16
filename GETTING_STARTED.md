# Getting Started (for Dummies)

Zero-to-running in one sitting. This guide assumes you have **never used Python,
conda, or a terminal before**. By the end you'll have a live waveform window
open against a Raspberry Shake on your local network (hostname `rs.local`),
plotting raw counts (no instrument response removal, no internet required).

Works the same way on **Windows 10/11** and **macOS** — where the steps differ
there are two columns. Everything else is identical.

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

  You should see replies. Press `Ctrl+C` (Windows/Linux) or `Cmd+C` (Mac)
  to stop. If `rs.local` doesn't resolve, you'll need your Shake's IP
  address instead — check your Shake's admin page or your router's
  connected-devices list, and substitute that IP for `rs.local` in every
  command below.
- About 2 GB of free disk space for the Python scientific stack.

---

## 1. Open a terminal

You'll be typing commands into a terminal (a.k.a. command prompt / shell).

| Windows | macOS |
|---|---|
| After step 2 below, use **"Anaconda Prompt (Miniconda3)"** from the Start menu. Until then, use **"Windows Terminal"** or **"Command Prompt"**. | Open **Terminal.app** (Applications → Utilities → Terminal, or ⌘-Space and type "terminal"). |

Every command block in this guide is something you type (or paste) into that
terminal and press Enter.

---

## 2. Install Miniconda

Miniconda is a small installer that gives you Python plus the `conda` package
manager, which will handle the scientific libraries this tool depends on.

### Windows

1. Go to <https://www.anaconda.com/download/success#miniconda>
2. Download the **Miniconda3 Windows 64-bit** installer (`.exe`).
3. Run the installer. Accept the defaults — in particular:
   - Install for **Just Me** (not All Users).
   - Leave **"Add Miniconda3 to my PATH"** UNchecked (the warning is real;
     you'll use the Anaconda Prompt instead, which knows where conda is).
4. When the installer finishes, open **Anaconda Prompt (Miniconda3)** from
   the Start menu. All further Windows commands go in this window.

### macOS

1. Go to <https://www.anaconda.com/download/success#miniconda>
2. Download the **Miniconda3 macOS 64-bit pkg** installer that matches your
   Mac:
   - Apple Silicon (M1 / M2 / M3 / M4): **Apple Silicon** version
   - Intel Mac: **Intel** version

   Not sure? Click the Apple logo → **About This Mac**. "Chip: Apple
   M*n*" = Apple Silicon. "Processor: Intel" = Intel.
3. Run the `.pkg` installer and accept the defaults.
4. Close and reopen **Terminal.app** so it picks up the new `conda` command.

### Verify

In your terminal (Anaconda Prompt on Windows, Terminal on Mac), run:

```
conda --version
```

You should see something like `conda 24.x.x`. If "command not found", close
the terminal, reopen it, and try again.

---

## 3. Install Git (Mac only — Windows has a workaround)

### Windows

You have two choices:

- **Easy**: skip Git entirely. Download the repo as a ZIP:
  <https://github.com/schaefferaj/SeedlinkPyUtils/archive/refs/heads/main.zip>
  Extract it to a folder like `C:\Users\<you>\SeedlinkPyUtils`. Skip ahead
  to step 4.
- **Proper**: install Git for Windows from <https://git-scm.com/download/win>
  (accept all defaults), then follow the Git instructions in step 4.

### macOS

Git ships with the Xcode Command Line Tools. In Terminal, run:

```
git --version
```

If Git isn't installed, macOS will pop up a dialog offering to install the
Command Line Tools — click **Install** and wait. Re-run `git --version` when
it's done.

---

## 4. Download the code

Pick the matching column. The resulting folder (called `SeedlinkPyUtils`)
can live anywhere — your home folder is fine.

### With Git (Mac, or Windows if you installed Git)

```
cd "%USERPROFILE%"         # Windows: go to your user folder
cd ~                       # Mac: go to your home folder
git clone https://github.com/schaefferaj/SeedlinkPyUtils.git
cd SeedlinkPyUtils
```

(On Windows, use just the `cd "%USERPROFILE%"` line; on Mac, use just the
`cd ~` line. Then the `git clone` and `cd SeedlinkPyUtils` lines are the
same on both.)

### Without Git (Windows ZIP route)

In your Anaconda Prompt, navigate into the folder you extracted the ZIP to:

```
cd "%USERPROFILE%\SeedlinkPyUtils-main"
```

(The folder name has `-main` on the end when GitHub gives you a ZIP. If you
renamed or moved it, adjust the path accordingly.)

You should now be "inside" the repository. Type `dir` (Windows) or `ls`
(Mac) — you should see files like `README.md`, `environment.yml`, and a
`src` folder.

---

## 5. Create the conda environment and install the package

This is the step that takes a few minutes (it's downloading ObsPy, NumPy,
SciPy, Matplotlib, and their dependencies).

Run these three commands **one at a time**, waiting for each to finish:

```
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

Verify:

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

1. Open a terminal (Anaconda Prompt on Windows, Terminal on Mac).
2. Activate the environment:
   ```
   conda activate seedlink-py-utils
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
specifically, not a regular Command Prompt.

**"seedlink-py-viewer: command not found" after step 5.**
You probably forgot `conda activate seedlink-py-utils`, or opened a new
terminal and forgot to activate again. Run the activate command and try
again. Your prompt should have `(seedlink-py-utils)` at the front when
you're in the right environment.

**`rs.local` doesn't resolve.**
mDNS (the thing that makes `.local` names work) isn't always reliable,
especially on Windows or over VPN. Find your Shake's IP address (router
admin page, or the Shake's own admin page at `http://rs.local/` if *that*
works in a browser) and use the IP instead of `rs.local` in every command.

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
  and the other three tools (`seedlink-py-mc-viewer`,
  `seedlink-py-archiver`, `seedlink-py-info`).
- The multi-channel viewer is a natural next step if you have a 3D/4D
  Shake:
  ```
  seedlink-py-mc-viewer AM.RXXXX.00.EH? --server rs.local:18000 --fdsn ''
  ```
  That opens three stacked panels (EHZ / EHN / EHE), one per component.
