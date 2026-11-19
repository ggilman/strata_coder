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
import math
import os
import shutil
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_PATH = os.path.join(HERE, "strata-coder.py")

# ----------------------------------------------------------------------------------------------------
# Panel text shared by the console and GUI panels
# ----------------------------------------------------------------------------------------------------

STATUS_RETRIES = 3
POLL_SECONDS = 2
POLL_MS = POLL_SECONDS * 1000
PANEL_TITLE = " Strata speed  %s  %s "
PANEL_HINT = " Ctrl+C quits - updates every %d s at %%s" % POLL_SECONDS

LIVE_ANSI = {"Green": "\033[32m", "Yellow": "\033[33m", "DarkGray": "\033[90m"}
LIVE_GUI = {"Green": "#1a9e3f", "Yellow": "#b58900", "DarkGray": "#8a8a8a"}
BORDER_GUI = {46: "#1a9e3f", 220: "#b58900", 241: "#767676"}
BAR_GUI = {"green": "#1a9e3f", "yellow": "#b58900", "red": "#c0392b"}
BAR_TRACK_GUI = "#e6e6e6"
BAR_BORDER_GUI = "#cfcfcf"
BAR_WIDTH = 16
BAR_UNI = ("\u2588", "\u2591")
BAR_ASC = ("#", "-")
GUI_HINT = "updates every %d s at %%s - r/F5 refresh, Esc/q close" % POLL_SECONDS
NO_SERVER_TEXT = "no Strata server is answering on port %%d - retrying every %d s" % POLL_SECONDS
LOST_TEXT = "lost the connection to the server (%%s) - retrying every %d s" % POLL_SECONDS
PREFIX_WIDTH = 10


