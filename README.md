# strata-coder

This tool runs a local AI coding assistant: [OpenCode](https://opencode.ai), backed by **Qwen3.8-Flash-Next** served by
[Strata](https://github.com/Niko1221/Strata). Everything installs per-user, so no admin rights and no Docker are needed.

## Use

Run it from the project folder you want OpenCode to work on:

```
cd D:\path\to\my-project
D:\...\strata_coder\strata-coder.cmd
```

(Or put the `strata_coder` folder on your user PATH and just type `strata-coder`.)

For a permanent shortcut, run `strata-coder --alias`: it detects the calling shell (Command Prompt, PowerShell, or Git Bash) and adds a `scode` alias to it, so you can type `scode` from then on. `--alias cmd`, `--alias powershell`, and `--alias bash` force a specific shell when detection picks the wrong one.

| Command | What it does |
|---|---|
| `strata-coder` | Sets up whatever is missing, starts the server, and opens OpenCode in the current folder |
| `strata-coder -CheckOnly` | Shows the specs, the chosen model, disk space, and what is installed. Changes nothing |
| `strata-coder -NoLaunch` | Sets up and starts the server only |
| `strata-coder -Launch` | Opens OpenCode even if a previous `-Setup` saved "do not open it" |
| `strata-coder -Stop` | Stops the Strata server |
| `strata-coder -Stats` | Shows the live tokens/sec of the running server (Ctrl+C to quit) |
| `strata-coder -Update` | Moves Strata and OpenCode to their newest releases |
| `strata-coder -Setup` | Interactive menu for the setup parameters (see below), then sets up |
| `strata-coder -DataDir E:\models` | Puts the model files somewhere specific |
| `strata-coder -Family coder -Model IQ1_M` | Overrides the automatic model choice |
| `strata-coder -Context 65536` / `-Port 8081` | Sets the context size and server port |
| `strata-coder -Kv q4_0` | Sets the KV cache quant: `int8` (default), `k8v4`, or `q4_0` |
| `strata-coder --alias` | Adds the permanent `scode` alias to the calling shell |
| `strata-coder --help` | Shows all options |

## The `-Setup` menu

`strata-coder -Setup` walks you through the setup parameters one by one. Pressing Enter keeps the displayed default,
which is the value saved in `%LOCALAPPDATA%\strata-coder\setup.json` from a previous run (or the built-in default on
the first run). A parameter given on the command line skips its question: `strata-coder -Setup -Port 8081` never asks
for the port.

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
3. **Context** — `0` for the Strata recommendation, or a size from 4k up to 256k (4096, 8192, 16384, 32768, 65536,
   131072, 262144 tokens).
4. **KV cache** — `int8` (8-bit, default), `k8v4` (8-bit K, 4-bit V), or `q4_0` (4-bit, smallest). Lower quantizations
   save RAM and let you pick a larger context.
5. **Server port** — default 8080.
6. **Open OpenCode after setup?** — yes/no.

The answers are saved to `setup.json` and every later run reuses them. `-Setup` re-asks and re-saves; if the saved
context or KV cache differs from what the model was configured with, the next run reconfigures the model in place
(no re-download). The same parameters can also be set without the menu via `-Family`, `-Model`, `-Context`, `-Kv`,
`-DataDir`, and `-Port`.

## What it does

1. **Checks the PC.** It looks at the NVIDIA GPU (needs RTX 20 or newer, 12 GB+ VRAM, and driver 580+), RAM, and CPU,
   then picks the best variant from the table above; `less than 30 GB` stops: the PC can't run any variant properly.

2. **Finds what already exists.** It looks for a Strata install (including one you set up yourself, which Strata records in
    `%APPDATA%\Strata\settings.json`), an already-prepared model, matching GGUFs in the Hugging Face cache, OpenCode, and a
    server already running. It skips whatever is there.
3. **Checks disk space** before downloading. Model files go to the folder Strata used before, otherwise to
   `%USERPROFILE%\ai-models\models`. The setup parameters (model, folder, context, KV cache, port, launch) are saved in
   `%LOCALAPPDATA%\strata-coder\setup.json`; `strata-coder -Setup` asks for them again and re-saves the file, and
   every other run reads them to fill in whatever the command line did not decide.
4. **Installs** Strata, the model, and OpenCode into `%LOCALAPPDATA%\strata-coder\`. If interrupted, run it again and the download resumes.
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
