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
| `strata-coder -Offline` | Blocks OpenCode's web tools; `-Offline false` turns the block off |
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

1. **Variant** — pick a model by number or by name (e.g. `IQ2_XS`). The default is the best variant that fits this
   PC's RAM:

    | Variant | Needs RAM | Download |
    |---|---|---|
    | Qwen3.8-Flash-Next IQ3_S (3.5-bit, best quality) | 60 GB | ~84 GB |
    | Qwen3.8-Flash-Next IQ3_XXS (3-bit) | 58 GB | ~76 GB |
    | Qwen3.8-Flash-Next IQ2_XS (2-bit) | 46 GB | ~68 GB |
    | Qwen3.8-Flash-Next **Coder** IQ1_M (code-focused) | 30 GB | ~58 GB |

2. **Model folder** — where the model files live. Default: the folder Strata used before, otherwise
   `%USERPROFILE%\ai-models\models`.
3. **Context** — `0` for the Strata recommendation, or a size from 4k up to 512k (4096, 8192, 16384, 32768, 65536,
   131072, 204800, 262144, 393216, 524288 tokens). Sizes past the model's trained 262144 are rope-extended by Strata
   (YaRN) automatically and need noticeably more RAM for the KV cache.
4. **KV cache** — `int8` (8-bit, default), `k8v4` (8-bit K, 4-bit V), or `q4_0` (4-bit, smallest). Lower quantizations
   save RAM and let you pick a larger context.
5. **Server port** — default 8080.
6. **Stay fully local, block OpenCode web access?** — yes/no. On, OpenCode's `webfetch` and `websearch` tools are set to
   `deny` in its config, so the assistant itself cannot browse the web or search. Combined with the already-disabled
   autoupdate, sharing, and cloud providers, nothing OpenCode does reaches the internet. Note this is a policy inside
   OpenCode, not a sandbox: its `bash` tool can still run network commands like `curl`. A hard block needs an outbound
   firewall rule for `opencode.exe`, which requires admin.
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
without starting the server or opening OpenCode; run `scode` afterwards to use it. If the saved
context or KV cache differs from what the model was configured with, the next run reconfigures the model in place
(no re-download). The same parameters can also be set without the menu via `-Family`, `-Model`, `-Context`, `-Kv`,
`-DataDir`, `-Port`, and `-Offline`.

### The GUI menu

The same menu also exists as one Tkinter window, opened directly with `python setup.py` or automatically by
`strata-coder -Setup` when the machine can display one (it needs tkinter, which ships with the standard Python
installers). The window opens **detached**: the command you typed finishes right away and the window lives on its
own, so your terminal is free; close the window to back out. The form has a variant dropdown (variants that don't
fit this PC's RAM are marked), model folder with a **Browse…** button, context and KV-cache dropdowns, port, and
an *Stay fully local* checkbox. Fields come up pre-filled from `setup.json`, and command-line parameters (e.g.
`strata-coder -Setup -Port 8081`) pre-fill over the saved values.

In the window the `scode` shortcut is an **action**, not a question: pick the shell (`auto`, `cmd`, `powershell` —
`auto`, `bash`, `zsh` on Linux) and press **Set scode shortcut** to add it right away; an inline note confirms
"open a new shell window to use it". Press it as often as you like — it replaces the previous entry.

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
   `%USERPROFILE%\ai-models\models`. The setup parameters (model, folder, context, KV cache, port, offline) are saved in
   `%LOCALAPPDATA%\strata-coder\setup.json`; `strata-coder -Setup` asks for them again and re-saves the file, and
   every other run reads them to fill in whatever the command line did not decide.
4. **Installs** Strata, the model, OpenCode, and [ripgrep](https://github.com/BurntSushi/ripgrep) into `%LOCALAPPDATA%\strata-coder\`. If interrupted, run it again and the download resumes. ripgrep is the `rg` command OpenCode uses for content search; its folder is added to `PATH` when OpenCode starts, and a failed ripgrep download is only a warning (search still works, just slower).
5. **Starts the server** hidden on `http://127.0.0.1:8080/v1`, with its output in `%LOCALAPPDATA%\strata-coder\logs\server.log`. The server is OpenAI- and Anthropic-compatible. Each run also keeps a transcript in `logs\run-*.log`. A fresh install uses the tested Strata release (v0.1.40.3); `strata-coder -Update` moves Strata and OpenCode to their newest releases and keeps the old Strata if the new one fails to set up.
6. **Opens OpenCode** in the current folder. Its config is in `%LOCALAPPDATA%\strata-coder\opencode.json`, so nothing is
   written into your project.

When you quit OpenCode, the script stops the Strata server so RAM and VRAM are freed. With `-NoLaunch` the server keeps
running after the script exits; stop it with `strata-coder -Stop`.

## Notes

- OpenCode runs as your user, not in a sandbox. It starts in your project folder and asks before risky actions.
- On a managed PC, the only thing that needs IT is a current NVIDIA driver (580 or newer).
- If Strata has no ready-made engine for the GPU, it would want to install Visual Studio and the CUDA Toolkit, which needs admin. The script
  stops instead.
- Strata's engine (`strata.exe`) is not code-signed, so Windows Smart App Control blocks it. The script stops at the PC check if
  Smart App Control is on and tells you how to turn it off. If something still blocks the engine, the script checks the Windows
  Code Integrity log and names the blocked file.