def _num(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def clamp_pct(pct):
    return max(0.0, min(100.0, _num(pct, 0.0)))


def usage_pct(used, total):
    used_f = _num(used, 0.0)
    total_f = _num(total, 0.0)
    return clamp_pct((100.0 * used_f / total_f) if total_f > 0 else 0.0)


def make_usage_bar(used, total, unit="GiB", value_fmt="%.1f"):
    used_val = _int(used, 0) if value_fmt == "%d" else _num(used, 0.0)
    total_val = _int(total, 0) if value_fmt == "%d" else _num(total, 0.0)
    pct = usage_pct(used_val, total_val)
    unit_suffix = (" " + unit) if unit else ""
    text = "%s / %s%s (%.0f%%)" % (value_fmt % used_val, value_fmt % total_val, unit_suffix, pct)
    return {"used": used_val, "total": total_val, "pct": pct, "text": text}


def bar_glyphs():
    try:
        "".join(BAR_UNI).encode(sys.stdout.encoding or "ascii")
        return BAR_UNI
    except (UnicodeEncodeError, LookupError):
        return BAR_ASC


def format_bar(pct, text="", width=BAR_WIDTH, glyphs=None):
    full, empty = glyphs or bar_glyphs()
    width = max(1, _int(width, BAR_WIDTH))
    pct = clamp_pct(pct)
    filled = max(0, min(width, int(round((pct / 100.0) * width))))
    bar_str = "[%s%s]" % (full * filled, empty * (width - filled))
    return "%s %s" % (bar_str, text) if text else bar_str


def format_usage_bar(used, total, width=BAR_WIDTH, glyphs=None):
    bar = make_usage_bar(used, total)
    return format_bar(bar["pct"], bar["text"], width=width, glyphs=glyphs)


def bar_grade(pct):
    pct = clamp_pct(pct)
    if pct >= 90.0:
        return "red"
    if pct >= 75.0:
        return "yellow"
    return "green"


def load_cli():
    if not os.path.exists(CLI_PATH):
        raise SystemExit("strata-coder.py must sit next to this script: %s" % CLI_PATH)

    spec = importlib.util.spec_from_file_location("strata_coder_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------------------------------------
# Server discovery and polling shared by both panels
# ----------------------------------------------------------------------------------------------------

def resolve_server(sc, server_port):
    default_port = getattr(sc, "DEFAULT_PORT", 8080)
    server_port = _int(server_port, default_port)
    if server_port <= 0:
        server_port = default_port

    health = sc.strata_health(server_port)
    if health:
        return server_port, health

    try:
        processes = sc.strata_server_processes() or []
    except Exception:
        processes = []

    for process in processes:
        try:
            candidate_port = _int(sc.server_port(process), 0)
        except Exception:
            continue
        if candidate_port <= 0 or candidate_port == server_port:
            continue
        candidate_health = sc.strata_health(candidate_port)
        if candidate_health:
            return candidate_port, candidate_health

    if hasattr(sc, "isolated_strata_ports"):
        try:
            iso_ports = sc.isolated_strata_ports() or []
        except Exception:
            iso_ports = []
        for candidate_port in iso_ports:
            candidate_port = _int(candidate_port, 0)
            if candidate_port <= 0 or candidate_port == server_port:
                continue
            candidate_health = sc.strata_health(candidate_port)
            if candidate_health:
                return candidate_port, candidate_health

    if processes:
        try:
            first_port = _int(sc.server_port(processes[0]), server_port)
            if first_port > 0:
                server_port = first_port
        except Exception:
            pass

    return server_port, health


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _fetch_json_dict(url, timeout=5):
    request = urllib.request.Request(url)
    with _NO_PROXY_OPENER.open(request, timeout=timeout) as response:
        payload = json.load(response)
    return payload if isinstance(payload, dict) else {}


def poll_once(base):
    """Return ((status_info, v1_status, totals), None) or (None, error)."""
    try:
        status_info = _fetch_json_dict(base + "/status")
        v1_status = _fetch_json_dict(base + "/v1/status")
    except Exception as exc:
        return None, exc

    totals = None

    try:
        metrics = _fetch_json_dict(base + "/metrics")
        raw_totals = metrics.get("totals")
        if isinstance(raw_totals, dict):
            totals = raw_totals
    except Exception:
        pass

    return (status_info, v1_status, totals), None


def get_status_display(status_info, v1_status, totals, glyphs=None):
    status_info = status_info if isinstance(status_info, dict) else {}
    v1_status = v1_status if isinstance(v1_status, dict) else {}
    totals = totals if isinstance(totals, dict) else None

    timing = v1_status.get("last_timings")
    if not isinstance(timing, dict):
        timing = {}
    machine = v1_status.get("machine")
    if not isinstance(machine, dict):
        machine = {}
    activity = v1_status.get("activity")
    if not isinstance(activity, dict):
        activity = {}

    tok_per_s = _num(status_info.get("tokens_per_s"), 0.0)
    if status_info.get("busy") and tok_per_s > 0:
        live_text = "generating at %.1f tok/s" % tok_per_s
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

    phase = status_info.get("phase")
    phase_text = ("  phase: %s" % phase) if phase else ""

    body_lines = ["status:   %s%s" % (live_text, phase_text)]

    pred_tps = _num(timing.get("predicted_per_second"), 0.0)
    if timing and pred_tps > 0:
        body_lines.append(
            "answer:   %s tok at %.1f tok/s, cache %s"
            % (_int(timing.get("predicted_n"), 0), pred_tps, _int(timing.get("cache_n"), 0)))
        body_lines.append(
            "prompt:   %s tok at %.1f tok/s"
            % (_int(timing.get("prompt_n"), 0), _num(timing.get("prompt_per_second"), 0.0)))
    else:
        body_lines.append("answer:   none yet")
        body_lines.append("prompt:   none yet")

    out_tokens = _num(totals.get("output_tokens"), 0.0) if totals else 0.0
    decode_ms = _num(totals.get("decode_ms"), 0.0) if totals else 0.0
    if totals and out_tokens > 0 and decode_ms > 0:
        body_lines.append(
            "average:  %.1f tok/s over %s answers since start"
            % (1000.0 * out_tokens / decode_ms, _int(totals.get("requests"), 0)))
    else:
        body_lines.append("average:  none yet")

    context_bar = None
    ctx_total = _int(v1_status.get("cache_max_tokens"), 0)
    raw_prompt_tokens = status_info.get("prompt_tokens")
    if ctx_total > 0 and raw_prompt_tokens is not None:
        context_bar = make_usage_bar(max(0, _int(raw_prompt_tokens, 0)), ctx_total, unit="", value_fmt="%d")
        body_lines.append(
            "context:  %s" % format_bar(context_bar["pct"], context_bar["text"], glyphs=glyphs))
    else:
        body_lines.append("context:  unknown")

    gpu = machine.get("gpu")
    gpu_bar = None
    vram_bar = None

    if isinstance(gpu, dict) and gpu:
        gpu_pct = clamp_pct(gpu.get("util_pct"))
        gpu_text = "%.0f%% util, %.0f C, %.0f W" % (
            round(gpu_pct),
            round(_num(gpu.get("temp_c"), 0.0)),
            _num(gpu.get("power_w"), 0.0),
        )
        gpu_bar = {"pct": gpu_pct, "text": gpu_text}

        vram_used = _num(gpu.get("used_mib"), 0.0) / 1024.0
        vram_total = _num(gpu.get("total_mib"), 0.0) / 1024.0
        vram_bar = make_usage_bar(vram_used, vram_total)

        body_lines.append(
            "gpu:      %s" % format_bar(gpu_pct, gpu_text, glyphs=glyphs))
        body_lines.append(
            "vram:     %s" % format_bar(vram_bar["pct"], vram_bar["text"], glyphs=glyphs))
    else:
        body_lines.append("gpu:      n/a")
        body_lines.append("vram:     n/a")

    ram = machine.get("ram")
    ram_bar = None

    if isinstance(ram, dict) and ram:
        ram_used = _num(ram.get("used_gib"), 0.0)
        ram_total = _num(ram.get("total_gib"), 0.0)
        ram_bar = make_usage_bar(ram_used, ram_total)

        body_lines.append(
            "ram:      %s" % format_bar(ram_bar["pct"], ram_bar["text"], glyphs=glyphs))
    else:
        body_lines.append("ram:      n/a")

    body_lines.append(
        "requests: %s total, %s in flight, %s queued"
        % (_int(activity.get("requests"), 0),
           _int(activity.get("in_flight"), 0),
           _int(status_info.get("queued"), 0)))

    up_s = max(0.0, _num(v1_status.get("uptime_s"), 0.0))
    if up_s >= 3600:
        up_text = "%dh %dm" % (int(up_s // 3600), int((up_s % 3600) // 60))
    else:
        up_text = "%dm" % int(up_s // 60)

    mtp_text = ""
    draft_n = _num(timing.get("draft_n"), 0.0)
    if draft_n > 0:
        mtp_pct = clamp_pct(100.0 * _num(timing.get("draft_n_accepted"), 0.0) / draft_n)
        mtp_text = "  MTP accept %.0f%%" % mtp_pct

    engine_name = v1_status.get("engine") or "unknown"
    body_lines.append("uptime:   %s  engine %s%s"
                      % (up_text, engine_name, mtp_text))

    return {
        "body_lines": body_lines,
        "live_color": live_color,
        "border256": border256,
        "model_text": v1_status.get("model") or "model",
        "context_bar": context_bar,
        "gpu_bar": gpu_bar,
        "vram_bar": vram_bar,
        "ram_bar": ram_bar,
    }


# ----------------------------------------------------------------------------------------------------
# Console panel
# ----------------------------------------------------------------------------------------------------

def _terminal_dimensions():
    try:
        size = shutil.get_terminal_size()
        return max(40, min(78, size.columns - 1)), max(1, size.lines)
    except Exception:
        return 78, 24


def show_status_panel(sc, server_port):
    server_port, health = resolve_server(sc, server_port)

    if not health:
        sc.fail("no Strata server is answering on port %d." % server_port)

    base = "http://127.0.0.1:%d" % server_port

    interactive = sys.stdout.isatty()
    width, term_lines = _terminal_dimensions()

    panel_top_drawn = False
    previous_lines = 0
    previous_size = (width, term_lines)
    failures = 0

    if interactive:
        sys.stdout.write("\033[?25l\033[2J\033[H")
        sys.stdout.flush()

    try:
        while True:
            data, exc = poll_once(base)

            if data is None:
                new_port, new_health = resolve_server(sc, server_port)
                if new_health and new_port != server_port:
                    server_port = new_port
                    base = "http://127.0.0.1:%d" % server_port
                    data, exc = poll_once(base)

            if data is None:
                failures += 1

                if failures >= STATUS_RETRIES:
                    if interactive:
                        sys.stdout.write("\033[?25h")
                        sys.stdout.flush()
                    sc.fail("lost the connection to the server (%s)" % exc)

                time.sleep(POLL_SECONDS)
                continue

            failures = 0
            status_info, v1_status, totals = data

            if interactive:
                width, term_lines = _terminal_dimensions()

            display = get_status_display(status_info, v1_status, totals)
            body_lines = list(display["body_lines"])

            rows = []

            for index, line in enumerate(body_lines):
                color = LIVE_ANSI.get(display["live_color"]) if (index == 0 and sc.COLOR_ON) else None
                rows.append((line, color))

            rows.append(None)
            rows.append((PANEL_HINT % time.strftime("%H:%M:%S"),
                         sc.DARKGRAY if sc.COLOR_ON else None))

            block = "\n".join(sc.build_box(
                PANEL_TITLE % (display["model_text"], base),
                rows, width, "\033[38;5;%sm" % display["border256"]))

            if not block.endswith("\n"):
                block += "\n"

            current_size = (width, term_lines)
            if interactive and panel_top_drawn:
                if current_size != previous_size or previous_lines >= term_lines:
                    sys.stdout.write("\033[2J\033[H")
                else:
                    sys.stdout.write("\r\033[%dA" % previous_lines)
                sys.stdout.write(block)
                sys.stdout.write("\033[0J")
            else:
                sys.stdout.write(block)
            sys.stdout.flush()

            panel_top_drawn = True
            previous_lines = block.count("\n")
            previous_size = current_size

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        sys.stdout.flush()
        sys.exit(0)
    finally:
        if interactive:
            try:
                sys.stdout.write("\033[?25h")
                sys.stdout.flush()
            except Exception:
                pass


# ----------------------------------------------------------------------------------------------------
# GUI panel
# ----------------------------------------------------------------------------------------------------

def show_status_window(sc, server_port):
    import tkinter as tk

    default_port = getattr(sc, "DEFAULT_PORT", 8080)
    server_port = _int(server_port, default_port)
    if server_port <= 0:
        server_port = default_port
    base = "http://127.0.0.1:%d" % server_port

    root = tk.Tk()
    root.title("Strata speed")
    root.configure(bg="#767676")
    root.minsize(490, 275)

    outer = tk.Frame(root, bg="white", highlightthickness=2, highlightbackground="#767676")
    outer.pack(fill="both", expand=True, padx=10, pady=10)

    if sc.IS_WINDOWS:
        body_font = ("Consolas", 11)
        title_font = ("Segoe UI", 11, "bold")
        footer_font = ("Segoe UI", 9)
    else:
        body_font = ("Monospace", 11)
        title_font = ("Sans", 11, "bold")
        footer_font = ("Sans", 9)

    title_label = tk.Label(outer, text="Strata speed  %s" % base,
                           font=title_font, bg="white", fg="#767676", anchor="w")
    title_label.pack(fill="x", padx=14, pady=(10, 4))

    header_rule = tk.Frame(outer, bg="#ebebeb", height=1)
    header_rule.pack(fill="x", padx=14, pady=(0, 4))

    lines_frame = tk.Frame(outer, bg="white")
    lines_frame.pack(fill="both", expand=True, padx=14, pady=(2, 4))

    footer_rule = tk.Frame(outer, bg="#ebebeb", height=1)
    footer_rule.pack(fill="x", padx=14, pady=(4, 0))

    footer = tk.Label(outer, text="", font=footer_font,
                      bg="white", fg="#8a8a8a", anchor="w")
    footer.pack(fill="x", padx=14, pady=(6, 10))

    ever_connected = False
    bar_w, bar_h = 140, 12
    row_slots = []
    chrome_state = {"border": None, "title": None, "footer": None}
    closing = False
    after_ids = {"start": None, "check": None}

    def get_row_slot(index):
        while len(row_slots) <= index:
            frame = tk.Frame(lines_frame, bg="white")
            prefix_label = tk.Label(frame, text="", font=body_font, bg="white",
                                    fg="black", anchor="w", width=PREFIX_WIDTH)
            prefix_label.pack(side="left")
            canvas = tk.Canvas(
                frame, width=bar_w, height=bar_h, bg=BAR_TRACK_GUI,
                highlightthickness=1, highlightbackground=BAR_BORDER_GUI, bd=0)
            rect_id = canvas.create_rectangle(0, 0, 0, bar_h, fill=BAR_GUI["green"],
                                              outline="", state="hidden")
            text_label = tk.Label(frame, text="", font=body_font, bg="white",
                                  fg="black", anchor="w")
            row_slots.append({
                "frame": frame,
                "prefix_label": prefix_label,
                "canvas": canvas,
                "rect_id": rect_id,
                "text_label": text_label,
                "visible": False,
                "mode": None,
                "prefix_width": PREFIX_WIDTH,
                "prefix_text": None,
                "prefix_fg": None,
                "fill_w": None,
                "fill_color": None,
                "bar_text": None,
                "text_fg": None,
            })
        return row_slots[index]

    def set_row_text(index, text, fg="black"):
        slot = get_row_slot(index)
        if not slot["visible"]:
            slot["frame"].pack(fill="x", pady=1)
            slot["visible"] = True

        has_prefix = len(text) >= PREFIX_WIDTH and ":" in text[:PREFIX_WIDTH]
        if has_prefix:
            prefix = text[:PREFIX_WIDTH]
            detail = text[PREFIX_WIDTH:]
            target_mode = "kv_text"
            target_width = PREFIX_WIDTH
        else:
            prefix = text
            detail = ""
            target_mode = "full_text"
            target_width = 0

        if slot["mode"] != target_mode:
            slot["canvas"].pack_forget()
            slot["text_label"].pack_forget()
            if target_mode == "kv_text":
                slot["text_label"].pack(side="left", padx=(2, 0))
            slot["mode"] = target_mode

        if slot["prefix_width"] != target_width:
            slot["prefix_label"].configure(width=target_width)
            slot["prefix_width"] = target_width

        if slot["prefix_text"] != prefix:
            slot["prefix_label"].configure(text=prefix)
            slot["prefix_text"] = prefix

        if slot["prefix_fg"] != fg:
            slot["prefix_label"].configure(fg=fg)
            slot["prefix_fg"] = fg

        if target_mode == "kv_text":
            if slot["bar_text"] != detail:
                slot["text_label"].configure(text=detail)
                slot["bar_text"] = detail
            if slot["text_fg"] != fg:
                slot["text_label"].configure(fg=fg)
                slot["text_fg"] = fg

    def set_row_bar(index, prefix, bar):
        slot = get_row_slot(index)
        if not slot["visible"]:
            slot["frame"].pack(fill="x", pady=1)
            slot["visible"] = True
        if slot["mode"] != "bar":
            slot["canvas"].pack_forget()
            slot["text_label"].pack_forget()
            slot["canvas"].pack(side="left", padx=(2, 8))
            slot["text_label"].pack(side="left")
            slot["mode"] = "bar"
        if slot["prefix_width"] != PREFIX_WIDTH:
            slot["prefix_label"].configure(width=PREFIX_WIDTH)
            slot["prefix_width"] = PREFIX_WIDTH
        if slot["prefix_text"] != prefix:
            slot["prefix_label"].configure(text=prefix)
            slot["prefix_text"] = prefix
        if slot["prefix_fg"] != "black":
            slot["prefix_label"].configure(fg="black")
            slot["prefix_fg"] = "black"
        if slot["text_fg"] != "black":
            slot["text_label"].configure(fg="black")
            slot["text_fg"] = "black"

        pct = clamp_pct(bar.get("pct"))
        fill_w = max(0, min(bar_w, int(round((pct / 100.0) * bar_w))))
        fill_color = BAR_GUI[bar_grade(pct)]
        if slot["fill_w"] != fill_w or slot["fill_color"] != fill_color:
            if fill_w > 0:
                slot["canvas"].coords(slot["rect_id"], 0, 0, fill_w, bar_h)
                slot["canvas"].itemconfigure(slot["rect_id"], fill=fill_color, state="normal")
            else:
                slot["canvas"].itemconfigure(slot["rect_id"], state="hidden")
            slot["fill_w"] = fill_w
            slot["fill_color"] = fill_color

        bar_text = bar.get("text") or (
            make_usage_bar(bar["used"], bar["total"])["text"]
            if "used" in bar else "%.0f%% util" % pct)
        if slot["bar_text"] != bar_text:
            slot["text_label"].configure(text=bar_text)
            slot["bar_text"] = bar_text

    def hide_extra_rows(used_count):
        for slot in row_slots[used_count:]:
            if slot["visible"]:
                slot["frame"].pack_forget()
                slot["visible"] = False

    def render_poll_result(data, exc):
        nonlocal ever_connected

        if closing:
            return

        try:
            if data is None:
                border = "#767676"
                if chrome_state["border"] != border:
                    outer.configure(highlightbackground=border)
                    root.configure(bg=border)
                    chrome_state["border"] = border

                title_text = "Strata speed  %s" % base
                if chrome_state["title"] != (title_text, border):
                    title_label.configure(text=title_text, fg=border)
                    chrome_state["title"] = (title_text, border)

                message = NO_SERVER_TEXT % server_port if not ever_connected else LOST_TEXT % exc
                set_row_text(0, message, fg="#c0392b")
                hide_extra_rows(1)
            else:
                status_info, v1_status, totals = data
                ever_connected = True

                display = get_status_display(status_info, v1_status, totals)

                border = BORDER_GUI.get(display["border256"], "#767676")
                if chrome_state["border"] != border:
                    outer.configure(highlightbackground=border)
                    root.configure(bg=border)
                    chrome_state["border"] = border

                title_text = "Strata speed  %s  %s" % (display["model_text"], base)
                if chrome_state["title"] != (title_text, border):
                    title_label.configure(text=title_text, fg=border)
                    chrome_state["title"] = (title_text, border)

                live = LIVE_GUI.get(display["live_color"], "black")

                bars_by_prefix = {
                    "context:": display.get("context_bar"),
                    "gpu:": display.get("gpu_bar"),
                    "vram:": display.get("vram_bar"),
                    "ram:": display.get("ram_bar"),
                }

                body_lines = display["body_lines"]
                for index, line in enumerate(body_lines):
                    bar = None
                    for prefix_key, candidate in bars_by_prefix.items():
                        if line.startswith(prefix_key) and candidate is not None:
                            bar = candidate
                            break

                    if bar is not None:
                        set_row_bar(index, line[:PREFIX_WIDTH], bar)
                    else:
                        set_row_text(index, line, fg=live if index == 0 else "black")

                hide_extra_rows(len(body_lines))

            footer_text = GUI_HINT % time.strftime("%H:%M:%S")
            if chrome_state["footer"] != footer_text:
                footer.configure(text=footer_text)
                chrome_state["footer"] = footer_text

            root.update_idletasks()
        except tk.TclError:
            close_window()

    latest_poll = {
        "ready": False,
        "data": None,
        "exc": None,
        "in_flight": False,
        "refresh_requested": False,
        "port": server_port,
        "base": base,
    }
    poll_lock = threading.Lock()

    def poll_worker():
        with poll_lock:
            current_port = latest_poll["port"]
            current_base = latest_poll["base"]
        try:
            data, error = poll_once(current_base)
            if data is None:
                new_port, new_health = resolve_server(sc, current_port)
                if new_port != current_port and new_health:
                    current_port = new_port
                    current_base = "http://127.0.0.1:%d" % current_port
                    data, error = poll_once(current_base)
        except BaseException as exc:
            data, error = None, exc
        with poll_lock:
            latest_poll["port"] = current_port
            latest_poll["base"] = current_base
            latest_poll["data"] = data
            latest_poll["exc"] = error
            latest_poll["ready"] = True
            latest_poll["in_flight"] = False

    def cancel_after(key):
        timer_id = after_ids.get(key)
        if timer_id is not None:
            after_ids[key] = None
            try:
                root.after_cancel(timer_id)
            except Exception:
                pass

    def schedule_start_poll(delay_ms=POLL_MS):
        if closing:
            return
        cancel_after("start")
        try:
            after_ids["start"] = root.after(delay_ms, start_poll)
        except Exception:
            pass

    def start_poll():
        after_ids["start"] = None
        if closing:
            return
        with poll_lock:
            if latest_poll["in_flight"]:
                return
            latest_poll["in_flight"] = True
            latest_poll["refresh_requested"] = False
        try:
            threading.Thread(target=poll_worker, daemon=True).start()
        except BaseException:
            with poll_lock:
                latest_poll["in_flight"] = False
            schedule_start_poll(POLL_MS)

    def check_poll():
        nonlocal server_port, base
        after_ids["check"] = None
        if closing:
            return
        ready = False
        refresh_now = False
        with poll_lock:
            if latest_poll["ready"]:
                data = latest_poll["data"]
                exc = latest_poll["exc"]
                server_port = latest_poll["port"]
                base = latest_poll["base"]
                latest_poll["ready"] = False
                ready = True
                refresh_now = latest_poll["refresh_requested"]
                latest_poll["refresh_requested"] = False
        if ready:
            render_poll_result(data, exc)
            if not closing:
                schedule_start_poll(0 if refresh_now else POLL_MS)
        if not closing:
            try:
                after_ids["check"] = root.after(100, check_poll)
            except Exception:
                pass

    def trigger_refresh(_event=None):
        if closing:
            return
        cancel_after("start")
        with poll_lock:
            if latest_poll["in_flight"]:
                latest_poll["refresh_requested"] = True
                return
        start_poll()

    def close_window(_event=None):
        nonlocal closing
        if closing:
            return
        closing = True
        cancel_after("start")
        cancel_after("check")
        try:
            root.destroy()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", close_window)
    root.bind("<Escape>", close_window)
    root.bind("<KeyPress-q>", close_window)
    root.bind("<KeyPress-Q>", close_window)
    root.bind("<KeyPress-r>", trigger_refresh)
    root.bind("<KeyPress-R>", trigger_refresh)
    root.bind("<F5>", trigger_refresh)

    set_row_text(0, "connecting to %s..." % base, fg="#767676")
    hide_extra_rows(1)
    start_poll()
    try:
        after_ids["check"] = root.after(100, check_poll)
    except Exception:
        pass

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        close_window()


# ----------------------------------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------------------------------

CHILD_ENV = "STRATA_STATUS_GUI_CHILD"


def run_status(sc, server_port, no_gui=False):
    if no_gui or not sc.is_available():
        show_status_panel(sc, server_port)
        return

    if os.environ.get(CHILD_ENV) == "1":
        show_status_window(sc, server_port)
        return

    sc.spawn_detached_gui(os.path.abspath(__file__), {CHILD_ENV: "1"}, [str(server_port)])
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

    port = args.port if args.port is not None else sc.saved_setup_port()

    run_status(sc, port, args.no_gui)


if __name__ == "__main__":
    main()
