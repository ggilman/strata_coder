#!/usr/bin/env python3
"""setup.py - the Strata Coder setup menu, console and GUI.

All menu text (questions, option labels, hints) lives in this file.
strata-coder.py imports it for its -Setup console menu, and -Setup opens the
GUI from here automatically when the machine can display one (force the
console menu with -NoGui). Running this file directly opens the GUI right
away:

    python setup.py

Pressing OK starts strata-coder.py -Setup with the chosen parameters, which
saves them and installs and configures everything. The scode shortcut is an
action, not an option: its button takes effect the moment it is pressed.
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
    "offline": "Stay fully local, block OpenCode web access?",
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


def variant_label(variant):
    return "%s (needs %d GB RAM)" % (variant["title"], variant["min_ram_gb"])


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
    context, kv, port, offline, and variant as a (family, model) pair) which
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

    if "offline" in overrides:
        default_offline = bool(overrides["offline"])
    else:
        default_offline = saved.get("offline") is True

    default_variant = None

    if "variant" in overrides:
        family, model = overrides["variant"]
        candidates = [v for v in sc.VARIANTS
                      if (not family or v["family"] == family)
                      and (not model or v["model"] == model)]

        if candidates:
            default_variant = sc.select_fitting_variant(candidates, ram_gb) or candidates[0]

    if not default_variant and saved.get("family") and saved.get("model"):
        default_variant = next(
            (v for v in sc.VARIANTS
             if v["family"] == saved["family"] and v["model"] == saved["model"]),
            None)

    if not default_variant:
        default_variant = sc.select_fitting_variant(sc.VARIANTS, ram_gb) or sc.VARIANTS[0]

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

    variant_values = {}
    variant_labels = []

    for v in sc.VARIANTS:
        fits = ram_gb >= v["min_ram_gb"]
        label = "%s  [needs %d GB RAM%s]" % (
            v["title"], v["min_ram_gb"], "" if fits else " - NOT ENOUGH RAM ON THIS PC")
        variant_values[label] = v
        variant_labels.append(label)

    default_variant_label = next(
        (l for l in variant_labels if variant_values[l] is default_variant), variant_labels[0])

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

    if not fits_any:
        info_text += "  WARNING: not enough RAM for any variant - setup will refuse to install."

    ttk.Label(body, text=info_text, foreground="red" if not fits_any else "black").grid(
        row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))

    def add_label(text, line):
        ttk.Label(body, text=text).grid(row=line, column=0, sticky="w", padx=(0, 8), pady=3)

    add_label("Model variant:", 1)
    variant_var = tk.StringVar(value=default_variant_label)
    ttk.Combobox(body, textvariable=variant_var, values=variant_labels,
                 state="readonly", width=86).grid(row=1, column=1, columnspan=3, sticky="w")

    add_label("Model folder:", 2)
    data_dir_var = tk.StringVar(value=default_data_dir)
    ttk.Entry(body, textvariable=data_dir_var, width=72).grid(row=2, column=1, sticky="w")

    def browse():
        picked = filedialog.askdirectory(title="Model folder",
                                         initialdir=data_dir_var.get() or os.path.expanduser("~"))
        if picked:
            data_dir_var.set(os.path.normpath(picked))

    ttk.Button(body, text="Browse...", width=10, command=browse).grid(row=2, column=2,
                                                                      sticky="w", padx=(6, 0))

    add_label("Context:", 3)
    context_var = tk.StringVar(value=default_context_label)
    ttk.Combobox(body, textvariable=context_var, values=context_labels,
                 state="readonly", width=34).grid(row=3, column=1, columnspan=3, sticky="w")

    add_label("KV cache:", 4)
    kv_var = tk.StringVar(value=default_kv_label)
    ttk.Combobox(body, textvariable=kv_var, values=kv_labels,
                 state="readonly", width=34).grid(row=4, column=1, columnspan=3, sticky="w")

    add_label("Server port:", 5)
    port_var = tk.StringVar(value=str(default_port))
    ttk.Entry(body, textvariable=port_var, width=10).grid(row=5, column=1, sticky="w")

    offline_var = tk.BooleanVar(value=default_offline)
    ttk.Checkbutton(body, text=QUESTIONS["offline"], variable=offline_var).grid(
        row=6, column=0, columnspan=4, sticky="w", pady=3)

    add_label("scode shortcut:", 7)
    shell_var = tk.StringVar(value="auto")
    ttk.Combobox(body, textvariable=shell_var, values=alias_shells,
                 state="readonly", width=12).grid(row=7, column=1, sticky="w")

    alias_status = ttk.Label(body, text="adds the scode alias to the chosen shell; safe to press again",
                             foreground="#666666")
    alias_status.grid(row=7, column=3, sticky="w", padx=(10, 0))

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

    ttk.Button(body, text="Set scode shortcut", width=18, command=do_alias).grid(
        row=7, column=2, sticky="w", padx=(6, 0))

    outcome = {}

    def on_ok():
        variant = variant_values.get(variant_var.get())

        if not variant:
            messagebox.showerror("Strata Coder setup", "Pick a model variant.")
            return

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
            "offline": bool(offline_var.get()),
        }

        root.destroy()

    buttons = ttk.Frame(body)
    buttons.grid(row=8, column=0, columnspan=4, sticky="e", pady=(14, 0))
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
        "-Offline", "true" if result["offline"] else "false",
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
