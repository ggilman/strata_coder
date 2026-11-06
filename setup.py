#!/usr/bin/env python3
"""setup.py - the Strata Coder setup menu, console and GUI.

All menu text (questions, option labels, hints) lives in this file.
strata-coder.py imports it for its -Setup console menu, and -Setup opens the
GUI from here automatically when the machine can display one (force the
console menu with -NoGui). Running this file directly opens the GUI right
away:

    python setup.py

Pressing OK starts strata-coder.py -Setup with the chosen parameters, which
saves them and installs and configures everything. The scode shortcut and the
Delete Strata engine buttons are actions, not options: their buttons take
effect the moment they are pressed.
"""

import ctypes
import importlib.util
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLI_PATH = os.path.join(HERE, "strata-coder.py")

# ----------------------------------------------------------------------------------------------------
# Menu text shared by the console and GUI menus
# ----------------------------------------------------------------------------------------------------

QUESTIONS = {
    "variant": "Variant",
    "dataDir": "Model folder",
    "context": "Context",
    "kv": "KV cache",
    "port": "Server port",
    "isolation": "Isolation - keep OpenCode away from the internet?",
    "alias": "Add the scode shortcut to %s?",
}

STEP_TEXT = ("Asking for setup parameters (arrow keys or digits move, Enter picks, "
             "* default is pre-selected)")

TEXT_HINT = " Enter keeps the default, or type the number or the name"
PICK_HINT = " up/down move, Enter picks, digits jump, Esc keeps the default"
PICKED_FMT = " picked %s"
KEEP_DEFAULT = " keeping the default"

PICK_INPUT = "  pick 1-%d (Enter keeps %s): "
YESNO_INPUT = "  y or n (Enter keeps %s): "
PATH_INPUT = " type a new path, or press Enter to keep the default: "
PORT_INPUT = " type a port, or press Enter to keep the default: "

CONTEXT_CHOICES = [4096, 8192, 16384, 32768, 65536, 131072, 204800, 262144, 393216, 524288]
TRAINED_CONTEXT = 262144
KV_CHOICES = ["int8", "k8v4", "q4_0"]
KV_LABELS = {"int8": "int8 (8-bit, default)", "k8v4": "k8v4 (8-bit K, 4-bit V)",
             "q4_0": "q4_0 (4-bit, smallest)"}


