#!/usr/bin/env python3
"""status.py - the live Strata status panel, console and GUI.

strata-coder.py loads this file for -Status: the GUI window opens when the
machine can display one, otherwise the console panel refreshes in place
(force it with -NoGui). Running this file directly shows the panel right
away:

    python status.py [port]

The port defaults to the one saved in setup.json (or 8080).
"""

import importlib.util
import json
import os
import shutil
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_PATH = os.path.join(HERE, "strata-coder.py")

# ----------------------------------------------------------------------------------------------------
# Panel text shared by the console and GUI panels
# ----------------------------------------------------------------------------------------------------

STATUS_RETRIES = 3
PANEL_TITLE = " Strata speed  %s  %s "
PANEL_HINT = " Ctrl+C quits - updates every 2 s at %s"

LIVE_ANSI = {"Green": "\033[32m", "Yellow": "\033[33m", "DarkGray": "\033[90m"}
LIVE_GUI = {"Green": "#1a9e3f", "Yellow": "#b58900", "DarkGray": "#8a8a8a"}
BORDER_GUI = {46: "#1a9e3f", 220: "#b58900", 241: "#767676"}
GUI_HINT = "updates every 2 s at %s - close this window to quit"
NO_SERVER_TEXT = "no Strata server is answering on port %d - retrying every 2 s"
LOST_TEXT = "lost the connection to the server (%s) - retrying every 2 s"


