# strata-coder

This tool runs a local AI coding assistant: [OpenCode](https://opencode.ai), backed by **Qwen3.8-Flash-Next** served by
[Strata](https://github.com/Niko1221/Strata). Everything installs per-user, so no admin rights and no Docker are needed.

## Install (once)

1. Install Python 3.9+ if you don't have it (the standard installer from python.org includes
   tkinter, which the GUI menus use; this project was tested with 3.12). Then clone this repo to a
   permanent location, e.g.:

   ```
   git clone https://github.com/ggilman/strata_coder.git D:\tools\strata_coder
   ```

2. Run the setup once, from PowerShell, Git Bash, or Command Prompt:

   ```
   python D:\tools\strata_coder\strata-coder.py -Setup
   ```

   It checks the PC, then opens the setup menu — a GUI window when the machine can display one,
   otherwise a box-drawing console menu (see "The `-Setup` menu" below). With the GUI the command
   finishes right away and the window opens in the background: press **OK** and the install runs in
   its own console window — Strata and OpenCode get installed, the model gets downloaded, and your
   answers get saved; it stops without starting the server or opening OpenCode. Closing the window
   or pressing Cancel changes nothing. In the console menu the questions are asked in the terminal
   and the install follows directly. Either way, open a new shell window afterwards if you added
   the `scode` shortcut, and run `scode` to start using it.

The repo is three scripts that must stay in the same folder: `strata-coder.py` (the command
itself), `setup.py` (the setup menu, its GUI, and all the menu text), and `status.py` (the status
dashboard and its GUI).

## Daily use

Open a terminal (Git Bash, PowerShell, or Command Prompt), change to the project folder you want
OpenCode to work on, and run:

```
cd D:\path\to\my-project
scode
```

`scode` starts the server if needed and opens OpenCode in the current folder; when you quit
OpenCode the server stops and RAM/VRAM are freed. Every command in the table below works the same
way through `scode`.

If you skipped the shortcut, run the full path instead (`python D:\...\strata_coder\strata-coder.py`),
put the `strata_coder` folder on your user PATH and type `strata-coder`, or add the shortcut later
with `strata-coder -Alias`. `-Alias cmd`, `-Alias powershell`, and `-Alias bash` force a
specific shell when detection picks the wrong one.

| Command | What it does |
|---|---|
| `strata-coder` | Sets up whatever is missing, starts the server, and opens OpenCode in the current folder |
| `strata-coder -CheckOnly` | Shows the specs, the chosen model, disk space, and what is installed. Changes nothing |
| `strata-coder -NoLaunch` | Sets up and starts the server only |
| `strata-coder -Stop` | Stops the Strata server |
| `strata-coder -Status` | Live dashboard of the running server: tokens/sec for the current answer plus the average over all answers, context use, GPU/RAM, uptime — in a GUI window when the machine can display one, otherwise refreshing in place in the console (Ctrl+C or closing the window quits) |
| `python status.py [port]` | Opens the status dashboard directly; port defaults to the saved one |
| `strata-coder -Update` | Moves Strata, OpenCode, and ripgrep to their newest releases |
| `strata-coder -Setup` | Setup-parameter menu (see below), then installs and configures everything and stops. Opens the GUI menu automatically when the machine can display one |
| `python setup.py` | Opens the GUI setup window directly — same parameters as `-Setup` |
| `strata-coder -NoGui` | Force the console versions of the setup menu and status dashboard |
| `strata-coder -DataDir E:\models` | Puts the model files somewhere specific |
| `strata-coder -Family coder -Model IQ1_M` | Overrides the automatic model choice |
| `strata-coder -Context 65536` / `-Port 8081` | Sets the context size and server port |
| `strata-coder -Kv q4_0` | Sets the KV cache quant: `int8` (default), `k8v4`, or `q4_0` |
| `strata-coder -Isolation docker` | Keeps OpenCode away from the internet (`none` / `local` / `docker` / `podman` / `wsl`) — the same choice as setup's Isolation, applied to the run; add `-Setup` to save it |
| `strata-coder -Alias` | Adds the permanent `scode` alias to the calling shell |
| `strata-coder -Setup -NoAlias` | Console `-Setup` that skips the `scode` shortcut question |
| `strata-coder -Help` | Shows all options (`--help` also works) |

## The `-Setup` menu

`strata-coder -Setup` asks the setup parameters one by one through a menu: a **GUI window** when the machine can
display one, otherwise a console menu with box-drawing frames, built from the shared menu text in
`setup.py` — nothing extra to download.
Force the console menu any time with `-NoGui`; `python setup.py` opens the GUI directly. In the console menu, move
the `>` cursor with the **arrow keys** (or PgUp/PgDn), type a **digit** to
jump to an option, and press **Enter** to pick it; the default of each question is marked `* default` and comes up
pre-selected, and Esc keeps it. On redirected input or consoles that can't read raw keys, the menus fall back to
plain prompts: press Enter to keep the default, type its number, or for the Variant and Context menus type a name
(e.g. `IQ2_XS`, `64k`). The default is the value saved in `%LOCALAPPDATA%\strata-coder\setup.json` from a previous
run (or the built-in default on the first run). A parameter given on the command line skips its question:
`strata-coder -Setup -Port 8081` never asks for the port. If the console cannot display Unicode box characters,
plain `+ - |` frames are used instead.

1. **Variant** — pick a model by number or by name (e.g. `IQ2_XS`). The list is sorted with the best
   quality on top, and each row grades itself against this PC's RAM and shows its cost and speed:
   `+ Qwen3.8-Flash-Next IQ3_S (60 GB, ~53 tok/s) ★★★` — the fastest one this PC can run carries a
   `⚡`, and the best pick for this PC is pre-selected:

     | Variant | Needs RAM | Download | Answers | Quality |
     |---|---|---|---|---|
     | Qwen3.8-Flash-Next IQ3_S (3.5-bit) | 60 GB | ~84 GB | ~53 tok/s | best |
     | Qwen3.8-Flash-Next IQ3_XXS (3-bit) | 58 GB | ~76 GB | ~62 tok/s (+17%) | great |
     | Qwen3.8-Flash-Next IQ2_XS (2-bit) | 46 GB | ~68 GB | ~79 tok/s (+49%) | better |
     | Qwen3.8-Flash-Next Q2_0 (2-bit, the fastest) | 48 GB | ~66 GB | ~94 tok/s (+77%) | good |
     | Swift 1.5 IQ3_XXS (3-bit; short-thinking fine-tune) | 60 GB | ~76 GB | ~62 tok/s | great |
     | Swift 1.5 IQ2_XS (2-bit; short-thinking fine-tune) | 48 GB | ~68 GB | ~79 tok/s | better |
     | Qwen3.8-Flash-Next **Coder** IQ1_M (code-focused) | 30 GB | ~58 GB | ~55 tok/s | code only |
     | Qwen3.8-Flash-Next (Unsloth) UD-IQ4_XS (~4-bit) | 48 GB | ~94 GB | see below | above IQ3_S |
     | Qwen3.8-Flash-Next (Unsloth) UD-Q4_K_XL (4-bit, experimental) | 48 GB | ~111 GB | see below | best |

    Speeds are Strata's published figures for an RTX 5070 (12 GB) with 64 GB of RAM; the percentage in
    brackets is against IQ3_S. The `ⓘ` button beside the list opens a popup with everything the
    short row leaves out for the highlighted model: the full variant title, why it got its mark, how
    long a 32K-token prompt takes to read, download and running-RAM sizes, the trained 256k context
    limit, and what a step down in size would buy ("IQ3_XXS is +17% (frees 7 GB RAM)").

    Colors and markers: green `+` runs at full speed on this PC, yellow `!` runs but pays for it
    (tight RAM, experts read from the SSD, or experimental), red `x` has too little RAM. Quality stars
    read ★ good, ★★ better, ★★★ best. The GUI shows the same grading as a colored list; the console
    prints the marks and stars under it as a legend. The Unsloth variants keep a RAM budget of their
    experts and read the rest from the SSD on PCs with less than about 80 GB of RAM, so they are only
    recommended on large-RAM PCs; pick them explicitly with `-Family unsloth`.


2. **Model folder** — where the model files live. Default: the folder Strata used before, otherwise
   `%USERPROFILE%\ai-models\models`.
3. **Context** — `0` for the Strata recommendation, or a size from 4k up to 512k (4096, 8192, 16384, 32768, 65536,
   131072, 204800, 262144, 393216, 524288 tokens). Sizes past the model's trained 262144 are rope-extended by Strata
   (YaRN) automatically and need noticeably more RAM for the KV cache.
4. **KV cache** — `int8` (8-bit, default), `k8v4` (8-bit K, 4-bit V), or `q4_0` (4-bit, smallest). Lower quantizations
   save RAM and let you pick a larger context.
5. **Server port** — default 8080.
 6. **Isolation** — pick one option (radio buttons in the GUI, a numbered list in the console):

    - **None** (default) — OpenCode runs on this PC with normal internet access.
    - **Local Only** — OpenCode keeps running on this PC, but nothing it does can leave it: its `webfetch` and
      `websearch` tools are set to `deny`, its `bash` tool gets a deny list for network commands (`curl`, `wget`,
      `git push/clone`, `npm install`, `pip install`, PowerShell `Invoke-WebRequest`, and similar), and it is launched
      with a dead proxy (`http://127.0.0.1:9` with `NO_PROXY` for localhost) that every child process inherits, so
      anything the bash tool spawns that ignores the deny list still cannot reach the internet; the local Strata
      endpoint is unaffected. All of it is undone when OpenCode exits. This covers OpenCode's use of the machine, not
      the setup itself, and it never removes `strata.exe` on its own — a fully offline PC could not re-download or
      rebuild it afterwards. To stop trusting the downloaded binary and force a fresh one, press **Delete Strata** in
      the setup window and run `-Setup` while the PC has network access (see the GUI section). Isolation never blocks
      that rebuild: Strata's own setup runs with the dead proxy stripped from its environment, and the block starts
      when OpenCode starts after it. Combined with the already-disabled autoupdate, sharing, and cloud providers,
      nothing OpenCode does reaches the internet. Note this is still a policy plus an inherited environment, not a
      sandbox — the container choices are the real jail.
    - **Docker** — runs OpenCode inside a Docker container on an internal network: Docker itself blocks all outbound
      traffic from the container, so the only network path is the local model endpoint, reached through a small
      `socat` relay container (the internal network resolves no external names at all). Your current folder is
      mounted read-write at a path named after the folder you run in (`D:\projects\my-app` becomes `/my-app`, so
      OpenCode shows your project name rather than a generic `/workspace`; odd characters are cleaned up and a
      folder named after a system directory like `home` mounts at `/projects/home`), and OpenCode's sessions persist
      in a named volume. The container image ships a full offline developer toolchain — git, the GNU and LLVM C/C++
      toolchains with clang-format, clang-tidy, cppcheck and gdb, Python 3 with pip, venv and dev headers,
      gcc/cmake/ninja/make with the SSL and FFI dev libraries, jq, sqlite3, zip/unzip and xxd, ffmpeg, ImageMagick,
      poppler-utils, shellcheck, tree, and the procps/lsof/strace diagnostics — but deliberately no network
      utilities and no .NET SDKs (Debian has none). Needs Docker Desktop installed (an administrator installs it;
      check its licensing for your company). The Strata server and GPU stay on the host, and `-Status` works
      unchanged. With the sandbox on, the server starts listening on all interfaces of this PC so the relay can
      reach it; what keeps other devices out is Windows Firewall, which blocks inbound to it by default (tell IT the
      server listens on your port while the sandbox is on). Turning the sandbox on restarts a running server once so
      it picks these up — `-Setup` does that automatically.
    - **Podman** — the same container wall driven by the Podman engine instead of Docker: same internal network,
      same sandbox image, same relay, same deny lists. Choose it where Podman is already installed or Docker
      Desktop's licensing is a problem. On Windows it needs a podman machine — start it with `podman machine start`
      before running `scode`.
    - **WSL Containers** — the same container wall run by `systemd-nspawn` (a standard systemd
      component) inside a WSL 2 Linux distro, with no Docker, no Podman, and no container engine
      at all. The container is started with no network interfaces whatsoever — only its own
      loopback exists, so there are no routes, no DNS, and nothing to firewall; the single path to
      the model is a small relay running inside WSL, which reaches the Windows server through
      WSL's mirrored networking (the shared Windows loopback), so the server keeps its
      `127.0.0.1`-only bind. It uses the same Debian dev toolchain image recipe, the same deny
      lists, and the same project-folder mount (`D:\projects\my-app` is mounted from WSL's
      `/mnt/d/projects/my-app` to `/my-app`), and OpenCode's sessions persist in a directory
      inside the distro. Needs Windows plus WSL 2 with systemd in the distro (the stock Ubuntu
      distro qualifies; non-default distros may need `systemd=true` in their `/etc/wsl.conf`) and
      mirrored networking (`[wsl2]` + `networkingMode=mirrored` in `%UserProfile%\.wslconfig`,
      then `wsl --shutdown` — the script detects and tells you). On first use it installs a few
      standard packages inside the distro (`systemd-container`, `debootstrap`) and builds the
      sandbox rootfs there; the Strata server and GPU stay on Windows and `-Status` works
      unchanged.
 7. **Add the `scode` shortcut?** — yes/no. Adds the `scode` alias to the detected shell (Command Prompt, PowerShell, or
   Git Bash), the same as `strata-coder -Alias`. Open a new shell window afterwards to pick it up. The GUI handles the
   shortcut differently: it is an instant action there, not a question — see the GUI section.

When the install finishes, `-Setup` prints a **memory stability** summary: the estimated peak RAM the
server will use with your chosen variant, context, and KV cache (the KV cache size is measured from the
downloaded model file), compared against this PC's RAM. It is rated **comfortable** (8+ GB spare),
**tight** (2-8 GB spare: close other large apps while the server runs), or **risky** (under 2 GB spare:
the server can run out of RAM and crash). For tight or risky ratings it also suggests a context size
that keeps a safe margin.

The answers are saved to `setup.json` and every later run reuses them. `-Setup` re-asks and re-saves, then stops
without starting the server or opening OpenCode; run `scode` afterwards to use it. The model is reconfigured in place
(no re-download) only when an engine-relevant answer changes — model folder, context, or KV cache; port, isolation,
and the `scode` shortcut never touch the engine. The same parameters can also be set without the menu via
`-Family`, `-Model`, `-Context`, `-Kv`, `-DataDir`, `-Port`, and `-Isolation`.

### The GUI menu

The same menu also exists as one Tkinter window, opened directly with `python setup.py` or automatically by
`strata-coder -Setup` when the machine can display one (it needs tkinter, which ships with the standard Python
installers). The window opens **detached**: the command you typed finishes right away and the window lives on its
own, so your terminal is free; close the window to back out. The form has a variant dropdown (variants that don't
fit this PC's RAM are marked), model folder with a **Browse…** button, port, an **Isolation** radio group
(**None / Local Only / Docker / Podman / WSL Containers**), and
**Context** and **KV cache** dropdowns — every one of them has an **ⓘ** popup that spells out its trade-offs.
Fields come up pre-filled from `setup.json`, and command-line parameters (e.g. `strata-coder -Setup -Port 8081`)
pre-fill over the saved values.

In the window the `scode` shortcut is an **action**, not a question: pick the shell (`auto`, `cmd`, `powershell` —
`auto`, `bash`, `zsh` on Linux) and press **Set Alias** to add it right away; a short inline note confirms
"open a new shell window to use it". The **ⓘ** button next to it opens a popup explaining exactly which profile,
macro or registry entry each shell gets and why. Press it as often as you like — it replaces the previous entry.

**Delete Strata** is an instant action too: it removes the downloaded `strata.exe` (including any copy under
the model folder) and an inline note confirms it, so the next `-Setup` re-creates the engine from a fresh download
instead of trusting the binary on disk. Press it only when that next `-Setup` can reach the network — the re-download
is Strata's only way back.

**OK** then starts `strata-coder -Setup` with every chosen parameter plus `-NoAlias -NoGui` in its own console
window (a new one on Windows, a terminal window on Linux), which saves the settings, runs the install and configure
steps, and shows the memory-stability summary before stopping. Closing the window or pressing **Cancel** exits and
changes nothing (a shortcut already set stays — it was an instant action).

The questions, option labels, and hints for both menus live in `setup.py`, so the console menu and the window always
ask exactly the same things.

### The status dashboard

`strata-coder -Status` (or `python status.py [port]`) shows the live server dashboard: tokens/sec for the current
answer, the all-time average, context use, GPU/RAM, requests in flight, and uptime. When the machine can display
one it opens as a GUI window — the border and the status line change color with server state (green while
generating, yellow while busy, gray while idle) — and it keeps retrying on screen if the server goes away. The GUI
opens detached: the window appears and the command finishes right away, so your terminal is free while the window
runs on its own. Without a display it refreshes a box-drawing panel in place in the console, like before (Ctrl+C
quits). `-NoGui` forces the console panel, and the port defaults to the saved one — or pass it:
`python status.py 8081`. The code for both presentations lives in `status.py`.

## What it does

1. **Checks the PC.** It looks at the NVIDIA GPU (needs RTX 20 or newer, 12 GB+ VRAM, and driver 580+), RAM, and CPU,
   then picks the best variant from the table above; `less than 30 GB` stops: the PC can't run any variant properly.

2. **Finds what already exists.** It looks for a Strata install (including one you set up yourself, which Strata records in
   `%APPDATA%\Strata\settings.json`), an already-prepared model, matching GGUFs in the Hugging Face cache, OpenCode, and a
   server already running. It skips whatever is there.
3. **Checks disk space** before downloading. Model files go to the folder Strata used before, otherwise to
    `%USERPROFILE%\ai-models\models`. The setup parameters (model, folder, context, KV cache, port, isolation) are saved in
   `%LOCALAPPDATA%\strata-coder\setup.json`; `strata-coder -Setup` asks for them again and re-saves the file, and
   every other run reads them to fill in whatever the command line did not decide.
4. **Installs** Strata, the model, OpenCode, and [ripgrep](https://github.com/BurntSushi/ripgrep) into `%LOCALAPPDATA%\strata-coder\`. If interrupted, run it again and the download resumes. ripgrep is the `rg` command OpenCode uses for content search; its folder is added to `PATH` when OpenCode starts, and a failed ripgrep download is only a warning (search still works, just slower).
5. **Starts the server** hidden on `http://127.0.0.1:8080/v1`, with its output in `%LOCALAPPDATA%\strata-coder\logs\server.log`. The server is OpenAI- and Anthropic-compatible. Each run also keeps a transcript in `logs\run-*.log`. A fresh install uses the tested Strata release (v0.1.40.3); `strata-coder -Update` moves Strata and OpenCode to their newest releases and keeps the old Strata if the new one fails to set up.
6. **Opens OpenCode** in the current folder. Its config is in `%LOCALAPPDATA%\strata-coder\opencode.json`, so nothing is
   written into your project.

When you quit OpenCode, the script stops the Strata server so RAM and VRAM are freed. With `-NoLaunch` the server keeps
running after the script exits; stop it with `strata-coder -Stop`.

## Isolation

One **Isolation** setting decides how OpenCode is kept from the internet (setup Q6, or `-Isolation` on the command
line); the full wording for each choice is in the `-Setup` menu questions above. The Strata server and GPU always stay
on the host, and `-Status` works the same in every mode.

- **None** (`-Isolation none`, the default) — OpenCode runs on this PC with normal internet access; only the normal
  OpenCode safety prompts apply.
- **Local Only** (`-Isolation local`) — a *policy plus an inherited environment*, not a sandbox. OpenCode's `webfetch`
  and `websearch` tools are set to `deny`, its `bash` tool gets a deny list for network commands (`curl`, `wget`,
  `git push/clone`, `npm install`, `pip install`, `Invoke-WebRequest`, and similar), and OpenCode is launched with a
  dead proxy (`http://127.0.0.1:9`, `NO_PROXY` for localhost) that every child process inherits, so anything that
  ignores the deny list still cannot get out; the local Strata endpoint is unaffected. Combined with the
  already-disabled autoupdate, sharing, and cloud providers, nothing OpenCode does reaches the internet. Switching
  back to `none` restores the normal config and clears the proxy. This covers OpenCode's use of the machine, not the
  setup: Strata's own setup runs with the sinkhole proxy stripped so downloads and rebuilds are never blocked, and
  the block only starts when OpenCode launches afterward. A hard block on the host still needs an outbound firewall
  rule for `opencode.exe`, which requires admin.
- **Docker** (`-Isolation docker`) and **Podman** (`-Isolation podman`) — a *real* sandbox driven by either engine
  through the same code path: OpenCode runs in a container on an internal network where outbound traffic is blocked
  and no external names resolve; the only network path is the local model endpoint through a `socat` relay container.
  Your current folder is mounted read-write at a path named after it, sessions persist in a named volume, and the
  image ships an offline developer toolchain but deliberately no network utilities. Inside the container the Local
  Only policies apply as well. Docker needs Docker Desktop/Engine (admin installs it; check licensing); Podman needs
  a podman machine started on Windows. While one of these is on, the server listens on all interfaces of this PC
  so the relay can reach it, and Windows Firewall is what keeps other devices out; `-Setup` restarts a running
  server once so it picks this up. The script reports a clear error instead of running unprotected if the
  chosen engine is missing.
- **WSL Containers** (`-Isolation wsl`, Windows only) — the same wall with no container engine at all: the same
  Debian rootfs is run by `systemd-nspawn` inside a systemd-enabled WSL 2 distro, and the container is given
  *no network interfaces* (`--private-network`), so there is nothing to block — the only path to the model is a
  unix socket relayed inside WSL to the server's localhost port, which works because WSL mirrored networking
  shares the Windows loopback (the server therefore keeps its `127.0.0.1`-only bind — nothing is opened on
  other interfaces). Needs WSL 2 with systemd in the distro and mirrored networking (both checked, with exact
  instructions on failure); the script installs `systemd-container` and `debootstrap` inside the distro and
  builds the rootfs and session directory there on first use. The same project-folder mount, toolchain, and deny
  lists as Docker; network shares (`\\server\...` folders) cannot be mounted, same as the other sandboxes.
- **Delete Strata** (the setup-window button) — a one-shot action, not a setting.
  It removes the downloaded `strata.exe` (and any copy under the model folder) so the next `-Setup` rebuilds the engine
  from a fresh download instead of trusting the binary on disk — the way to stop trusting an unsigned download.
  Isolation never deletes the engine on its own, because a fully offline PC could not get it back; press it only when
  that next `-Setup` can reach the network.

Pick **Local Only** for a lightweight policy-level guard while OpenCode runs on the host, or Docker/Podman/WSL
Containers for container-enforced isolation (WSL Containers is the one that installs no container engine at all).
Any choice can be set without the menu via `-Isolation none|local|docker|podman|wsl`; it applies to the run it is
given on, and to later runs when combined with `-Setup`.

## Notes

- OpenCode runs as your user, not in a sandbox, unless you pick **Docker**, **Podman**, or **WSL Containers**
  for Isolation. It starts in your project folder and asks before risky actions.
- On a managed PC, the only thing that needs IT is a current NVIDIA driver (580 or newer) — plus Docker Desktop
  (or Podman) if you want those container options, which an administrator must install (and whose licensing for
  your company IT should confirm). The **WSL Containers** option needs no third-party install at all, only WSL 2
  (enable once with `wsl --install`, which an administrator does) and its mirrored networking mode, both checked
  and explained by the script. With a container isolation on, the script reports a clear error instead of running
  unprotected when its engine is missing.
- If Strata has no ready-made engine for the GPU, it would want to install Visual Studio and the CUDA Toolkit, which needs admin. The script
  stops instead.
- Strata's engine (`strata.exe`) is not code-signed, so Windows Smart App Control blocks it. The script stops at the PC check if
  Smart App Control is on and tells you how to turn it off. If something still blocks the engine, the script checks the Windows
  Code Integrity log and names the blocked file.
