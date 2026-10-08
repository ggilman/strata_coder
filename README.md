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
| `strata-coder -Stop` | Stops the Strata server |
| `strata-coder -Stats` | Shows the live tokens/sec of the running server (Ctrl+C to quit) |
| `strata-coder -Setup` | Asks for the setup parameters (model, folder, context, port, launch), saves them to `setup.json`, then sets up |
| `strata-coder -DataDir E:\models` | Puts the model files somewhere specific |
| `strata-coder -Family coder -Model IQ1_M` | Overrides the automatic model choice |
| `strata-coder -Context 65536` / `-Port 8081` | Sets the context size and server port |
| `strata-coder --alias` | Adds the permanent `scode` alias to the calling shell |
| `strata-coder --help` | Shows all options |

## What it does

1. **Checks the PC.** It looks at the NVIDIA GPU (needs RTX 20 or newer, 12 GB+ VRAM, and driver 580+), RAM, and CPU, then picks the best variant:

    | RAM | Variant | Download |
    |---|---|---|
    | 64 GB | Qwen3.8-Flash-Next IQ3_S | ~84 GB |
    | 60 GB | Qwen3.8-Flash-Next IQ3_XXS | ~76 GB |
    | 48 GB | Qwen3.8-Flash-Next IQ2_XS | ~68 GB |
    | 32 GB | Qwen3.8-Flash-Next **Coder** IQ1_M | ~58 GB |
    | less | Stops: the PC can't run any variant properly | |

2. **Finds what already exists.** It looks for a Strata install (including one you set up yourself, which Strata records in
    `%APPDATA%\Strata\settings.json`), an already-prepared model, matching GGUFs in the Hugging Face cache, OpenCode, and a
    server already running. It skips whatever is there.
3. **Checks disk space** before downloading. Model files go to the folder Strata used before, otherwise to
   `%USERPROFILE%\ai-models\models`. The setup parameters (model, folder, context, port, launch) are saved in
   `%LOCALAPPDATA%\strata-coder\setup.json`; `strata-coder -Setup` asks for them again and re-saves the file, and
   every other run reads them to fill in whatever the command line did not decide.
4. **Installs** Strata, the model, and OpenCode into `%LOCALAPPDATA%\strata-coder\`. If interrupted, run it again and the download resumes.
5. **Starts the server** hidden on `http://127.0.0.1:8080/v1`, with its output in `%LOCALAPPDATA%\strata-coder\logs\server.log`. The server is OpenAI- and Anthropic-compatible. Each run also keeps a transcript in `logs\run-*.log`. A fresh install uses the tested Strata release (v0.1.40.3); `strata-coder -Update` moves Strata and OpenCode to their newest releases and keeps the old Strata if the new one fails to set up.
6. **Opens OpenCode** in the current folder. Its config is in `%LOCALAPPDATA%\strata-coder\opencode.json`, so nothing is
   written into your project.

The server keeps running after you quit OpenCode, so the next start is instant. Use `strata-coder -Stop` to free the RAM and VRAM.

## Notes

- OpenCode runs as your user, not in a sandbox. It starts in your project folder and asks before risky actions.
- On a managed PC, the only thing that needs IT is a current NVIDIA driver (580 or newer).
- If Strata has no ready-made engine for the GPU, it would want to install Visual Studio and the CUDA Toolkit, which needs admin. The script
  stops instead.