def load_cli():
    if not os.path.exists(CLI_PATH):
        raise SystemExit("strata-coder.py must sit next to this script: %s" % CLI_PATH)

    spec = importlib.util.spec_from_file_location("strata_coder_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def is_available():
    try:
        import tkinter as tk
    except ImportError:
        return False

    try:
        root = tk.Tk()
    except Exception:
        return False

    try:
        root.destroy()
    except Exception:
        pass

    return True


# ----------------------------------------------------------------------------------------------------
# Server discovery and polling shared by both panels
# ----------------------------------------------------------------------------------------------------

def resolve_server(sc, server_port):
    processes = sc.strata_server_processes()
    process = processes[0] if processes else None

    health = sc.strata_health(server_port)

    if not health and process:
        server_port = sc.server_port(process)
        health = sc.strata_health(server_port)

    return server_port, health


def poll_once(base):
    """Return ((status_info, v1_status, totals), None) or (None, error)."""
    try:
        request = urllib.request.Request(base + "/status")
        with urllib.request.urlopen(request, timeout=5) as response:
            status_info = json.load(response)
        request = urllib.request.Request(base + "/v1/status")
        with urllib.request.urlopen(request, timeout=5) as response:
            v1_status = json.load(response)
    except Exception as exc:
        return None, exc

    totals = None

    try:
        request = urllib.request.Request(base + "/metrics")
        with urllib.request.urlopen(request, timeout=5) as response:
            totals = (json.load(response) or {}).get("totals")
    except Exception:
        pass

    return (status_info, v1_status, totals), None


def get_status_display(status_info, v1_status, totals):
    timing = v1_status.get("last_timings")
    machine = v1_status.get("machine") or {}
    activity = v1_status.get("activity") or {}

    if status_info.get("busy") and status_info.get("tokens_per_s"):
        live_text = "generating at %.1f tok/s" % float(status_info["tokens_per_s"])
        border256 = 46
        live_color = "Green"
    elif status_info.get("busy"):
        live_text = "busy"
        border256 = 220
        live_color = "Yellow"
    else:
        live_text = "idle"
        border256 = 241
        live_color = "DarkGray"

    phase_text = ("  phase: %s" % status_info["phase"]) if status_info.get("phase") else ""

    body_lines = ["status:   %s%s" % (live_text, phase_text)]

    if timing and timing.get("predicted_per_second"):
        body_lines.append(
            "answer:   %s tok at %.1f tok/s, cache %s"
            % (timing.get("predicted_n"), float(timing["predicted_per_second"]), timing.get("cache_n")))
        body_lines.append(
            "prompt:   %s tok at %.1f tok/s"
            % (timing.get("prompt_n"), float(timing.get("prompt_per_second") or 0)))
    else:
        body_lines.append("answer:   none yet")
        body_lines.append("prompt:   none yet")

    if totals and float(totals.get("output_tokens") or 0) > 0 and float(totals.get("decode_ms") or 0) > 0:
        body_lines.append(
            "average:  %.1f tok/s over %s answers since start"
            % (100 * float(totals["output_tokens"]) / float(totals["decode_ms"]),
               totals.get("requests")))
    else:
        body_lines.append("average:  none yet")

    if v1_status.get("cache_max_tokens") and status_info.get("prompt_tokens") is not None:
        body_lines.append(
            "context:  %s / %s (%.0f%%)"
            % (status_info["prompt_tokens"], v1_status["cache_max_tokens"],
               100 * float(status_info["prompt_tokens"]) / float(v1_status["cache_max_tokens"])))
    else:
        body_lines.append("context:  unknown")

    gpu = machine.get("gpu")

    if gpu:
        body_lines.append(
            "gpu:      %.0f%% util, %.0f C, %.0f W, VRAM %.1f / %.1f GiB"
            % (round(float(gpu.get("util_pct") or 0)), round(float(gpu.get("temp_c") or 0)),
               float(gpu.get("power_w") or 0),
               float(gpu.get("used_mib") or 0) / 1024, float(gpu.get("total_mib") or 0) / 1024))
    else:
        body_lines.append("gpu:      n/a")

    ram = machine.get("ram")

    if ram:
        body_lines.append("ram:      %.1f / %.1f GiB"
                          % (float(ram.get("used_gib") or 0), float(ram.get("total_gib") or 0)))
    else:
        body_lines.append("ram:      n/a")

    body_lines.append(
        "requests: %s total, %s in flight, %s queued"
        % (activity.get("requests"), activity.get("in_flight"), status_info.get("queued")))

    up_s = float(v1_status.get("uptime_s") or 0)
    if up_s >= 3600:
        up_text = "%dh %dm" % (int(up_s // 3600), int((up_s % 3600) // 60))
    else:
        up_text = "%dm" % int(up_s // 60)

    mtp_text = ""
    if timing and timing.get("draft_n"):
        mtp_text = "  MTP accept %.0f%%" % (100 * float(timing.get("draft_n_accepted") or 0) / float(timing["draft_n"]))

    body_lines.append("uptime:   %s  engine %s%s"
                      % (up_text, v1_status.get("engine"), mtp_text))

    return {
        "body_lines": body_lines,
        "live_color": live_color,
        "border256": border256,
        "model_text": v1_status.get("model") or "model",
    }


# ----------------------------------------------------------------------------------------------------
# Console panel
# ----------------------------------------------------------------------------------------------------

def show_status_panel(sc, server_port):
    server_port, health = resolve_server(sc, server_port)

    if not health:
        sc.fail("no Strata server is answering on port %d." % server_port)

    base = "http://127.0.0.1:%d" % server_port

    interactive = sys.stdout.isatty()

    try:
        width = min(78, shutil.get_terminal_size().columns - 1)
    except Exception:
        interactive = False
        width = 78

    panel_top_drawn = False
    previous_lines = 0
    failures = 0

    if interactive:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()

    try:
        while True:
            data, exc = poll_once(base)

            if data is None:
                failures += 1

                if failures >= STATUS_RETRIES:
                    sc.fail("lost the connection to the server (%s)" % exc)

                time.sleep(2)
                continue

            failures = 0
            status_info, v1_status, totals = data

            display = get_status_display(status_info, v1_status, totals)
            body_lines = list(display["body_lines"])

            rows = []

            for index, line in enumerate(body_lines):
                color = LIVE_ANSI[display["live_color"]] if (index == 0 and sc.COLOR_ON) else None
                rows.append((line, color))

            rows.append(None)
            rows.append((PANEL_HINT % time.strftime("%H:%M:%S"),
                         sc.DARKGRAY if sc.COLOR_ON else None))

            block = "\n".join(sc.build_box(
                PANEL_TITLE % (display["model_text"], base),
                rows, width, "\033[38;5;%sm" % display["border256"]))

            if not block.endswith("\n"):
                block += "\n"

            if interactive and panel_top_drawn:
                sys.stdout.write("\033[%dA" % previous_lines)

            sys.stdout.write(block)
            sys.stdout.flush()

            panel_top_drawn = True
            previous_lines = block.count("\n")

            time.sleep(2)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        sys.stdout.flush()
        sys.exit(0)


# ----------------------------------------------------------------------------------------------------
# GUI panel
# ----------------------------------------------------------------------------------------------------

def show_status_window(sc, server_port):
    import tkinter as tk

    server_port, health = resolve_server(sc, server_port)
    base = "http://127.0.0.1:%d" % server_port

    root = tk.Tk()
    root.title("Strata speed")
    root.configure(bg="#767676")

    outer = tk.Frame(root, bg="white", highlightthickness=2, highlightbackground="#767676")
    outer.pack(fill="both", expand=True, padx=10, pady=10)

    if sc.IS_WINDOWS:
        body_font = ("Consolas", 11)
        title_font = ("Segoe UI", 11, "bold")
    else:
        body_font = ("Monospace", 11)
        title_font = ("Sans", 11, "bold")

    title_label = tk.Label(outer, text="Strata speed  %s" % base,
                           font=title_font, bg="white", fg="#767676")
    title_label.pack(anchor="w", padx=12, pady=(10, 0))

    lines_frame = tk.Frame(outer, bg="white")
    lines_frame.pack(fill="both", expand=True, padx=12, pady=(6, 0))

    footer = tk.Label(outer, text="", font=("Segoe UI", 9) if sc.IS_WINDOWS else ("Sans", 9),
                      bg="white", fg="#8a8a8a")
    footer.pack(anchor="w", padx=12, pady=(8, 10))

    ever_connected = bool(health)

    def tick():
        nonlocal ever_connected

        data, exc = poll_once(base)

        for child in lines_frame.winfo_children():
            child.destroy()

        if data is None:
            message = NO_SERVER_TEXT % server_port if not ever_connected else LOST_TEXT % exc
            tk.Label(lines_frame, text=message, font=body_font, bg="white",
                     fg="#c0392b", anchor="w").pack(fill="x")
            footer.configure(text=GUI_HINT % time.strftime("%H:%M:%S"))
        else:
            status_info, v1_status, totals = data
            ever_connected = True

            display = get_status_display(status_info, v1_status, totals)

            border = BORDER_GUI.get(display["border256"], "#767676")
            outer.configure(highlightbackground=border)
            root.configure(bg=border)
            title_label.configure(
                text=" Strata speed  %s  %s " % (display["model_text"], base), fg=border)

            live = LIVE_GUI.get(display["live_color"], "black")

            for index, line in enumerate(display["body_lines"]):
                tk.Label(lines_frame, text=line, font=body_font, bg="white", anchor="w",
                         fg=live if index == 0 else "black").pack(fill="x")

            footer.configure(text=GUI_HINT % time.strftime("%H:%M:%S"))

        root.after(2000, tick)

    tick()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ----------------------------------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------------------------------

CHILD_ENV = "STRATA_STATUS_GUI_CHILD"


def spawn_detached_gui(server_port):
    import subprocess

    env = dict(os.environ)
    env[CHILD_ENV] = "1"

    options = {"env": env,
               "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL}

    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True

    subprocess.Popen([sys.executable, os.path.abspath(__file__), str(server_port)], **options)


def run_status(sc, server_port, no_gui=False):
    if no_gui or not is_available():
        show_status_panel(sc, server_port)
        return

    if os.environ.get(CHILD_ENV) == "1":
        show_status_window(sc, server_port)
        return

    spawn_detached_gui(server_port)
    sc.write_step("status window opened in the background - the command finished; close the window to quit")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Strata speed status panel")
    parser.add_argument("port", nargs="?", type=int, help="server port (default: from setup.json)")
    parser.add_argument("-NoGui", dest="no_gui", action="store_true",
                        help="force the console panel instead of the GUI window")
    args = parser.parse_args()

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    sc = load_cli()

    port = args.port

    if port is None:
        port = 8080
        status_setup = sc.read_json(sc.SETUP_PATH)

        if status_setup:
            status_port = sc.get_int_setting(status_setup.get("port"), 0)

            if status_port > 0:
                port = status_port

    run_status(sc, port, args.no_gui)


if __name__ == "__main__":
    main()