def context_label(tokens):
    if tokens == 0:
        return "0 (Strata recommendation)"

    label = "%dk (%d tokens)" % (tokens // 1024, tokens)

    if tokens > TRAINED_CONTEXT:
        label += " (past trained, YaRN)"

    return label


def context_options():
    options = [{"value": 0, "name": "0", "label": context_label(0)}]

    for tokens in CONTEXT_CHOICES:
        options.append({"value": tokens, "name": "%dk" % (tokens // 1024),
                        "label": context_label(tokens)})

    return options


def kv_options():
    return [{"value": k, "name": k, "label": KV_LABELS[k]} for k in KV_CHOICES]


GRADE_LETTERS = {"green": "+", "yellow": "!", "red": "x"}
GUI_GRADE_COLORS = {"green": "#0a7d28", "yellow": "#a06b00", "red": "#c00000"}

FASTEST_ICON = "\u26a1"
QUALITY_STARS = {"best": "\u2605\u2605\u2605", "above IQ3_S": "\u2605\u2605\u2605",
                 "great": "\u2605\u2605", "better": "\u2605\u2605",
                 "good": "\u2605", "code only": "\u2605"}


def quality_stars(variant, star="\u2605"):
    return QUALITY_STARS.get(variant.get("quality_word"), "").replace("\u2605", star)


def variant_label(variant, grade="", fastest=False, star="\u2605",
                  fastest_icon=FASTEST_ICON):
    """The one-line label of a variant: '+ Qwen3.8-Flash-Next IQ3_S (60 GB, ~53 tok/s) \u2605\u2605\u2605'.
    grade and fastest come from the CLI (grade_variant() plus the fastest variant this PC can
    run); everything the short label drops - the full title, the grade reason, the best marker -
    lives in the info popup (GUI) or the advice line (console)."""
    facts = "%d GB" % variant["min_ram_gb"]

    if variant.get("speed_tok_s"):
        facts += ", ~%d tok/s%s" % (variant["speed_tok_s"],
                                    " " + fastest_icon if fastest else "")

    text = "%s (%s) %s" % (short_name(variant), facts, quality_stars(variant, star))

    if grade in GRADE_LETTERS:
        text = "%s %s" % (GRADE_LETTERS[grade], text)

    return text


def grade_legend(star="\u2605", fastest_icon=FASTEST_ICON):
    return ["+ full speed   ! runs with a cost   x not enough RAM here",
            "quality: %s good   %s%s better   %s%s%s best    %s = the fastest this PC can run"
            % (star, star, star, star, star, star, fastest_icon),
            "tok/s = answers per second (60+ is faster than reading)"]


def scode_info_text():
    return "\n".join([
        "The scode shortcut",
        "",
        "Adds a command named scode so Strata Coder starts from any shell window by just typing "
        "scode - no paths to remember. It runs this same script, so scode -Status, scode -Stop "
        "and scode -Setup work from anywhere.",
        "",
        "Where the shortcut lands depends on the shell chosen next to this button:",
        "  PowerShell:  a scode function in your current-user profile file.",
        "  cmd:         a DOSKEY macro file in %USERPROFILE%\\bin, loaded by a Command Processor "
        "AutoRun entry (any AutoRun value you already have is kept, not replaced).",
        "  bash / zsh:  a marked scode function block in ~/.bashrc, ~/.bash_profile or ~/.zshrc.",
        "",
        "auto picks the shell that opened this setup window. Pressing the button again just "
        "rewrites the block, so it also repairs the shortcut if this script is ever moved. "
        "Close and reopen a shell window before scode works.",
    ])


def engine_info_text():
    return "\n".join([
        "Delete the Strata engine",
        "",
        "Removes the downloaded Strata engine program (strata.exe on Windows, strata on Linux) "
        "from the engine folder and the model folder. The downloaded model files are NOT touched.",
        "",
        "Use it when Windows Smart App Control or your antivirus blocked or quarantined the "
        "engine, when the engine misbehaves and a clean copy should replace it, or when you are "
        "about to run offline isolation, which deletes and rebuilds it itself.",
        "",
        "The next -Setup downloads the engine again, so that part needs network.",
    ])


def context_info_text():
    return "\n".join([
        "Context size",
        "",
        "How much the model can keep in mind at once: the conversation so far, opened files, "
        "tool results. A token is roughly four characters of English, so 32K tokens is a few "
        "hundred pages.",
        "",
        "0 lets Strata pick its recommendation for this model and PC.",
        "",
        "A bigger context costs two things:",
        "  - RAM: the KV cache reserves memory for the whole context and grows in a straight "
        "line with it - at 128K and above that is tens of GB on top of the model. The RAM "
        "grading on this screen counts it; if it comes up tight, lower the context or step "
        "the KV cache down.",
        "  - time: a long prompt must first be read (a 32K prompt takes about 10-20 seconds "
        "depending on the variant); the speed of answers afterwards does not change.",
        "",
        "The models were trained on 256K tokens. Past that Strata stretches them with YaRN: "
        "it works, but very long contexts answer weaker. Most coding work fits comfortably "
        "in 64K-128K.",
    ])


def kv_info_text():
    return "\n".join([
        "KV cache quantization",
        "",
        "The KV cache is the memory holding every token the model has already read. This "
        "setting chooses how precisely it is kept: less precision means a smaller cache - the "
        "same context in less RAM - at the price of a little answer quality.",
        "",
        "  int8:  full precision. The default; pick it while RAM has room.",
        "  k8v4:  keeps the cache about a quarter smaller; the quality change is rarely "
        "noticeable.",
        "  q4_0:  about half the cache, the biggest saving; long or very detailed answers can "
        "degrade slightly.",
        "",
        "Rule of thumb: stay on int8; step down a notch when a large context no longer fits, "
        "before you shrink the context itself.",
    ])


def isolation_info(which):
    """the popup text for one isolation choice, shown by the GUI's per-option info buttons."""
    texts = {
        "none": "\n".join([
            "Isolation: None",
            "",
            "OpenCode runs directly on this PC with the normal internet access of your user "
            "account. Nothing is blocked.",
            "",
            "Fine for everyday work; pick Local Only or a container when the code or data on "
            "this machine must not leave it.",
        ]),
        "local": "\n".join([
            "Isolation: Local Only",
            "",
            "OpenCode keeps running on this PC, but nothing leaves it. While OpenCode runs:",
            "  - OpenCode and every process it starts get a dead proxy (http://127.0.0.1:9), "
            "so any web request dies instantly;",
            "  - OpenCode's webfetch and websearch tools are switched off;",
            "  - its bash gets a deny list for network tools: curl, wget, ssh, git push/pull/"
            "clone, npm, pip, uv, conda, winget installs and friends. All other commands keep "
            "working.",
            "",
            "Local traffic stays exempt (NO_PROXY covers 127.0.0.1 and localhost), so OpenCode "
            "still reaches the model server on this PC. Everything is undone the moment "
            "OpenCode exits; no permanent machine changes are made.",
            "",
            "This is a policy wall, not a jail: a very determined exploit could still step "
            "around it. The Docker and Podman choices are the real jail.",
        ]),
        "docker": "\n".join([
            "Isolation: Docker",
            "",
            "The stronger wall: OpenCode itself runs inside a Docker container instead of on "
            "this PC.",
            "  - the container sits on an internal Docker network with no route to the "
            "internet;",
            "  - it reaches the model only through a small relay on that network, and the "
            "model still runs on this PC;",
            "  - only the project folder you open is mounted inside the container - the rest "
            "of this PC is invisible to it;",
            "  - inside the container the same dead proxy and network-tool deny list as "
            "Local Only apply.",
            "",
            "Even a misbehaving or compromised OpenCode cannot reach your other files or your "
            "network. Needs Docker Desktop or Docker Engine; the sandbox image is built on "
            "first use, and OpenCode's history is kept in a named volume that survives "
            "restarts.",
        ]),
        "podman": "\n".join([
            "Isolation: Podman",
            "",
            "The same container wall as Docker, driven by the Podman engine instead: same "
            "internal network, same image, same relay, same deny lists. Pick it when Podman "
            "is already installed or Docker Desktop's licensing is a problem.",
            "",
            "Needs Podman: on Windows that means a podman machine (run 'podman machine "
            "start' before using it); on Linux the normal podman install works out of the "
            "box. The sandbox image builds on first use.",
        ]),
        "wsl": "\n".join([
            "Isolation: WSL Containers",
            "",
            "The same container wall as Docker and Podman, but run by systemd-nspawn (part of "
            "systemd) inside a WSL 2 Linux distro - no Docker, no Podman, no container engine "
            "at all.",
            "",
            "  - the container gets no network interfaces whatsoever (only its own loopback): "
            "no routes, no DNS, nothing to firewall;",
            "  - the model keeps running on Windows; the container reaches it through a small "
            "relay inside WSL, using WSL's mirrored networking (shared Windows loopback);",
            "  - the same Debian dev toolchain and network deny lists as the Docker sandbox; "
            "your project folder is mounted inside, and OpenCode's history persists in a WSL "
            "directory.",
            "",
            "Needs Windows plus WSL 2 with systemd in the distro (the stock Ubuntu distro "
            "qualifies). The script installs a few standard distro packages inside it "
            "(systemd-container, debootstrap) and builds the sandbox rootfs on first use. If "
            "your WSL is not in mirrored networking mode yet, add [wsl2] and "
            "networkingMode=mirrored to %UserProfile%\\.wslconfig and run wsl --shutdown.",
        ]),
    }

    return texts.get(which, "")


def isolation_options():
    """the console setup's isolation options; WSL Containers is only offered on Windows,
    where WSL exists."""
    options = [{"value": "none", "name": "None", "label": "None - nothing extra"},
               {"value": "local", "name": "Local", "label": "Local Only - block web access for "
                                                            "OpenCode on this PC"},
               {"value": "docker", "name": "Docker", "label": "Docker - OpenCode in a container "
                                                              "with no internet"},
               {"value": "podman", "name": "Podman", "label": "Podman - the same container wall, "
                                                              "Podman engine"}]

    if os.name == "nt":
        options.append({"value": "wsl", "name": "WSL",
                        "label": "WSL Containers - the same container inside WSL 2, "
                                 "no container engine"})

    return options


def speed_nudge(variant, peers, ram_gb):
    """plain words: what a smaller size of the same family would buy in speed."""
    speed = variant.get("speed_tok_s")

    if not speed:
        return ""

    steps = sorted(
        (s for s in peers
         if s is not variant and s["family"] == variant["family"] and ram_gb >= s["min_ram_gb"]
         and s.get("speed_tok_s", 0) > speed),
        key=lambda s: s["speed_tok_s"])[:2]

    if not steps:
        return ""

    parts = []

    for s in steps:
        freed = int(round(variant["arena_gb"] - s["arena_gb"]))
        parts.append("%s is %+d%%%s"
                     % (s["model"], round(100.0 * (s["speed_tok_s"] / speed - 1)),
                        " (frees %d GB RAM)" % freed if freed >= 3 else ""))

    return ("  Faster in this family: %s - each step is one down in quality."
            % ", ".join(parts))


def short_name(variant):
    """the variant's name without its trailing parenthetical: 'Qwen3.8-Flash-Next (Unsloth) UD-IQ4_XS'."""
    return variant["title"].rsplit(" (", 1)[0]


def variant_info_text(sc, variant, grade, note, ram_gb, is_best, fastest, peers):
    """the full details behind one variant, shown in the GUI's info popup."""
    lines = [variant["title"], ""]

    if grade == "green":
        lines.append("Runs at full speed on this PC.")
    elif grade == "yellow":
        lines.append("Runs here, but %s." % note)
    else:
        lines.append("Will NOT run: this PC does not have the RAM it needs.")

    if is_best:
        lines[-1] += "  It is the best this PC can run."

    if variant.get("speed_tok_s"):
        lines.append("")
        lines.append("Answers ~%d tokens per second (60+ is faster than reading); a 32K-token "
                     "prompt reads in about %d seconds."
                     % (variant["speed_tok_s"],
                        max(1, int(round(32768.0 / (variant.get("prompt_tok_s") or 1600))))))

        if fastest:
            lines.append("This is the fastest variant this PC can run.")
    elif variant.get("budget"):
        lines.append("")

        if ram_gb < sc.SSD_BUDGET_RAM_GB:
            lines.append("Measured about 8 tokens per second on a 64 GB PC (IQ3_S: ~53): it keeps "
                         "only part of its experts in RAM and reads the rest from the SSD; about "
                         "%d GB of RAM is needed to hold all of them." % sc.SSD_BUDGET_RAM_GB)
        else:
            lines.append("No published speed yet (close to IQ3_S when all its experts fit in "
                         "RAM); Strata marks this quant experimental.")

    lines.append("")
    lines.append("Quality: %s. More stars answer better but run slower; fewer stars run faster."
                 % variant.get("quality_word", ""))
    lines.append("Model files: about %s GB to download; uses about %d GB of RAM while running."
                 % (variant["download_gb"], variant["arena_gb"]))
    lines.append("Needs about %d GB RAM; this PC has %s GB."
                 % (variant["min_ram_gb"], ram_gb))
    lines.append("Trained context: 262144 tokens (256k); beyond that Strata stretches it with "
                 "YaRN and answers get weaker.")

    nudge = speed_nudge(variant, peers, ram_gb).strip()

    if nudge:
        lines.append("")
        lines.append(nudge)

    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------------------------------------

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


def run_setup_gui(sc, overrides=None):
    """Show the setup window and return the chosen settings dict, or None on Cancel.

    overrides carries parameters already given on the command line (dataDir,
    context, kv, port, isolation, and variant as a (family, model) pair) which
    pre-fill the form over the saved setup.json values. The scode shortcut is
    not part of the result: its button acts the moment it is pressed."""

    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    overrides = overrides or {}
    saved = sc.read_json(sc.SETUP_PATH) or {}
    ram_gb = sc._ram_gb()

    default_data_dir = overrides.get("dataDir") or ""

    if not default_data_dir and saved.get("dataDir"):
        default_data_dir = saved["dataDir"]

    if not default_data_dir:
        prior = sc.read_json(sc.STRATA_SETTINGS_PATH)

        if prior and prior.get("data_dir") and os.path.exists(prior["data_dir"]):
            default_data_dir = prior["data_dir"]

    if not default_data_dir:
        default_data_dir = os.path.join(os.path.expanduser("~"), "ai-models", "models")

    default_context = overrides.get("context")

    if default_context is None:
        default_context = sc.get_int_setting(saved.get("context"), 0)

    default_kv = overrides.get("kv") or str(saved.get("kv") or "int8")
    default_port = overrides.get("port") or sc.get_int_setting(saved.get("port"), 8080) or 8080

    default_isolation = sc.saved_isolation(saved)

    if "isolation" in overrides:
        default_isolation = overrides["isolation"]

    if default_isolation == "wsl" and os.name != "nt":
        default_isolation = "none"

    default_variant = None

    if "variant" in overrides:
        family, model = overrides["variant"]
        candidates = [v for v in sc.VARIANTS
                      if (not family or v["family"] == family)
                      and (not model or v["model"] == model)]

        if candidates:
            default_variant = sc.pick_best_variant(candidates, ram_gb) or candidates[0]

    if not default_variant and saved.get("family") and saved.get("model"):
        default_variant = next(
            (v for v in sc.VARIANTS
             if v["family"] == saved["family"] and v["model"] == saved["model"]),
            None)

    if not default_variant:
        default_variant = sc.pick_best_variant(sc.VARIANTS, ram_gb) or sc.VARIANTS[0]

    context_values = {}
    context_labels = []

    for tokens in [0] + CONTEXT_CHOICES:
        label = context_label(tokens)
        context_values[label] = tokens
        context_labels.append(label)

    if default_context not in context_values.values():
        label = "%d (saved custom value)" % default_context
        context_values[label] = default_context
        context_labels.insert(0, label)

    default_context_label = next(
        (l for l in context_labels if context_values[l] == default_context), context_labels[0])

    kv_values = {}
    kv_labels = []

    for k in KV_CHOICES:
        kv_values[KV_LABELS[k]] = k
        kv_labels.append(KV_LABELS[k])

    if default_kv not in kv_values.values():
        label = "%s (saved custom value)" % default_kv
        kv_values[label] = default_kv
        kv_labels.insert(0, label)

    default_kv_label = next((l for l in kv_labels if kv_values[l] == default_kv), kv_labels[0])

    variant_rows = sorted(sc.VARIANTS, key=lambda v: -v["quality"])
    best_variant = sc.pick_best_variant(variant_rows, ram_gb)
    grades = {i: sc.grade_variant(v, ram_gb) for i, v in enumerate(variant_rows)}
    default_index = next((i for i, v in enumerate(variant_rows) if v is default_variant), 0)

    fitting = [v for v in variant_rows if ram_gb >= v["min_ram_gb"] and v.get("speed_tok_s")]
    fastest_variant = max(fitting, key=lambda v: v["speed_tok_s"]) if fitting else None

    alias_shells = ("auto", "cmd", "powershell") if sc.IS_WINDOWS else ("auto", "bash", "zsh")
    shell_names = {"cmd": "Command Prompt", "powershell": "PowerShell",
                   "bash": "Bash", "zsh": "zsh"}

    # ------------------------------------------------------------------------------------------------

    root = tk.Tk()
    root.title("Strata Coder setup - %s" % ("Windows" if sc.IS_WINDOWS else "Linux"))

    body = ttk.Frame(root, padding=12)
    body.grid(sticky="nsew")

    fits_any = ram_gb >= min(v["min_ram_gb"] for v in sc.VARIANTS)
    info_text = "This PC has %s GB RAM." % ram_gb

    if best_variant:
        info_text += "  Best it can run: %s%s." % (
            short_name(best_variant),
            " (~%d tok/s)" % best_variant["speed_tok_s"]
            if best_variant.get("speed_tok_s") else "")

    if not fits_any:
        info_text += "  WARNING: not enough RAM for any variant - setup will refuse to install."

    ttk.Label(body, text=info_text,
              foreground="red" if not fits_any else GUI_GRADE_COLORS["green"] if best_variant
              else "black").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

    def add_label(text, line):
        ttk.Label(body, text=text).grid(row=line, column=0, sticky="w", padx=(0, 8), pady=3)

    add_label("Model variant:", 1)

    variant_list = tk.Listbox(body, width=86, height=len(variant_rows),
                              activestyle="dotbox", exportselection=False)
    variant_list.grid(row=1, column=1, columnspan=2, rowspan=2, sticky="w")

    for index, v in enumerate(variant_rows):
        grade, _note = grades[index]
        variant_list.insert(tk.END, variant_label(v, grade, v is fastest_variant))
        variant_list.itemconfig(index, foreground=GUI_GRADE_COLORS[grade])

    variant_list.selection_set(default_index)
    variant_list.see(default_index)

    def selected_index():
        selection = variant_list.curselection()

        if not selection:
            variant_list.selection_set(default_index)
            selection = variant_list.curselection()

        return selection[0]

    def info_popup(title, text, color="black"):
        popup = tk.Toplevel(root)
        popup.title(title)
        popup.resizable(False, False)

        ttk.Label(popup, text=text, foreground=color, wraplength=560,
                  justify="left").pack(padx=14, pady=(12, 4))
        ttk.Button(popup, text="Close", command=popup.destroy).pack(padx=14, pady=(4, 12))
        popup.transient(root)

    def show_variant_info():
        index = selected_index()
        v = variant_rows[index]
        grade, note = grades[index]

        info_popup(short_name(v),
                   variant_info_text(sc, v, grade, note, ram_gb, v is best_variant,
                                     v is fastest_variant, variant_rows),
                   GUI_GRADE_COLORS[grade])

    ttk.Button(body, text="\u24d8", width=4, command=show_variant_info).grid(
        row=1, column=3, sticky="nw", padx=(6, 0))

    add_label("Model folder:", 3)
    data_dir_var = tk.StringVar(value=default_data_dir)
    ttk.Entry(body, textvariable=data_dir_var, width=72).grid(row=3, column=1, sticky="w")

    def browse():
        picked = filedialog.askdirectory(title="Model folder",
                                         initialdir=data_dir_var.get() or os.path.expanduser("~"))
        if picked:
            data_dir_var.set(os.path.normpath(picked))

    ttk.Button(body, text="Browse...", width=10, command=browse).grid(row=3, column=2,
                                                                      sticky="w", padx=(6, 0))

    add_label("Context:", 4)
    context_var = tk.StringVar(value=default_context_label)
    context_row = ttk.Frame(body)
    context_row.grid(row=4, column=1, sticky="w")
    ttk.Combobox(context_row, textvariable=context_var, values=context_labels,
                 state="readonly", width=34).pack(side="left")
    ttk.Button(context_row, text="\u24d8", width=4,
               command=lambda: info_popup("Context size", context_info_text())).pack(
        side="left", padx=(6, 0))

    add_label("KV cache:", 5)
    kv_var = tk.StringVar(value=default_kv_label)
    kv_row = ttk.Frame(body)
    kv_row.grid(row=5, column=1, sticky="w")
    ttk.Combobox(kv_row, textvariable=kv_var, values=kv_labels,
                 state="readonly", width=34).pack(side="left")
    ttk.Button(kv_row, text="\u24d8", width=4,
               command=lambda: info_popup("KV cache", kv_info_text())).pack(side="left",
                                                                            padx=(6, 0))

    add_label("Server port:", 6)
    port_var = tk.StringVar(value=str(default_port))
    ttk.Entry(body, textvariable=port_var, width=10).grid(row=6, column=1, sticky="w")

    add_label("Isolation:", 7)
    isolation_var = tk.StringVar(value=default_isolation)

    isolation_rows = [("none", "None"),
                      ("local", "Local Only"),
                      ("docker", "Docker"),
                      ("podman", "Podman")]

    if os.name == "nt":
        isolation_rows.append(("wsl", "WSL Containers"))

    for offset, (value, label) in enumerate(isolation_rows):
        line = 7 + offset
        radio = ttk.Radiobutton(body, text=label, variable=isolation_var, value=value)
        radio.grid(row=line, column=1, sticky="w", pady=1)
        ttk.Button(body, text="\u24d8", width=4,
                   command=lambda which=value: info_popup(label, isolation_info(which))).grid(
            row=line, column=2, sticky="w", padx=(6, 0))

    add_label("scode shortcut:", 12)
    shell_var = tk.StringVar(value="auto")
    ttk.Combobox(body, textvariable=shell_var, values=alias_shells,
                 state="readonly", width=12).grid(row=12, column=1, sticky="w")

    alias_status = ttk.Label(body, text="", foreground="#666666", wraplength=240, justify="left")
    alias_status.grid(row=13, column=1, sticky="w", padx=(12, 0))

    def do_alias():
        shell = sc.get_calling_shell() if shell_var.get() == "auto" else shell_var.get()

        try:
            sc.install_scode_alias(shell)
        except (SystemExit, Exception):
            alias_status.configure(foreground="red",
                                   text="could not add the scode shortcut")
            return

        alias_status.configure(
            foreground="green",
            text="scode shortcut added to %s - open a new shell window to use it"
                 % shell_names.get(shell, shell))

    scode_row = ttk.Frame(body)
    scode_row.grid(row=13, column=0, sticky="w")
    ttk.Button(scode_row, text="Set Alias", width=10, command=do_alias).pack(side="left")
    ttk.Button(scode_row, text="\u24d8", width=4,
               command=lambda: info_popup("scode shortcut", scode_info_text())).pack(
        side="left", padx=(6, 0))

    engine_status = ttk.Label(body, text="", foreground="#666666", wraplength=240, justify="left")
    engine_status.grid(row=14, column=1, sticky="w", padx=(12, 0))

    def do_purge_engine():
        state_json = sc.read_json(sc.STATE_PATH) or {}
        strata_settings = sc.read_json(sc.STRATA_SETTINGS_PATH) or {}

        roots = []

        if state_json.get("StrataRoot"):
            roots.append(state_json["StrataRoot"])

        roots.extend(strata_settings.get("installs", []))
        roots.extend(sc.glob.glob(os.path.join(sc.APP_DIR, "strata*")))

        searched = set()
        deleted = []
        failed = []
        data_dir = data_dir_var.get().strip()

        for root in roots:
            if not root or not os.path.isdir(root) or root in searched:
                continue

            searched.add(root)
            removed, failures = sc.delete_strata_engine(root, data_dir)
            deleted += removed
            failed += failures

        if deleted:
            engine_status.configure(
                foreground="green",
                text="removed %d engine file(s); -Setup re-creates it on the next run (needs network)"
                     % len(deleted))
        elif failed:
            engine_status.configure(foreground="red",
                                    text="could not remove " + "; ".join(failed))
        else:
            engine_status.configure(foreground="#666666",
                                    text="no downloaded Strata engine found - nothing to delete")

    engine_row = ttk.Frame(body)
    engine_row.grid(row=14, column=0, sticky="w")
    ttk.Button(engine_row, text="Delete Strata", width=14,
               command=do_purge_engine).pack(side="left")
    ttk.Button(engine_row, text="\u24d8", width=4,
               command=lambda: info_popup("Strata engine", engine_info_text())).pack(
        side="left", padx=(6, 0))

    outcome = {}

    def on_ok():
        selection = variant_list.curselection()

        if not selection:
            messagebox.showerror("Strata Coder setup", "Pick a model variant.")
            return

        variant = variant_rows[selection[0]]

        data_dir = data_dir_var.get().strip()

        if not data_dir:
            messagebox.showerror("Strata Coder setup", "The model folder cannot be empty.")
            return

        context = context_values.get(context_var.get())

        if context is None:
            messagebox.showerror("Strata Coder setup", "Pick a context size.")
            return

        kv = kv_values.get(kv_var.get())

        if kv is None:
            messagebox.showerror("Strata Coder setup", "Pick a KV cache quantization.")
            return

        port_text = port_var.get().strip()

        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            messagebox.showerror("Strata Coder setup", "Port must be a number from 1 through 65535.")
            return

        outcome["result"] = {
            "family": variant["family"],
            "model": variant["model"],
            "dataDir": data_dir,
            "context": context,
            "kv": kv,
            "port": int(port_text),
            "isolation": isolation_var.get(),
        }

        root.destroy()

    variant_list.bind("<Double-Button-1>", lambda _event: on_ok())

    buttons = ttk.Frame(body)
    buttons.grid(row=15, column=0, columnspan=4, sticky="e", pady=(14, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
    ttk.Button(buttons, text="OK", command=on_ok).pack(side="right")

    root.resizable(False, False)
    root.mainloop()

    return outcome.get("result")


CHILD_ENV = "STRATA_SETUP_GUI_CHILD"


def spawn_detached_gui(overrides=None):
    env = dict(os.environ)
    env[CHILD_ENV] = json.dumps(overrides or {})

    options = {"env": env,
               "stdin": subprocess.DEVNULL,
               "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL}

    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True

    subprocess.Popen([sys.executable, os.path.abspath(__file__)], **options)


def apply_setup(sc, result):
    command = [
        sys.executable, CLI_PATH, "-Setup",
        "-Family", result["family"],
        "-Model", result["model"],
        "-DataDir", result["dataDir"],
        "-Context", str(result["context"]),
        "-Kv", result["kv"],
        "-Port", str(result["port"]),
        "-Isolation", result["isolation"],
        "-NoAlias",
        "-NoGui",
    ]

    options = {}

    if sc.IS_WINDOWS:
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:
            options["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    else:
        for terminal in ("x-terminal-emulator", "xterm"):
            path = shutil.which(terminal)

            if path:
                command = [path, "-e"] + command
                break

        options["start_new_session"] = True

    subprocess.Popen(command, **options)


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    if os.environ.get(CHILD_ENV) is None:
        if not is_available():
            raise SystemExit("no display available for the setup window - run: "
                             "python strata-coder.py -Setup -NoGui")

        spawn_detached_gui()
        return

    sc = load_cli()
    result = run_setup_gui(sc, json.loads(os.environ[CHILD_ENV]))

    if result is None:
        return

    apply_setup(sc, result)


if __name__ == "__main__":
    main()
