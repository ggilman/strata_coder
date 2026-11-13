# strata_coder — Code Structure

View with VS Code built-in Markdown preview (`Ctrl+Shift+V`). No extensions required.

## Module Relationships

```mermaid
flowchart LR
    CLI["strata-coder.py<br/>(main CLI, entry point)"]
    SETUP["setup.py<br/>(setup menu / GUI)"]
    STATUS["status.py<br/>(status panel / GUI)"]

    CLI -- "load_setup()<br/>importlib.util" --> SETUP
    CLI -- "load_status()<br/>importlib.util" --> STATUS
    SETUP -- "load_cli()<br/>importlib.util" --> CLI
    STATUS -- "load_cli()<br/>importlib.util" --> CLI

    subgraph EXT["External processes"]
        SERVER["strata server<br/>(local HTTP)"]
        OC["opencode sandbox<br/>(WSL / container)"]
    end
    CLI -- "health / models / chat<br/>HTTP + sockets" --> SERVER
    CLI -- "run_opencode_wsl<br/>run_opencode_sandbox" --> OC
```

## Inside strata-coder.py (main module)

```mermaid
flowchart TD
    MAIN["main() / parse_args()<br/>CLI dispatch"]

    subgraph UX["UI & prompts"]
        WRITE["write_step/ok/info/skip/warn, fail()"]
        BOX["build_box(), _wrap(), box_glyphs()<br/>(ASCII box rendering)"]
        PROMPT["prompt_choice/text/port/yes_no()<br/>_interactive_choice()"]
    end

    subgraph STATE["State & system"]
        SAVE["save_state(), lock_file()<br/>read_json/write_text_file"]
        SPEC["get_system_spec(), _ram_gb()<br/>grade_variant(), pick_best_variant()"]
    end

    subgraph DL["Downloads & install"]
        DL1["download_file(), extract_archive()<br/>get_downloaded_file()"]
        GH["get_latest_release()<br/>install_tool_from_release()<br/>install_ripgrep()"]
        GGUF["read_gguf_meta(), get_gguf_arch()<br/>get_kv_cache_gb()<br/>(GGUF model metadata)"]
    end

    subgraph SRV["strata server control"]
        HC["strata_health/models/ready()<br/>strata_chat_completion()"]
        PROC["port_in_use(), strata_server_processes()<br/>stop_strata_servers(), wait_strata_ready()"]
    end

    subgraph SBX["Sandbox / isolation"]
        WSL["WSL helpers<br/>(via status.py-style relay,<br/>ensure_wsl_rootfs, wsl_start_relay)"]
        POD["ensure_sandbox_image()<br/>stage_sandbox_binaries()<br/>start_sandbox_relay()<br/>run_opencode_sandbox()"]
    end

    subgraph COMP["Companions (importlib)"]
        LS["load_setup(), load_status(),<br/>run_setup_menu()"]
    end

    MAIN --> UX
    MAIN --> STATE
    MAIN --> DL
    MAIN --> SRV
    MAIN --> SBX
    MAIN --> LS
    DL --> GGUF
    SRV --> PROC
    SBX --> SRV
```

## setup.py / status.py (companion modules)

```mermaid
flowchart TD
    subgraph SETUP["setup.py"]
        SMAIN["main() / run_setup_gui()<br/>menu, choices, apply_setup()"]
        SINFO["variant_label(), grade_legend(),<br/>isolation_options(), context_options()"]
        SGUI["spawn_detached_gui()"]
        SMAIN --> SINFO
        SMAIN --> SGUI
    end

    subgraph STATUS["status.py"]
        TMAIN["main() / run_status()<br/>show_status_panel / show_status_window"]
        TBAR["format_bar(), format_usage_bar(),<br/>bar_grade()"]
        TPOLL["poll_once(), resolve_server()<br/>(HTTP + unix-socket relay)"]
        TMAIN --> TBAR
        TMAIN --> TPOLL
    end

    CLI["strata-coder.py"] -- "load_setup()" --> SETUP
    CLI -- "load_status()" --> STATUS
    SETUP -- "load_cli() for state, spec,<br/>download & server helpers" --> CLI
    STATUS -- "load_cli() for server<br/>discovery & health" --> CLI
```
