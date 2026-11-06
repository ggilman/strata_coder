#!/usr/bin/env python3
"""strata-coder - OpenCode launch and setup utility (cross-platform port of strata-coder.ps1).

Runs identically on Windows (cmd, PowerShell, Git Bash) and Linux. Installs per-user.

Modes:
    strata-coder.py [-CheckOnly] [-Family qwen|swift|coder|unsloth] [-Model NAME] [-Context N]
        [-Kv int8|k8v4|q4_0] [-DataDir PATH] [-Port N] [-NoLaunch] [-Update]
        [-Stop] [-Status] [-Setup] [-Isolation none|local|docker|podman|wsl] [-Alias] [-NoAlias] [-NoGui]
        [-AliasShell auto|cmd|powershell|bash|zsh] [-Help]
"""

import argparse
import atexit
import base64
import ctypes
import fnmatch
import glob
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import msvcrt
    import winreg
else:
    import fcntl
    import select
    import termios
    import tty

# ----------------------------------------------------------------------------------------------------
# Paths, version pins, and model definitions
# ----------------------------------------------------------------------------------------------------

PROJECT_DIR = os.getcwd()

if IS_WINDOWS:
    APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local"), "strata-coder")
    STRATA_SETTINGS_PATH = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming"), "Strata", "settings.json")
else:
    APP_DIR = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "strata-coder")
    STRATA_SETTINGS_PATH = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "strata", "settings.json")

OPENCODE_DIR = os.path.join(APP_DIR, "opencode")
OPENCODE_EXE = os.path.join(OPENCODE_DIR, "opencode.exe" if IS_WINDOWS else "opencode")
OPENCODE_CFG = os.path.join(APP_DIR, "opencode.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
SETUP_PATH = os.path.join(APP_DIR, "setup.json")
DOWNLOAD_DIR = os.path.join(APP_DIR, "downloads")
LOG_DIR = os.path.join(APP_DIR, "logs")
UNPACK_DIR = os.path.join(APP_DIR, "strata-unpack")
RG_DIR = os.path.join(APP_DIR, "ripgrep")
RG_EXE = os.path.join(RG_DIR, "rg.exe" if IS_WINDOWS else "rg")
SANDBOX_DIR = os.path.join(APP_DIR, "sandbox")

SANDBOX_NETWORK = "strata-coder-sandbox"
SANDBOX_IMAGE = "strata-coder-opencode"
SANDBOX_IMAGE_REVISION = "r2"
SANDBOX_CONTAINER = "strata-coder-opencode"
SANDBOX_HOME_VOLUME = "strata-coder-opencode-home"
SANDBOX_RELAY = "strata-coder-relay"
SANDBOX_RELAY_IMAGE = "alpine/socat:latest"

WSL_DIR = os.path.join(APP_DIR, "wsl")
WSL_ROOTFS = "/var/lib/machines/strata-coder"
WSL_HOME = "/var/lib/machines/strata-coder-home"
WSL_RELAY_DIR = "/run/strata-coder-wsl"
WSL_RELAY_UNIT = "strata-coder-wsl-relay"
WSL_SANDBOX_USER = "sandbox"
WSL_SCRIPT_DIR = "/usr/local/lib/strata-wsl"
WSL_LAUNCHER_PATH = WSL_SCRIPT_DIR + "/launch.sh"
WSL_PROBE_PATH = WSL_SCRIPT_DIR + "/probe.sh"
WSL_NSPAWN_SCRIPT = "/tmp/strata-wsl-nspawn.sh"
WSL_EXTRAS_PATH = "/tmp/strata-wsl-extras"
WSL_DEBIAN_SUITE = "bookworm"
WSL_DEBIAN_MIRROR = "http://deb.debian.org/debian"
WSL_DISTRO_ENV = "STRATA_WSL_DISTRO"

SCRIPT_PATH = os.path.abspath(__file__)
MENU_PATH = os.path.join(os.path.dirname(SCRIPT_PATH), "setup.py")
STATUS_PATH = os.path.join(os.path.dirname(SCRIPT_PATH), "status.py")

PINNED_STRATA = "v0.1.40.3"

VARIANTS = [
    # "speed_tok_s": answers written per second in a short chat, "prompt_tok_s": tokens/s read on a
    # 32K prompt (Strata's published measurements on an RTX 5070 12 GB with 64 GB RAM; other cards
    # land within about +-20%, more VRAM is faster), "quality": Strata's own one-word quality rating.
    {
        "family": "qwen",
        "model": "IQ3_S",
        "min_ram_gb": 60,
        "download_gb": 83.6,
        "arena_gb": 50.3,
        "quality": 90,
        "speed_tok_s": 53,
        "prompt_tok_s": 1620,
        "quality_word": "best",
        "title": "Qwen3.8-Flash-Next IQ3_S (3.5-bit, best quality)",
        "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "qwen",
        "model": "IQ3_XXS",
        "min_ram_gb": 58,
        "download_gb": 75.8,
        "arena_gb": 42.9,
        "quality": 80,
        "speed_tok_s": 62,
        "prompt_tok_s": 1750,
        "quality_word": "great",
        "title": "Qwen3.8-Flash-Next IQ3_XXS (3-bit)",
        "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "qwen",
        "model": "IQ2_XS",
        "min_ram_gb": 46,
        "download_gb": 68.0,
        "arena_gb": 35.5,
        "quality": 70,
        "speed_tok_s": 79,
        "prompt_tok_s": 2090,
        "quality_word": "better",
        "title": "Qwen3.8-Flash-Next IQ2_XS (2-bit; recommended for 48-64 GB RAM)",
        "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "qwen",
        "model": "Q2_0",
        "min_ram_gb": 48,
        "download_gb": 66.4,
        "arena_gb": 34.0,
        "quality": 65,
        "speed_tok_s": 94,
        "prompt_tok_s": 2650,
        "quality_word": "good",
        "title": "Qwen3.8-Flash-Next Q2_0 (2-bit, the fastest)",
        "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "swift",
        "model": "IQ3_XXS",
        "min_ram_gb": 60,
        "download_gb": 75.8,
        "arena_gb": 42.9,
        "quality": 79,
        "speed_tok_s": 62,
        "prompt_tok_s": 1750,
        "quality_word": "great",
        "title": "Swift 1.5 IQ3_XXS (3-bit; Qwen3.8-Flash-Next fine-tune, much shorter thinking)",
        "repo": "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "swift",
        "model": "IQ2_XS",
        "min_ram_gb": 48,
        "download_gb": 68.0,
        "arena_gb": 35.5,
        "quality": 69,
        "speed_tok_s": 79,
        "prompt_tok_s": 2090,
        "quality_word": "better",
        "title": "Swift 1.5 IQ2_XS (2-bit i-quant; Qwen3.8-Flash-Next fine-tune, much shorter thinking)",
        "repo": "ukisai/Swift-1.5-Qwen3.8-Flash-Next-GSQ-RCO-GGUF",
    },
    {
        "family": "coder",
        "model": "IQ1_M",
        "min_ram_gb": 30,
        "download_gb": 58.4,
        "arena_gb": 23.4,
        "quality": 60,
        "speed_tok_s": 55,
        "prompt_tok_s": 2180,
        "quality_word": "code only",
        "title": "Qwen3.8-Flash-Next Coder IQ1_M (code-focused; fits 32 GB RAM)",
        "repo": "ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-Coder-GGUF",
    },
    {
        "family": "unsloth",
        "model": "UD-IQ4_XS",
        "min_ram_gb": 48,
        "download_gb": 93.7,
        "arena_gb": 59.5,
        "quality": 95,
        "budget": True,
        "quality_word": "above IQ3_S",
        "title": "Qwen3.8-Flash-Next (Unsloth) UD-IQ4_XS (~4-bit, quality between IQ3_S and UD-Q4_K_XL)",
        "repo": "unsloth/Qwen3.8-Flash-Next-GGUF",
    },
    {
        "family": "unsloth",
        "model": "UD-Q4_K_XL",
        "min_ram_gb": 48,
        "download_gb": 111.3,
        "arena_gb": 77.0,
        "quality": 98,
        "budget": True,
        "experimental": True,
        "quality_word": "best",
        "title": "Qwen3.8-Flash-Next (Unsloth) UD-Q4_K_XL (4-bit, the best quality)",
        "repo": "unsloth/Qwen3.8-Flash-Next-GGUF",
    },
]

MIN_VRAM_MIB = 11500
MIN_DRIVER = 580
MIN_COMPUTE_CAP = 7.5
MTP_GB = 8
MARGIN = 1.15
LOAD_MINUTES = 15
SLACK_GB = 1
SETUP_MINUTES = 6

KV_BYTES_PER_WEIGHT = {"int8": 1.0625, "k8v4": 0.8125, "q4_0": 0.5625}
COMFORTABLE_HEADROOM_GB = 8
TIGHT_HEADROOM_GB = 2
# below this RAM the budget variants (Unsloth) keep only part of their experts in RAM and read
# the rest from the SSD, which is much slower than the smaller quantizations held entirely in RAM
SSD_BUDGET_RAM_GB = 80

# Isolation: none = nothing extra; local = host-side policies around OpenCode; docker / podman =
# OpenCode inside a container with no internet; wsl = the same container wall run by systemd-nspawn
# inside a systemd-enabled WSL 2 distro on Windows, with no container engine installed at all.
ISOLATION_CHOICES = ["none", "local", "docker", "podman", "wsl"]
ISOLATION_LABELS = {"none": "None", "local": "Local Only", "docker": "Docker",
                    "podman": "Podman", "wsl": "WSL Containers"}
CONTAINER_ISOLATIONS = ("docker", "podman")

LOG_KEEP = 20
DEFAULT_CONTEXT_LIMIT = 131072
MAX_OUTPUT_LIMIT = 32768
MIN_OUTPUT_LIMIT = 256

RG_ASSET_FILTER = "ripgrep-*x86_64-pc-windows-msvc.zip" if IS_WINDOWS else "ripgrep-*-x86_64-unknown-linux-musl.tar.gz"

# ----------------------------------------------------------------------------------------------------
# Output and error helpers
# ----------------------------------------------------------------------------------------------------

def _enable_virt_terminal():
    if not IS_WINDOWS:
        return True
    try:
        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, ctypes.c_uint32(mode.value | 0x0004))
    except Exception:
        pass
    return True


COLOR_ON = sys.stdout.isatty() and (IS_WINDOWS and _enable_virt_terminal() or True)

RESET, CYAN, GREEN, GRAY, YELLOW, RED, DARKGRAY = (
    "\033[0m", "\033[36m", "\033[32m", "\033[90m", "\033[33m", "\033[31m", "\033[90m",
)

GRADE_COLORS = {"green": GREEN, "yellow": YELLOW, "red": RED}

TRANSCRIPT = None
TRANSCRIPT_PATH = None
SETUP_LOCK = None


def _emit(color, text=""):
    if color and COLOR_ON:
        sys.stdout.write(color + text + RESET + "\n")
    else:
        sys.stdout.write(text + "\n")
    sys.stdout.flush()
    if TRANSCRIPT:
        TRANSCRIPT.write(text + "\n")
        TRANSCRIPT.flush()


def write_step(message):
    _emit(None, "")
    _emit(CYAN, "==> " + message)


def write_ok(message):
    _emit(GREEN, "    ok    " + message)


def write_info(message):
    _emit(None, "          " + message)


def write_skip(message):
    _emit(GRAY, "    skip  " + message)


def write_warn(message):
    _emit(YELLOW, "    warn  " + message)


def fail(message, hint=None):
    _emit(None, "")
    _emit(RED, "STOPPED: " + message)
    if hint:
        _emit(RED, "         " + hint)
    if TRANSCRIPT_PATH:
        _emit(DARKGRAY, "         Log: " + TRANSCRIPT_PATH)
    close_session_resources()
    sys.exit(1)


# ----------------------------------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------------------------------

def read_json(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except Exception as exc:
        write_warn("could not parse %s (%s); ignoring its contents" % (path, exc))
        return None


def write_text_file(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def update_profile_block(path, begin, end, block, strip_pattern=None):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    text = ""
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()

    pattern = r"(?ms)^[ \t]*" + re.escape(begin) + r".*?^[ \t]*" + re.escape(end) + r"[ \t]*\r?\n?"
    text = re.sub(pattern, "", text)

    if strip_pattern:
        text = re.sub(strip_pattern, "", text, flags=re.MULTILINE)

    if text and not text.endswith("\n"):
        text += "\n"

    write_text_file(path, text + block + "\n")


def get_int_setting(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def saved_isolation(saved):
    """The isolation value of a saved setup.json. Older files stored the offline/sandbox
    booleans instead, so those are migrated: sandbox true was Docker, offline true was local."""
    value = str((saved or {}).get("isolation") or "").strip().lower()

    if value in ISOLATION_CHOICES:
        if value == "wsl" and not IS_WINDOWS:
            return "none"
        return value

    if (saved or {}).get("sandbox") is True:
        return "docker"

    if (saved or {}).get("offline") is True:
        return "local"

    return "none"


def grade_variant(variant, ram_gb):
    """('green' | 'yellow' | 'red', the reason) for this PC's RAM: green runs at full speed,
    yellow runs with a cost, red will not run."""
    if ram_gb < variant["min_ram_gb"]:
        return "red", "not enough RAM on this PC"

    if variant.get("experimental"):
        return "yellow", "experimental"

    if variant.get("budget") and ram_gb < SSD_BUDGET_RAM_GB:
        return "yellow", "part of the experts are read from the SSD (slower)"

    if ram_gb < variant["min_ram_gb"] + TIGHT_HEADROOM_GB:
        return "yellow", "tight on this PC"

    return "green", ""


def pick_best_variant(candidates, ram_gb):
    """The best variant this PC can run: the highest quality one that fits without a warning;
    when every fitting variant carries a warning, the first fitting one in list order."""
    fitting = [c for c in candidates if ram_gb >= c["min_ram_gb"]]

    if not fitting:
        return None

    comfortable = [c for c in fitting if grade_variant(c, ram_gb)[0] == "green"]

    if comfortable:
        return max(comfortable, key=lambda c: c["quality"])

    return fitting[0]


def close_session_resources():
    global SETUP_LOCK, TRANSCRIPT, TRANSCRIPT_PATH

    if SETUP_LOCK:
        try:
            unlock_file(SETUP_LOCK)
        except Exception:
            pass
        SETUP_LOCK = None

    if TRANSCRIPT:
        try:
            TRANSCRIPT.close()
        except Exception:
            pass
        TRANSCRIPT = None


def lock_file(path):
    handle = open(path, "a+")
    if IS_WINDOWS:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


def lock_file_blocking(path):
    handle = open(path, "a+")
    if IS_WINDOWS:
        while True:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def unlock_file(handle):
    try:
        if IS_WINDOWS:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def save_state(changes):
    state = {}
    existing = read_json(STATE_PATH)
    if isinstance(existing, dict):
        state.update(existing)
    state.update(changes)
    os.makedirs(APP_DIR, exist_ok=True)
    write_text_file(STATE_PATH, json.dumps(state, indent=2))


def run_native(exe, args_list):
    try:
        proc = subprocess.run(
            [exe] + list(args_list),
            capture_output=True,
            text=True,
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "").splitlines()
    except Exception:
        return 1, []


def mouse_off_sequences():
    esc = "\x1b"
    return [
        esc + "[?1000l", esc + "[?1002l", esc + "[?1003l", esc + "[?1004l",
        esc + "[?1005l", esc + "[?1006l", esc + "[?1015l", esc + "[?2004l",
    ]


def reset_terminal_mouse():
    payload = "".join(mouse_off_sequences())
    try:
        sys.stdout.buffer.write(payload.encode("ascii"))
        sys.stdout.buffer.flush()
    except Exception:
        pass

    if IS_WINDOWS:
        try:
            kernel32 = ctypes.windll.kernel32
            stdin_handle = kernel32.GetStdHandle(-10)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
                clear = 0x0010 | 0x0200
                kernel32.SetConsoleMode(stdin_handle, ctypes.c_uint32(mode.value & ~clear))
        except Exception:
            pass


def existing_ancestor(path):
    path = os.path.abspath(path)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def get_free_gb(path):
    usage = shutil.disk_usage(existing_ancestor(path))
    return round(usage.free / (1024 ** 3), 1)


def get_folder_gb(path):
    if not os.path.isdir(path):
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return round(total / (1024 ** 3), 1)


def http_head_size(url):
    request = urllib.request.Request(url, method="HEAD")
    request.add_header("User-Agent", "strata-coder")
    with urllib.request.urlopen(request, timeout=15) as response:
        value = response.headers.get("Content-Length")
        return int(value) if value else None


def download_file(url, out_file):
    parent = os.path.dirname(out_file)
    if parent:
        os.makedirs(parent, exist_ok=True)

    local_size = 0
    if os.path.isfile(out_file):
        local_size = os.path.getsize(out_file)
        try:
            remote_size = http_head_size(url)
            if remote_size and local_size > 0 and remote_size == local_size:
                write_info("the file is already fully downloaded")
                return
        except Exception:
            pass

    curl = shutil.which("curl.exe" if IS_WINDOWS else "curl") or shutil.which("curl")

    if curl:
        proc = subprocess.run(
            [curl, "-L", "--fail", "--retry", "3", "--retry-delay", "5", "-C", "-",
             "-o", out_file, "-w", "%{http_code}", url],
            capture_output=True, text=True, errors="replace",
        )
        http_code = (proc.stdout or "").strip()

        if proc.returncode == 0:
            return

        if proc.returncode == 22 and http_code == "416" and local_size > 0:
            write_info("the server reports the file is already fully downloaded")
            return

        write_warn("curl failed with exit code %d; retrying with the built-in downloader" % proc.returncode)
        if os.path.isfile(out_file):
            os.remove(out_file)
    else:
        write_warn("curl was not found; the built-in downloader is much slower for large files")

    try:
        request = urllib.request.Request(url)
        request.add_header("User-Agent", "strata-coder")
        with urllib.request.urlopen(request, timeout=600) as response, open(out_file, "wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception as exc:
        if os.path.isfile(out_file):
            os.remove(out_file)
        raise RuntimeError("could not download %s (%s)" % (url, exc))


def extract_archive(archive, destination):
    os.makedirs(destination, exist_ok=True)
    if archive.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
    else:
        with tarfile.open(archive, "r:*") as tf:
            try:
                tf.extractall(destination, filter="data")
            except TypeError:
                tf.extractall(destination)


def get_downloaded_file(url, zip_path, stage, file_filter):
    download_file(url, zip_path)

    shutil.rmtree(stage, ignore_errors=True)

    try:
        extract_archive(zip_path, stage)
    except Exception as exc:
        shutil.rmtree(stage, ignore_errors=True)
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        raise RuntimeError("the archive is damaged (%s); it was removed" % exc)

    staged = find_matching_file(stage, file_filter)

    if not staged:
        shutil.rmtree(stage, ignore_errors=True)
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        raise RuntimeError("%s was not found in the archive" % file_filter)

    return staged


def find_matching_file(root, pattern):
    for current, _dirs, files in os.walk(root):
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                return os.path.join(current, name)
    return None


def get_latest_release(repository):
    try:
        request = urllib.request.Request(
            "https://api.github.com/repos/%s/releases/latest" % repository)
        request.add_header("User-Agent", "strata-coder")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        write_warn("could not contact GitHub for %s (%s)" % (repository, exc))
        return None


def install_tool_from_release(repo, release, asset_filter, exe_name, dest_exe, stage_name, fail_hint):
    if not release:
        release = get_latest_release(repo)

    if not release:
        write_warn("could not install %s; %s" % (repo, fail_hint))
        return None

    assets = release.get("assets", [])
    asset = next((a for a in assets if fnmatch.fnmatch(a.get("name", ""), asset_filter)), None)

    if not asset:
        write_warn("%s release %s has no matching x86_64 archive; %s"
                   % (repo, release.get("tag_name", "?"), fail_hint))
        return None

    zip_path = os.path.join(DOWNLOAD_DIR, asset["name"])
    stage = os.path.join(APP_DIR, stage_name + "-unpack")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    try:
        staged_exe = get_downloaded_file(asset["browser_download_url"], zip_path, stage, exe_name)
    except Exception as exc:
        write_warn("could not prepare %s (%s); %s" % (repo, exc, fail_hint))
        return None

    os.makedirs(os.path.dirname(dest_exe), exist_ok=True)
    shutil.move(staged_exe, dest_exe)
    if not IS_WINDOWS:
        try:
            os.chmod(dest_exe, 0o755)
        except OSError:
            pass
    shutil.rmtree(stage, ignore_errors=True)
    if os.path.isfile(zip_path):
        os.remove(zip_path)

    return release


def show_log_tail(path, lines=25):
    if path and os.path.isfile(path):
        _emit(DARKGRAY, "    --- last %d lines of %s ---" % (lines, path))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read().splitlines()
            for line in content[-lines:]:
                _emit(None, "    | " + line)
        except Exception:
            pass


# ----------------------------------------------------------------------------------------------------
# Strata server helpers
# ----------------------------------------------------------------------------------------------------

def strata_health(server_port):
    try:
        request = urllib.request.Request("http://127.0.0.1:%d/health" % server_port)
        with urllib.request.urlopen(request, timeout=5) as response:
            health = json.load(response)
        if health.get("service") == "strata":
            return health
    except Exception:
        pass
    return None


def strata_models(server_port):
    try:
        request = urllib.request.Request("http://127.0.0.1:%d/v1/models" % server_port)
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
        return [m for m in body.get("data", []) if m and m.get("id") and str(m["id"]).strip()]
    except Exception:
        return []


def strata_ready(server_port):
    health = strata_health(server_port)
    if not health:
        return False
    if health.get("loaded"):
        return True
    return len(strata_models(server_port)) > 0


def strata_chat_completion(server_port, model_id):
    body = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "temperature": 0,
        "max_tokens": 4,
        "stream": False,
    }).encode("utf-8")

    try:
        request = urllib.request.Request(
            "http://127.0.0.1:%d/v1/chat/completions" % server_port,
            data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", "Bearer local")
        with urllib.request.urlopen(request, timeout=180) as response:
            return True, None, json.load(response)
    except Exception as exc:
        return False, str(exc), None


def port_in_use(server_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        return sock.connect_ex(("127.0.0.1", server_port)) == 0
    finally:
        sock.close()


def strata_server_processes():
    processes = []

    if IS_WINDOWS:
        script = (
            "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
            "| ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        )
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, errors="replace",
            )
            for line in (proc.stdout or "").splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2 and parts[0].strip().isdigit():
                    processes.append({"pid": int(parts[0]), "command": parts[1]})
        except Exception:
            pass
    else:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open("/proc/%s/cmdline" % entry, "rb") as handle:
                    command = handle.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
                if command:
                    processes.append({"pid": int(entry), "command": command})
            except OSError:
                continue

    return [
        p for p in processes
        if p["command"]
        and re.search(r"server\.py", p["command"])
        and re.search(r"--engine\s+\"?strata", p["command"])
    ]


def server_port(process):
    match = re.search(r"--port\s+\"?(\d+)", process["command"])
    return int(match.group(1)) if match else 8080


def stop_strata_servers(on_port=0):
    processes = strata_server_processes()

    if on_port > 0:
        processes = [p for p in processes if server_port(p) == on_port]

    found = 0

    for process in processes:
        pid = process["pid"]

        if IS_WINDOWS:
            if not _process_exists_win(pid):
                continue

            found += 1
            rc, _ = run_native("taskkill.exe", ["/T", "/F", "/PID", str(pid)])

            if rc == 0:
                write_ok("stopped the Strata server (PID %d)" % pid)
            elif rc != 128:
                write_warn("could not stop the Strata server (PID %d); taskkill exit code %d" % (pid, rc))
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                continue

            found += 1

            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                    time.sleep(0.1)
                else:
                    os.kill(pid, signal.SIGKILL)
                write_ok("stopped the Strata server (PID %d)" % pid)
            except OSError as exc:
                write_warn("could not stop the Strata server (PID %d); %s" % (pid, exc))

    return found


def _process_exists_win(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        ctypes.c_uint32(PROCESS_QUERY_LIMITED_INFORMATION), False, ctypes.c_uint32(pid))
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def wait_strata_ready(process, server_port):
    deadline = time.time() + LOAD_MINUTES * 60

    while time.time() < deadline:
        if strata_ready(server_port):
            return True

        if process:
            if process.poll() is not None:
                return False
        elif not strata_server_processes():
            return False

        time.sleep(3)

    return False


# ----------------------------------------------------------------------------------------------------
# Setup, candidates, and system discovery
# ----------------------------------------------------------------------------------------------------

def get_candidates(family_filter, model_filter):
    candidates = list(VARIANTS)

    if family_filter:
        candidates = [c for c in candidates if c["family"] == family_filter]

    if model_filter:
        candidates = [c for c in candidates if c["model"] == model_filter.upper()]

    if not candidates:
        known = ", ".join("%s/%s" % (v["family"], v["model"]) for v in VARIANTS)
        fail("no model variant matches -Family '%s' -Model '%s'" % (family_filter, model_filter),
             "Choose one of: " + known)

    return candidates


def get_calling_shell():
    if os.environ.get("MSYSTEM"):
        return "bash"

    if not IS_WINDOWS:
        wanted = ("bash", "zsh", "sh", "dash", "ksh", "fish")
        pid = os.getpid()
        for _ in range(6):
            try:
                with open("/proc/%d/stat" % pid) as handle:
                    fields = handle.read().rsplit(") ", 1)[1].split()
                ppid = int(fields[1])
                with open("/proc/%d/comm" % pid) as handle:
                    name = handle.read().strip().lower().lstrip("-")
                if name in wanted:
                    return "zsh" if name == "zsh" else "bash"
                if ppid <= 1 or ppid == pid:
                    break
                pid = ppid
            except (OSError, IndexError, ValueError):
                break

        shell = os.path.basename(os.environ.get("SHELL") or "")
        if shell in wanted:
            return "zsh" if shell == "zsh" else "bash"
        return "bash"

    try:
        pid = os.getpid()
        for _ in range(6):
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"ProcessId = %d\") | "
                 "ForEach-Object { $_.Name + \"`t\" + $_.ParentProcessId }" % pid],
                capture_output=True, text=True, errors="replace",
            )
            line = (proc.stdout or "").strip()
            if not line:
                break
            parts = line.split("\t")
            name = parts[0].strip().lower()
            if name in ("bash.exe", "sh.exe"):
                return "bash"
            if name == "cmd.exe":
                return "cmd"
            if name in ("powershell.exe", "pwsh.exe") and pid != os.getpid():
                return "powershell"
            if len(parts) < 2 or not parts[1].strip().isdigit():
                break
            pid = int(parts[1])
    except Exception:
        pass

    return "cmd"


def _bash_block():
    python_cmd = sys.executable
    if IS_WINDOWS:
        python_cmd = python_cmd.replace("\\", "/")

    printf_off = "".join(seq.replace("\x1b", "\\033") for seq in mouse_off_sequences())

    lines = [
        "# BEGIN strata-coder scode",
        "SCODE_PY=%s" % shlex.quote(python_cmd),
        "SCODE_CMD=%s" % shlex.quote(SCRIPT_PATH.replace("\\", "/") if IS_WINDOWS else SCRIPT_PATH),
        "unset -f scode 2>/dev/null",
        "scode() {",
        '    "$SCODE_PY" "$SCODE_CMD" "$@"',
        "    local rc=$?",
        '    printf "%s" 2>/dev/null >/dev/tty' % printf_off,
        "    return $rc",
        "}",
        "# END strata-coder scode",
    ]
    return "\n".join(lines)


def _strip_legacy_block(path, begin, end):
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return
    pattern = r"(?ms)^[ \t]*" + re.escape(begin) + r".*?^[ \t]*" + re.escape(end) + r"[ \t]*\r?\n?"
    new_text = re.sub(pattern, "", text)
    if new_text != text:
        write_text_file(path, new_text)


def install_scode_alias(shell):
    write_info("adding the scode shortcut to " + shell)

    if shell in ("bash", "zsh"):
        home = os.environ.get("HOME") or os.path.expanduser("~")

        if shell == "bash":
            profile_file = os.path.join(home, ".bash_profile" if IS_WINDOWS else ".bashrc")
        else:
            profile_file = os.path.join(home, ".zshrc")

        begin = "# BEGIN strata-coder scode"
        end = "# END strata-coder scode"

        _strip_legacy_block(profile_file, "# BEGIN strata-coder s2code", "# END strata-coder s2code")
        update_profile_block(profile_file, begin, end, _bash_block(), r"^[ \t]*alias (scode|s2code)=.*\r?\n?")
        write_ok("updated the scode function in " + profile_file)
    elif shell == "powershell":
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                 "$PROFILE.CurrentUserCurrentHost"],
                capture_output=True, text=True, errors="replace",
            )
            profile_file = (proc.stdout or "").strip()
        except Exception:
            profile_file = ""

        if not profile_file:
            fail("could not locate the PowerShell profile to add scode.")

        begin = "# >>> strata-coder scode >>>"
        end = "# <<< strata-coder scode <<<"
        block = "\n".join([
            begin,
            'function scode { & "%s" "%s" @args }' % (sys.executable, SCRIPT_PATH),
            end,
        ])

        _strip_legacy_block(profile_file, "# >>> strata-coder s2code >>>", "# <<< strata-coder s2code <<<")
        update_profile_block(profile_file, begin, end, block)
        write_ok("updated the scode function in " + profile_file)
    elif shell == "cmd":
        user_profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        macro_dir = os.path.join(user_profile, "bin")
        macro_file = os.path.join(macro_dir, "cmd_macros.doskey")

        os.makedirs(macro_dir, exist_ok=True)

        lines = []
        if os.path.isfile(macro_file):
            try:
                with open(macro_file, "r", encoding=_oem_encoding(), errors="replace") as handle:
                    lines = [ln for ln in handle.read().splitlines()
                             if not re.match(r"^\s*(scode|s2code)=", ln)]
            except OSError:
                pass

        lines.append("scode=%s %s $*" % (sys.executable, SCRIPT_PATH))

        with open(macro_file, "w", encoding=_oem_encoding(), newline="") as handle:
            handle.write("\r\n".join(lines) + "\r\n")
        write_ok("updated the DOSKEY macro: " + macro_file)

        loader = 'doskey /macrofile="%s"' % macro_file
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Command Processor", 0,
                                 winreg.KEY_READ | winreg.KEY_WRITE)
            try:
                try:
                    old, _ = winreg.QueryValueEx(key, "AutoRun")
                except OSError:
                    old = None

                if old is None or not str(old).strip():
                    new = loader
                elif macro_file.lower() in str(old).lower():
                    new = old
                else:
                    new = str(old) + " & " + loader

                winreg.SetValueEx(key, "AutoRun", 0, winreg.REG_EXPAND_SZ, new)
            finally:
                winreg.CloseKey(key)
            write_ok("preserved or updated the CMD AutoRun entry")
        except Exception as exc:
            write_warn("the DOSKEY macro was created, but the CMD AutoRun entry could not be "
                       "updated (%s)." % exc)
    else:
        fail("unknown alias target '%s'" % shell, "Valid targets: cmd, powershell, bash, zsh.")

    write_info("open a new shell window to pick up the shortcut")


def _oem_encoding():
    try:
        cp = ctypes.windll.kernel32.GetOEMCP()
        return "cp%d" % cp
    except Exception:
        return "ascii"


def show_help():
    _emit(None, """strata-coder - OpenCode launch and setup utility

Usage:
  strata-coder.py [options]      (python strata-coder.py ... on any shell)
  scode [options] after the shortcut is set up

Options:
  -Help, --help, -h          Show this help.
  -Setup                     Interactive setup menu, then install and stop.
  -CheckOnly                 Show specs and installs; change nothing.
  -NoLaunch                  Set up and start the server without OpenCode.
  -Stop                      Stop the Strata server.
  -Status                    Live tokens/sec panel: a GUI window when the
                             machine can display one, otherwise refreshing
                             in place in this console (-NoGui forces it).
  -Update                    Update Strata, OpenCode, and ripgrep to
                             newest releases.
  -Alias                     Add the scode shortcut. Pick the shell with
                             -AliasShell: auto (default), cmd, powershell,
                             bash, or zsh.
  -NoAlias                   Skip the scode-shortcut question in the -Setup
                             menu and leave the shortcut out.
  -NoGui                     Force the console setup menu instead of the GUI
                             window that -Setup opens when the machine can
                             display one.
  -Family qwen|swift|coder|unsloth
                               Model family.
  -Model <name>              Model variant, e.g. IQ3_S, IQ1_M or UD-IQ4_XS.
  -Context <tokens>          Context size; 0 = Strata recommendation.
  -Kv int8|k8v4|q4_0         KV cache quantization.
  -DataDir <path>            Model folder.
  -Port <port>               Server port, default 8080.
    -Isolation <mode>          How OpenCode is isolated: none, local (host web
                               block), docker / podman (container with no
                               internet), wsl (same container inside WSL 2,
                               no container engine). Applies to this
                               run; add -Setup to save it.
                               scode -Setup -Isolation none turns it off.""")


def install_ripgrep(release):
    release = install_tool_from_release(
        "BurntSushi/ripgrep", release, RG_ASSET_FILTER,
        "rg.exe" if IS_WINDOWS else "rg", RG_EXE, "ripgrep",
        "OpenCode content search will be slower")

    if not release:
        return False

    save_state({"RipgrepVersion": release["tag_name"]})

    write_ok("ripgrep %s is ready at %s" % (release["tag_name"], RG_EXE))

    return True


BOLD = "\033[1m"
BOX_UNI = {"tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
           "h": "\u2500", "v": "\u2502", "lr": "\u251c", "rr": "\u2524"}
BOX_ASC = {"tl": "+", "tr": "+", "bl": "+", "br": "+",
           "h": "-", "v": "|", "lr": "|", "rr": "|"}


def box_glyphs():
    try:
        "".join(BOX_UNI.values()).encode(sys.stdout.encoding or "ascii")
        return BOX_UNI
    except (UnicodeEncodeError, LookupError):
        return BOX_ASC


def box_width():
    try:
        columns = shutil.get_terminal_size().columns
    except Exception:
        columns = 80

    return max(50, min(78, columns - 2))


def _pad(text, width):
    if len(text) > width:
        return text[:width]

    return text + " " * (width - len(text))


def _wrap(text, width):
    lines = []
    current = ""

    for word in text.split():
        while len(word) > width:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:width])
            word = word[width:]

        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = current + " " + word if current else word

    if current:
        lines.append(current)

    return lines or [""]


def _hrule(glyphs, inner, kind, border_color):
    if kind == "top":
        rule = glyphs["tl"] + glyphs["h"] * inner + glyphs["tr"]
    elif kind == "mid":
        rule = glyphs["lr"] + glyphs["h"] * inner + glyphs["rr"]
    else:
        rule = glyphs["bl"] + glyphs["h"] * inner + glyphs["br"]

    if border_color and COLOR_ON:
        rule = border_color + rule + RESET

    return rule


def _vrow(glyphs, text, inner, color, border_color):
    body = _pad(text, inner)

    if color and COLOR_ON:
        body = color + body + RESET

    bar = glyphs["v"]

    if border_color and COLOR_ON:
        bar = border_color + glyphs["v"] + RESET

    return bar + body + bar


def build_box(title, rows, width=None, border_color=None):
    glyphs = box_glyphs()
    inner = (width or box_width()) - 2
    lines = [_hrule(glyphs, inner, "top", border_color)]

    if title is not None:
        lines.append(_vrow(glyphs, title, inner, (BOLD + CYAN) if COLOR_ON else None, border_color))
        lines.append(_hrule(glyphs, inner, "mid", border_color))

    for row in rows:
        if row is None:
            lines.append(_hrule(glyphs, inner, "mid", border_color))
        else:
            lines.append(_vrow(glyphs, row[0], inner, row[1], border_color))

    lines.append(_hrule(glyphs, inner, "bottom", border_color))

    return lines


def _default_rows(default, width=None):
    inner = (width or box_width()) - 2
    rows = [("default:", GRAY if COLOR_ON else None)]

    for line in _wrap(str(default), inner - 3):
        rows.append(("  " + line, (BOLD + GREEN) if COLOR_ON else None))

    return rows


_COMPANION_MODS = {}


def load_companion(path, module_name, purpose):
    if module_name in _COMPANION_MODS:
        return _COMPANION_MODS[module_name]

    if not os.path.exists(path):
        fail("%s must sit next to strata-coder.py %s." % (os.path.basename(path), purpose),
             "Restore %s next to strata-coder.py and run again." % os.path.basename(path))

    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _COMPANION_MODS[module_name] = module

    return module


def load_setup():
    return load_companion(MENU_PATH, "strata_setup_menu", "to show the setup menu")


def load_status():
    return load_companion(STATUS_PATH, "strata_status_panel", "to show the status panel")


def run_setup_menu(state):
    menu = load_setup()

    if state["no_gui"] or not menu.is_available():
        return False

    overrides = {}

    if state["family"] or state["model"]:
        overrides["variant"] = (state["family"], state["model"])

    if state["data_dir_specified"]:
        overrides["dataDir"] = state["data_dir"]

    if state["context_specified"]:
        overrides["context"] = state["context"]

    if state["kv_specified"]:
        overrides["kv"] = state["kv"]

    if state["port_specified"]:
        overrides["port"] = state["port"]

    if state["isolation_specified"]:
        overrides["isolation"] = state["isolation"]

    menu.spawn_detached_gui(overrides)

    write_step("Setup window opened in the background - this command finished; press OK in the window "
               "and the install runs in its own console window")
    return True


def _interactive_available():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (ValueError, AttributeError):
        return False


def _encodable(text):
    try:
        text.encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _read_key():
    if IS_WINDOWS:
        try:
            ch = msvcrt.getwch()

            if ch in ("\x00", "\xe0"):
                ch = msvcrt.getwch()
                return {"H": "up", "P": "down", "K": "left", "M": "right",
                        "I": "pgup", "Q": "pgdn"}.get(ch)

            if ch == "\r":
                return "enter"

            if ch == "\x1b":
                return "esc"

            if ch == "\x03":
                return "intr"

            if ch == "\x1a":
                return "eof"

            return ch
        except KeyboardInterrupt:
            return "intr"

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    try:
        tty.setcbreak(fd)
        ch = sys.stdin.read(1)

        if ch == "" or ch == "\x04":
            return "eof"

        if ch == "\x03":
            return "intr"

        if ch in ("\r", "\n"):
            return "enter"

        if ch == "\x1b":
            if not select.select([sys.stdin], [], [], 0.2)[0]:
                return "esc"

            head = sys.stdin.read(1)
            tail = sys.stdin.read(1) if head in ("[", "O") else ""

            return {"A": "up", "B": "down", "C": "right", "D": "left",
                    "H": "pgup", "F": "pgdn"}.get(tail)

        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _interactive_choice(header, ordered, default_index, default_value, legend=None):
    menu = load_setup()
    arrows = "\u2191\u2193" if _encodable("\u2191\u2193") else "up/down"
    width = box_width()
    inner = width - 2
    cursor = default_index if default_index is not None else 0
    footer = menu.PICK_HINT.replace("up/down", arrows)
    result = default_value
    done = False
    drawn = 0

    print()

    while not done:
        rows = []

        for number, option in enumerate(ordered, 1):
            is_cursor = number - 1 == cursor
            star = "   * default" if number - 1 == default_index else ""
            prefix = " %d. " % number

            if is_cursor:
                prefix = ">" + prefix[1:]

            indent = " " * len(prefix)
            wrapped = _wrap(option["label"], inner - len(prefix) - len(star))
            color = option.get("color") if COLOR_ON else None

            if is_cursor and COLOR_ON:
                color = BOLD + (color or GREEN)

            rows.append((prefix + wrapped[0] + star, color))

            for line in wrapped[1:]:
                rows.append((indent + line, color))

        rows.append(None)

        for line in _legend_lines(legend):
            rows.append((line, GRAY if COLOR_ON else None))

        rows.append((footer, GRAY if COLOR_ON else None))

        block = "\033[K\n".join(build_box(" " + header, rows, width)) + "\033[K\n"

        if drawn:
            sys.stdout.write("\033[%dA" % drawn)

        sys.stdout.write(block)
        sys.stdout.flush()

        drawn = block.count("\n")

        try:
            key = _read_key()
        except KeyboardInterrupt:
            key = "esc"

        if key == "up":
            cursor = (cursor - 1) % len(ordered)
        elif key == "down":
            cursor = (cursor + 1) % len(ordered)
        elif key == "pgup":
            cursor = (cursor - 5) % len(ordered)
        elif key == "pgdn":
            cursor = (cursor + 5) % len(ordered)
        elif key and key.isdigit() and 1 <= int(key) <= len(ordered):
            cursor = int(key) - 1
        elif key == "enter":
            result = ordered[cursor]["value"]
            footer = menu.PICKED_FMT % (ordered[cursor].get("name") or str(result))
            done = True
        elif key in ("esc", "intr", "eof"):
            footer = menu.KEEP_DEFAULT
            done = True

    return result


def _legend_lines(legend):
    return [] if not legend else (list(legend) if isinstance(legend, (list, tuple)) else [legend])


def prompt_choice(header, options, default_value, legend=None):
    menu = load_setup()
    ordered = select_ordered_default(options, default_value)
    default_index = next(
        (i for i, o in enumerate(ordered) if o["value"] == default_value), None)

    if _interactive_available():
        return _interactive_choice(header, ordered, default_index, default_value, legend)

    width = box_width()
    inner = width - 2

    rows = []

    for number, option in enumerate(ordered, 1):
        is_default = number - 1 == default_index
        star = "   * default" if is_default else ""
        prefix = " %d. " % number
        indent = " " * len(prefix)
        wrapped = _wrap(option["label"], inner - len(prefix) - len(star))
        color = option.get("color") if COLOR_ON else None

        if is_default and COLOR_ON:
            color = BOLD + (color or GREEN)

        rows.append((prefix + wrapped[0] + star, color))

        for line in wrapped[1:]:
            rows.append((indent + line, color))

        rows.append(("", None))

    rows.pop()
    rows.append(None)

    for line in _legend_lines(legend):
        rows.append((line, GRAY if COLOR_ON else None))

    rows.append((menu.TEXT_HINT, GRAY if COLOR_ON else None))

    print()
    print("\n".join(build_box(" " + header, rows, width)))

    default_hint = str(default_index + 1) if default_index is not None else str(default_value)

    while True:
        try:
            answer = input(menu.PICK_INPUT % (len(ordered), default_hint))
        except EOFError:
            answer = ""

        answer = answer.strip()

        if answer == "":
            break

        index = get_int_setting(answer, 0)

        if 1 <= index <= len(ordered):
            return ordered[index - 1]["value"]

        by_name = next(
            (o for o in ordered if str(o.get("name", "")).lower() == answer.lower()), None)

        if by_name:
            return by_name["value"]

        write_warn("Enter a number from 1 through %d, a name such as %s, or leave the field blank."
                   % (len(ordered), ordered[0].get("name", ordered[0]["value"])))

    return default_value


def select_ordered_default(options, default_value):
    default_option = next((o for o in options if o["value"] == default_value), None) or options[0]
    return [default_option] + [o for o in options if o["value"] != default_option["value"]]


def prompt_text(header, default):
    print()
    print("\n".join(build_box(" " + header, _default_rows(default))))

    try:
        return input(load_setup().PATH_INPUT).strip()
    except EOFError:
        return default


def prompt_port(header, default_port):
    while True:
        print()
        print("\n".join(build_box(" " + header, _default_rows(str(default_port)))))

        try:
            answer = input(load_setup().PORT_INPUT).strip()
        except EOFError:
            answer = ""

        if answer == "":
            return default_port

        number = get_int_setting(answer, 0)

        if 1 <= number <= 65535:
            return number

        write_warn("Enter a port number from 1 through 65535 or leave the field blank.")


def prompt_yes_no(message, default):
    if _interactive_available():
        options = [{"value": True, "name": "yes", "label": "yes"},
                   {"value": False, "name": "no", "label": "no"}]

        return _interactive_choice(message, options, 0 if default else 1, default)

    yes_color = (BOLD + GREEN) if (default and COLOR_ON) else None
    no_color = (BOLD + GREEN) if (not default and COLOR_ON) else None

    print()
    print("\n".join(build_box(" " + message, [
        ("  Y. yes", yes_color),
        ("  n. no", no_color),
    ])))

    default_answer = "y" if default else "n"

    try:
        answer = input(load_setup().YESNO_INPUT % default_answer)
    except EOFError:
        answer = ""

    if answer.strip() == "":
        return default

    return answer.strip().upper() == "Y"


def prompt_setup_params(spec, state):
    menu = load_setup()
    saved = read_json(SETUP_PATH)

    default_data_dir = None

    if state["data_dir_specified"] and state["data_dir"]:
        default_data_dir = state["data_dir"]
    elif saved and saved.get("dataDir"):
        default_data_dir = saved["dataDir"]

    if not default_data_dir:
        prior_settings = read_json(STRATA_SETTINGS_PATH)

        if prior_settings and prior_settings.get("data_dir") and os.path.exists(prior_settings["data_dir"]):
            default_data_dir = prior_settings["data_dir"]

    if not default_data_dir:
        default_data_dir = os.path.join(os.path.expanduser("~"), "ai-models", "models")

    default_context = 0
    default_kv = "int8"
    default_port = 8080
    default_isolation = saved_isolation(saved)

    if saved:
        default_context = get_int_setting(saved.get("context"), default_context)

        if saved.get("kv"):
            default_kv = str(saved["kv"])

        saved_port = get_int_setting(saved.get("port"), 0)

        if saved_port > 0:
            default_port = saved_port

    if state["context_specified"]:
        default_context = state["context"]

    if state["kv_specified"]:
        default_kv = state["kv"]

    if state["port_specified"]:
        default_port = state["port"]

    selected = None

    if state["family"] or state["model"]:
        requested = get_candidates(state["family"], state["model"])
        selected = pick_best_variant(requested, spec["ram_gb"])

        if not selected:
            selected = requested[0]

    if not selected and saved and saved.get("family") and saved.get("model"):
        selected = next(
            (v for v in VARIANTS if v["family"] == saved["family"] and v["model"] == saved["model"]),
            None)

    if not selected:
        selected = pick_best_variant(VARIANTS, spec["ram_gb"])

    if not selected:
        fail("this PC does not have enough RAM for any configured Strata model variant.")

    write_step(menu.STEP_TEXT)

    if not (state["family"] or state["model"]):
        variant_rows = sorted(VARIANTS, key=lambda v: -v["quality"])
        fitting = [v for v in variant_rows
                   if spec["ram_gb"] >= v["min_ram_gb"] and v.get("speed_tok_s")]
        fastest_variant = max(fitting, key=lambda v: v["speed_tok_s"]) if fitting else None
        star = "\u2605" if _encodable("\u2605") else "*"
        fastest_icon = "\u26a1" if _encodable("\u26a1") else ">>"
        variant_options = []

        for v in variant_rows:
            grade, _note = grade_variant(v, spec["ram_gb"])
            variant_options.append({
                "value": v,
                "name": v["model"],
                "label": menu.variant_label(v, grade, v is fastest_variant, star, fastest_icon),
                "color": GRADE_COLORS[grade] if COLOR_ON else None,
            })

        selected = prompt_choice(menu.QUESTIONS["variant"], variant_options, selected,
                                 menu.grade_legend(star, fastest_icon))

    selected_data_dir = default_data_dir

    if not state["data_dir_specified"]:
        answer = prompt_text(menu.QUESTIONS["dataDir"], default_data_dir)

        if answer.strip() != "":
            selected_data_dir = os.path.abspath(os.path.expanduser(answer.strip()))

    selected_context = default_context

    if not state["context_specified"]:
        selected_context = prompt_choice(menu.QUESTIONS["context"], menu.context_options(),
                                         default_context)

    selected_kv = default_kv

    if not state["kv_specified"]:
        selected_kv = prompt_choice(menu.QUESTIONS["kv"], menu.kv_options(), default_kv)

    selected_port = default_port

    if not state["port_specified"]:
        selected_port = prompt_port(menu.QUESTIONS["port"], default_port)

    selected_isolation = default_isolation

    if state["isolation_specified"]:
        selected_isolation = state["isolation"]
    else:
        selected_isolation = prompt_choice(menu.QUESTIONS["isolation"],
                                           menu.isolation_options(), default_isolation)

    if selected_isolation == "wsl" and not IS_WINDOWS:
        fail("the WSL isolation needs Windows; it runs the container inside Windows Subsystem for Linux.",
             "For the same wall on this system use docker or podman: -Isolation docker or podman")

    calling_shell = get_calling_shell()

    if state["no_alias"]:
        add_alias = False
    else:
        add_alias = prompt_yes_no(menu.QUESTIONS["alias"] % calling_shell, True)

    config = {
        "family": selected["family"],
        "model": selected["model"],
        "context": selected_context,
        "kv": selected_kv,
        "dataDir": selected_data_dir,
        "port": selected_port,
        "isolation": selected_isolation,
    }

    os.makedirs(APP_DIR, exist_ok=True)
    write_text_file(SETUP_PATH, json.dumps(config, indent=2))
    write_ok("setup saved to " + SETUP_PATH)

    if add_alias:
        install_scode_alias(calling_shell)

    return config


SMART_APP_CONTROL_HINT = (
    "Strata's engine (strata.exe) is not code-signed, so Smart App Control blocks it. "
    "Turn it off in Windows Security > App & browser control > Smart App Control settings, "
    "then run this script again."
)


def get_smart_app_control_state():
    if not IS_WINDOWS:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SYSTEM\CurrentControlSet\Control\CI\Policy")
        try:
            value, _ = winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")
        finally:
            winreg.CloseKey(key)
    except OSError:
        return None

    return {0: "off", 1: "on", 2: "evaluation"}.get(int(value))


def get_smart_app_control_blocks(since_epoch):
    if not IS_WINDOWS:
        return []

    tail = re.sub(r"^[A-Za-z]:", "", APP_DIR).rstrip("\\/").replace("\\", "/")

    script = (
        "Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational';"
        "Id=3077;StartTime=%s} -ErrorAction SilentlyContinue | "
        "ForEach-Object { ($_.Message -replace '\\r?\\n',' ') }"
        % ("[datetime]::FromFileTimeUtc(%d)" % int(since_epoch * 10000000 + 116444736000000000))
    )

    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, errors="replace",
        )
        lines = (proc.stdout or "").splitlines()
    except Exception:
        return []

    blocks = []
    for line in lines:
        match = re.search(r"attempted to load (.+?) that did not meet", line)
        if not match:
            continue
        file_path = match.group(1).replace("\\", "/")
        index = file_path.lower().find(tail.lower())
        if index >= 0:
            remainder = file_path[index + len(tail):].strip("/")
            candidate = os.path.join(APP_DIR, remainder) if remainder else APP_DIR
            if candidate not in blocks:
                blocks.append(candidate)

    return blocks


def assert_not_blocked_by_smart_app_control(since_epoch):
    blocked = get_smart_app_control_blocks(since_epoch)

    if blocked:
        fail("Windows Smart App Control blocked %s." % ", ".join(blocked), SMART_APP_CONTROL_HINT)


def _ram_gb():
    if IS_WINDOWS:

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return round(status.ullTotalPhys / (1024 ** 3), 1)

    with open("/proc/meminfo") as handle:
        for line in handle:
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) * 1024 / (1024 ** 3), 1)
    return 0


def _cpu_spec():
    if IS_WINDOWS:
        name = "unknown CPU"
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            try:
                name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
            finally:
                winreg.CloseKey(key)
        except OSError:
            pass
        try:
            avx2 = bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))
        except Exception:
            avx2 = None
        return name, os.cpu_count(), avx2

    name = "unknown CPU"
    cores = None
    flags = ""
    try:
        with open("/proc/cpuinfo") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "model name" and name == "unknown CPU":
                    name = value
                elif key == "cpu cores" and cores is None:
                    cores = get_int_setting(value, None)
                elif key == "flags":
                    flags = value
                    break
    except OSError:
        pass
    avx2 = "avx2" in flags.split() if flags else None
    return name, cores or os.cpu_count(), avx2


def get_system_spec():
    spec = {}

    if IS_WINDOWS:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
            try:
                product = str(winreg.QueryValueEx(key, "ProductName")[0])
                try:
                    display = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
                except OSError:
                    display = ""
            finally:
                winreg.CloseKey(key)
            spec["os"] = (product + (" " + display if display else "")).strip()
        except OSError:
            spec["os"] = "Windows %s" % sys.getwindowsversion().major
    else:
        pretty = ""
        try:
            with open("/etc/os-release") as handle:
                for line in handle:
                    if line.startswith("PRETTY_NAME="):
                        pretty = line.split("=", 1)[1].strip().strip('"')
                        break
        except OSError:
            pass
        spec["os"] = (pretty or "Linux") + " " + os.uname().release

    spec["ram_gb"] = _ram_gb()
    spec["cpu"], spec["cores"], spec["avx2"] = _cpu_spec()
    spec["gpu"] = None

    smi_path = shutil.which("nvidia-smi.exe" if IS_WINDOWS else "nvidia-smi") \
        or shutil.which("nvidia-smi")

    if IS_WINDOWS and not smi_path:
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\\Windows"), "System32", "nvidia-smi.exe")
        if os.path.isfile(candidate):
            smi_path = candidate

    if smi_path:
        rc, rows = run_native(smi_path, [
            "--query-gpu=index,name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ])

        gpus = []
        for row in rows:
            fields = [f.strip() for f in row.split(",")]
            if len(fields) >= 5:
                vram = get_int_setting(fields[2], 0)
                try:
                    compute = float(fields[4])
                except ValueError:
                    compute = 0.0
                if vram:
                    gpus.append({
                        "index": fields[0],
                        "name": fields[1],
                        "vram_mib": vram,
                        "driver": fields[3],
                        "compute_cap": compute,
                    })

        if gpus:
            spec["gpu"] = max(gpus, key=lambda g: g["vram_mib"])

    spec["other_gpus"] = []

    if not spec["gpu"]:
        if IS_WINDOWS:
            _rc, names = run_native("powershell.exe", [
                "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
            spec["other_gpus"] = [n.strip() for n in names if n.strip()]
        else:
            lspci = shutil.which("lspci")
            if lspci:
                _rc, lines = run_native(lspci, [])
                for line in lines:
                    if " VGA" in line or " 3D" in line or " Display" in line:
                        spec["other_gpus"].append(line.split(": ", 1)[-1].strip())

    return spec


# ----------------------------------------------------------------------------------------------------
# Installation detection and setup helpers
# ----------------------------------------------------------------------------------------------------

def venv_python(root):
    return os.path.join(root, ".venv", "Scripts" if IS_WINDOWS else "bin",
                        "python.exe" if IS_WINDOWS else "python")


def test_strata_config(root, tag):
    config = read_json(os.path.join(root, "strata-%s.json" % tag))

    if not config or not config.get("exe"):
        return False

    return os.path.isfile(config["exe"]) and os.path.isfile(venv_python(root))


def test_strata_engine_missing(root, tag):
    config = read_json(os.path.join(root, "strata-%s.json" % tag))

    if not config or not config.get("exe"):
        return False

    return not os.path.isfile(config["exe"])


def delete_strata_engine(root, data_dir):
    deleted = []
    failed = []

    engine_names = ("strata.exe",) if IS_WINDOWS else ("strata",)
    targets = []

    for base in (root, data_dir):
        if not base or not os.path.isdir(base):
            continue
        for name in engine_names:
            for path in glob.glob(os.path.join(base, "**", name), recursive=True):
                if os.path.isfile(path) and path not in targets:
                    targets.append(path)

    for path in targets:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError as exc:
            failed.append("%s (%s)" % (path, exc.strerror or exc))

    return deleted, failed


def read_gguf_string(reader):
    (length,) = struct.unpack("<Q", reader.read(8))

    if length > 1048576:
        raise ValueError("GGUF string length %d is out of range" % length)

    return reader.read(length).decode("utf-8", "replace")


GGUF_VALUE_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}


def read_gguf_value(reader, value_type, depth=0):
    if value_type == 0:
        return reader.read(1)[0]
    if value_type == 1:
        return struct.unpack("<b", reader.read(1))[0]
    if value_type == 2:
        return struct.unpack("<H", reader.read(2))[0]
    if value_type == 3:
        return struct.unpack("<h", reader.read(2))[0]
    if value_type == 4:
        return struct.unpack("<I", reader.read(4))[0]
    if value_type == 5:
        return struct.unpack("<i", reader.read(4))[0]
    if value_type == 6:
        return struct.unpack("<f", reader.read(4))[0]
    if value_type == 7:
        return reader.read(1)[0] != 0
    if value_type == 8:
        return read_gguf_string(reader)
    if value_type == 9:
        (element_type,) = struct.unpack("<I", reader.read(4))
        (count,) = struct.unpack("<Q", reader.read(8))

        if depth >= 2:
            raise ValueError("GGUF array nesting is too deep")

        element_size = GGUF_VALUE_SIZES.get(element_type, 0)

        if element_size > 0:
            reader.seek(count * element_size, 1)
        else:
            for _ in range(count):
                read_gguf_value(reader, element_type, depth + 1)

        return None
    if value_type == 10:
        return struct.unpack("<Q", reader.read(8))[0]
    if value_type == 11:
        return struct.unpack("<q", reader.read(8))[0]
    if value_type == 12:
        return struct.unpack("<d", reader.read(8))[0]

    raise ValueError("unknown GGUF value type %d" % value_type)


def read_gguf_meta(path):
    meta = {}

    try:
        with open(path, "rb") as raw:
            reader = io.BufferedReader(raw)

            if reader.read(4) != b"GGUF":
                return None

            (version,) = struct.unpack("<I", reader.read(4))

            if version < 2 or version > 3:
                return None

            reader.read(8)
            (n_kv,) = struct.unpack("<Q", reader.read(8))

            if n_kv > 1048576:
                return None

            for _ in range(n_kv):
                key = read_gguf_string(reader)
                (value_type,) = struct.unpack("<I", reader.read(4))
                meta[key] = read_gguf_value(reader, value_type)
    except Exception:
        return None

    return meta


def get_gguf_arch(meta):
    if not meta:
        return None

    arch = str(meta.get("general.architecture", "")).strip()

    return arch or None


def get_kv_weights_per_token(meta):
    arch = get_gguf_arch(meta)

    if not arch:
        return None

    layers = get_int_setting(meta.get("%s.block_count" % arch), 0)
    heads = get_int_setting(meta.get("%s.attention.head_count" % arch), 0)
    heads_kv = get_int_setting(meta.get("%s.attention.head_count_kv" % arch), 0)
    embd = get_int_setting(meta.get("%s.embedding_length" % arch), 0)

    if layers <= 0 or heads <= 0 or heads_kv <= 0 or embd <= 0:
        return None

    head_dim = embd // heads

    if head_dim <= 0:
        return None

    interval = get_int_setting(meta.get("%s.full_attention_interval" % arch), 1)

    if interval < 1:
        interval = 1

    full_layers = max(1, layers // interval)

    return 2 * full_layers * heads_kv * head_dim


def get_gguf_trained_context(meta):
    arch = get_gguf_arch(meta)

    if not arch:
        return 0

    return get_int_setting(meta.get("%s.context_length" % arch), 0)


def get_kv_bytes(kv_quant):
    return KV_BYTES_PER_WEIGHT.get(str(kv_quant), KV_BYTES_PER_WEIGHT["int8"])


def get_kv_cache_gb(weights_per_token, tokens, kv_quant):
    if not weights_per_token or tokens <= 0:
        return None

    return round(weights_per_token * tokens * get_kv_bytes(kv_quant) / (1024 ** 3), 1)


def get_config_arg_value(config, name):
    cfg_args = [str(a) for a in (config.get("args") or [])]

    for i, arg in enumerate(cfg_args):
        if arg == name and i + 1 < len(cfg_args):
            return cfg_args[i + 1]

    return None


def get_configured_context(root, tag):
    config = read_json(os.path.join(root, "strata-%s.json" % tag))

    if not config or not config.get("args"):
        return 0

    return get_int_setting(get_config_arg_value(config, "--max-context"), 0)


def model_shard_filter(model, first_shard):
    if first_shard:
        return "*%s*00001-of-*.gguf" % model
    return "*%s*-of-*.gguf" % model


def count_matching(dir_path, pattern):
    return len(glob.glob(os.path.join(dir_path, pattern)))


def find_model_shard(directory, pattern):
    if not directory or not os.path.isdir(directory):
        return None

    return find_matching_file(directory, pattern)


def show_ram_stability(spec, variant, context_tokens, kv_quant, root, tag, data_dir, gguf_dir):
    write_step("Memory stability")

    shard_filter = model_shard_filter(variant["model"], True)
    shard = None

    for search_dir in [gguf_dir, data_dir]:
        shard = find_model_shard(search_dir, shard_filter)

        if shard:
            break

    meta = read_gguf_meta(shard) if shard else None

    weights_per_token = get_kv_weights_per_token(meta)
    trained = get_gguf_trained_context(meta)

    configured = get_configured_context(root, tag)

    if configured <= 0:
        configured = context_tokens

    if configured <= 0:
        configured = trained

    estimated = float(variant["min_ram_gb"])
    kv_gb = None
    kv_baseline_gb = None

    if weights_per_token and configured > 0:
        baseline_context = trained if trained > 0 else configured
        kv_gb = get_kv_cache_gb(weights_per_token, configured, kv_quant)
        kv_baseline_gb = get_kv_cache_gb(weights_per_token, baseline_context, "int8")
        estimated = round(variant["min_ram_gb"] + kv_gb - kv_baseline_gb, 1)

    write_info("this PC has %s GB RAM; %s needs at least %s GB"
               % (spec["ram_gb"], variant["model"], variant["min_ram_gb"]))

    if kv_gb is not None:
        write_info("the %d-token context at %s KV uses about %s GB of it"
                   % (configured, kv_quant, kv_gb))
    else:
        write_info("the KV cache size could not be measured; the estimate uses the published minimum")

    headroom = round(spec["ram_gb"] - estimated, 1)

    write_info("estimated peak use at full context: about %s GB" % estimated)

    if headroom >= COMFORTABLE_HEADROOM_GB:
        write_ok("headroom of about %s GB: this setup should run comfortably" % headroom)
    elif headroom >= TIGHT_HEADROOM_GB:
        write_warn("headroom of about %s GB: close other large apps while the server runs" % headroom)
    else:
        _emit(RED, "    warn  headroom of about %s GB: the server may run out of RAM and crash" % headroom)
        _emit(RED, "          lower the context, pick q4_0 KV, or choose a smaller variant")

    if headroom < COMFORTABLE_HEADROOM_GB and weights_per_token and kv_baseline_gb:
        bytes_per_weight = get_kv_bytes(kv_quant)

        fit_tokens = int(
            ((spec["ram_gb"] - COMFORTABLE_HEADROOM_GB - variant["min_ram_gb"] + kv_baseline_gb)
             * (1024 ** 3)) // (weights_per_token * bytes_per_weight))

        if 0 < fit_tokens < configured:
            fit_tokens = (fit_tokens // 1024) * 1024

            write_info("a context of %d tokens or less keeps about %d GB of headroom"
                       % (fit_tokens, COMFORTABLE_HEADROOM_GB))


def path_is_under(path, parent):
    path = os.path.normcase(os.path.abspath(path))
    parent = os.path.normcase(os.path.abspath(parent))

    if not parent.endswith(os.sep):
        parent += os.sep

    return path.startswith(parent)


def test_setup_drift(root, tag, context_tokens, kv_quant, data_dir):
    config = read_json(os.path.join(root, "strata-%s.json" % tag))

    if not config or not config.get("args"):
        return False

    current_context = get_config_arg_value(config, "--max-context")
    current_kv = get_config_arg_value(config, "--kv")

    if context_tokens and str(current_context) != str(context_tokens):
        return True

    if kv_quant and str(current_kv or "").lower() != str(kv_quant).lower():
        return True

    # the model folder is baked into the engine's model paths; a different
    # folder (or model files that went missing) needs a reconfigure
    native = get_config_arg_value(config, "--native")

    if native:
        if not os.path.isfile(native) or not path_is_under(native, data_dir):
            return True

    return False


def get_strata_version(root, state):
    match = re.match(r"^strata-(v[\d.]+)$", os.path.basename(root))

    if match:
        return match.group(1)

    if state and state.get("StrataRoot") == root and state.get("StrataVersion"):
        return state["StrataVersion"]

    return "unknown"


def install_strata_code(version):
    destination = os.path.join(APP_DIR, "strata-%s" % version)

    if os.path.isfile(os.path.join(destination, "setup.py")):
        return destination

    zip_path = os.path.join(DOWNLOAD_DIR, "strata-%s.zip" % version)

    try:
        download_file(
            "https://github.com/Niko1221/Strata/archive/refs/tags/%s.zip" % version, zip_path)
    except Exception as exc:
        fail("could not download Strata %s (%s)" % (version, exc),
             "Check network access and run the script again.")

    shutil.rmtree(UNPACK_DIR, ignore_errors=True)

    try:
        extract_archive(zip_path, UNPACK_DIR)
    except Exception as exc:
        shutil.rmtree(UNPACK_DIR, ignore_errors=True)
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        fail("the Strata archive is damaged (%s); it was removed." % exc,
             "Run the script again to download a fresh copy.")

    inner = next((d for d in sorted(glob.glob(os.path.join(UNPACK_DIR, "*"))) if os.path.isdir(d)), None)

    marker = ("START-HERE.bat" if IS_WINDOWS else "setup.sh")

    if (not inner
            or not os.path.isfile(os.path.join(inner, "setup.py"))
            or not os.path.isfile(os.path.join(inner, marker))):
        shutil.rmtree(UNPACK_DIR, ignore_errors=True)
        if os.path.isfile(zip_path):
            os.remove(zip_path)
        fail("the Strata %s archive does not contain expected setup files." % version,
             "Run the script again. If it repeats, verify the Strata release.")

    shutil.rmtree(destination, ignore_errors=True)

    shutil.move(inner, destination)

    shutil.rmtree(UNPACK_DIR, ignore_errors=True)
    if os.path.isfile(zip_path):
        os.remove(zip_path)

    return destination


def invoke_strata_setup(root, arguments):
    if IS_WINDOWS:
        command = ["cmd.exe", "/d", "/c", os.path.join(root, "START-HERE.bat")] + arguments
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP") else 0
        popen_kwargs = dict(creationflags=creationflags)
    else:
        command = ["sh", os.path.join(root, "setup.sh")] + arguments
        popen_kwargs = dict(start_new_session=True)

    # the offline dead proxy must not block the engine and model downloads;
    # isolation resumes when OpenCode starts after this step
    popen_kwargs["env"] = without_sinkhole_proxy(os.environ)

    process = subprocess.Popen(
        command,
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kwargs
    )

    last_progress = 0.0

    try:
        for line in iter(process.stdout.readline, ""):
            line = line.rstrip("\r\n")

            if re.search(r"has to be compiled|Install them now\?|Windows will ask for permission", line):
                if IS_WINDOWS:
                    subprocess.run(["taskkill.exe", "/T", "/F", "/PID", str(process.pid)],
                                   capture_output=True)
                else:
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    except OSError:
                        process.kill()

                fail("Strata has no ready-made engine for this GPU and is requesting build tools "
                     "that require administrator rights.",
                     "On a system where you have administrator rights, run %s manually."
                     % os.path.join(root, "START-HERE.bat" if IS_WINDOWS else "setup.sh"))

            if re.search(r"\d+%\|", line) or re.search(r"[\d.]+\s*[MG]B/s", line):
                if time.time() - last_progress < 5:
                    continue
                last_progress = time.time()

            if line.strip():
                _emit(None, "    | " + line)

        process.wait()

        return process.returncode
    finally:
        try:
            process.stdout.close()
        except Exception:
            pass


def get_setup_args(variant, directory, server_port, context_tokens, kv_quant, gguf_dir):
    arguments = [
        "--family", variant["family"],
        "--model", variant["model"],
        "--vision", "no",
        "--data-dir", directory,
        "--port", str(server_port),
        "--no-browser",
        "--no-start",
        "--yes",
    ]

    if context_tokens:
        arguments += ["--context", str(context_tokens)]

    if kv_quant:
        arguments += ["--kv", kv_quant]

    if gguf_dir:
        arguments += ["--gguf-dir", gguf_dir]

    return arguments


def invoke_model_setup(root, variant, directory, server_port, context_tokens, kv_quant, gguf_dir, tag):
    os.makedirs(directory, exist_ok=True)

    exit_code = invoke_strata_setup(
        root, get_setup_args(variant, directory, server_port, context_tokens, kv_quant, gguf_dir))

    return exit_code, test_strata_config(root, tag)


# ----------------------------------------------------------------------------------------------------
# Local (host-side) isolation policies
# ----------------------------------------------------------------------------------------------------

OFFLINE_PROXY_URL = "http://127.0.0.1:9"
OFFLINE_NO_PROXY = "127.0.0.1,localhost"

SINKHOLE_PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def without_sinkhole_proxy(env):
    # Strata's own setup downloads the engine and the model, so isolation must
    # never reach it, not even through an inherited environment (e.g. scode
    # started from inside an isolated OpenCode session). Only the dead-proxy
    # sinkhole values are dropped; a real corporate proxy stays in place.
    return {key: value for key, value in env.items()
            if not (key.upper() in SINKHOLE_PROXY_NAMES and value == OFFLINE_PROXY_URL)}

OFFLINE_DENY_COMMANDS = (
    "curl", "curl.exe", "wget", "wget.exe", "ftp", "tftp", "telnet", "nc", "ncat", "socat",
    "ssh", "scp", "sftp", "rsync",
    "Invoke-WebRequest", "invoke-webrequest", "Invoke-RestMethod", "invoke-restmethod",
    "iwr", "irm",
    "git push", "git pull", "git fetch", "git clone", "git ls-remote", "git submodule update",
    "npm install", "npm i", "npm add", "npm ci", "npm update", "npx",
    "yarn install", "yarn add", "yarn upgrade", "pnpm install", "pnpm add", "pnpm update",
    "pip install", "pip3 install", "pip download", "uv add", "uv sync", "uv pip install",
    "poetry install", "poetry add", "poetry update", "conda install", "conda create",
    "choco install", "winget install", "apt install", "apt-get install", "dotnet tool install",
)


def offline_permission_block():
    bash_rules = {"*": "allow"}

    for command in OFFLINE_DENY_COMMANDS:
        bash_rules[command] = "deny"
        bash_rules[command + " *"] = "deny"

    return {"webfetch": "deny", "websearch": "deny", "bash": bash_rules}


def set_offline_proxy_sinkhole():
    saved = {}

    for group in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        value = OFFLINE_NO_PROXY if group == "NO_PROXY" else OFFLINE_PROXY_URL

        existing = {}
        for key in list(os.environ):
            if key.upper() == group:
                existing[key] = os.environ[key]
                del os.environ[key]

        saved[group] = existing
        os.environ[group] = value
        os.environ[group.lower()] = value

    return saved


def clear_offline_proxy_sinkhole(saved):
    for group, existing in saved.items():
        for key in list(os.environ):
            if key.upper() == group.upper():
                del os.environ[key]
        for key, value in existing.items():
            os.environ[key] = value


# ----------------------------------------------------------------------------------------------------
# Container sandbox (linux image used by both the docker and the podman engine)
# ----------------------------------------------------------------------------------------------------

# tools baked into the sandbox image; ripgrep is absent because the linux musl
# rg binary is copied in below, the .NET SDKs are absent because debian bookworm
# carries no dotnet packages, and network utilities are deliberately absent so
# nothing inside the container can probe or reach a network
SANDBOX_APT_PACKAGES = [
    # version control and search
    "git",
    "tree",
    # python
    "python3",
    "python3-pip",
    "python3-venv",
    "python3-dev",
    # build tools (needed for packages with native extensions)
    "gcc",
    "g++",
    "make",
    "cmake",
    "ninja-build",
    "pkgconf",
    # C/C++ extended toolchain
    "clang",
    "clang-format",
    "clang-tidy",
    "cppcheck",
    "gdb",
    "libssl-dev",
    "libffi-dev",
    # data and files
    "jq",
    "sqlite3",
    "unzip",
    "zip",
    "xxd",
    # media and documents
    "ffmpeg",
    "imagemagick",
    "poppler-utils",
    # process and debug
    "procps",
    "lsof",
    "strace",
    "shellcheck",
]

SANDBOX_DOCKERFILE = (
    "FROM debian:bookworm-slim\n"
    "RUN useradd -m sandbox && apt-get update \\\n"
    "    && apt-get install -y --no-install-recommends ca-certificates wget \\\n"
    + "".join("        %s \\\n" % pkg for pkg in SANDBOX_APT_PACKAGES) +
    "    && rm -rf /var/lib/apt/lists/*\n"
    "COPY opencode /usr/local/bin/opencode\n"
    "COPY rg /usr/local/bin/rg\n"
    "ENV HOME=/home/sandbox\n"
    "USER sandbox\n"
    "WORKDIR /workspace\n"
    "ENTRYPOINT [\"opencode\"]\n"
)


# ----------------------------------------------------------------------------------------------------
# Container sandbox: docker and podman speak the same CLI, so one code path drives both engines;
# a backend entry carries the exe name, a display label, how to reach host.docker.internal, and
# the startup hint shown when the engine is not up.
# ----------------------------------------------------------------------------------------------------

CONTAINER_BACKENDS = {
    "docker": {"exe": "docker", "label": "Docker", "host_gateway": not IS_WINDOWS,
               "hint": "Start Docker Desktop (it is installed by an administrator) and run the script again"},
    "podman": {"exe": "podman", "label": "Podman", "host_gateway": True,
               "hint": "start Podman first: on Windows run 'podman machine start'; "
                       "on Linux start the podman service"},
}


def container_ready(backend):
    tool = shutil.which(backend["exe"])

    if not tool:
        return None, "the %s command was not found" % backend["exe"]

    try:
        proc = subprocess.run([tool, "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, errors="replace")
    except Exception as exc:
        return None, str(exc)

    if proc.returncode:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return None, detail[0] if detail else "the %s service is not running" % backend["exe"]

    try:
        os_proc = subprocess.run([tool, "info", "--format", "{{.OSType}}"],
                                 capture_output=True, text=True, errors="replace")
    except Exception:
        os_proc = None

    os_type = ((os_proc.stdout if os_proc else "") or "").strip().lower()

    if os_type and os_type != "linux":
        return None, ("%s is running in Windows container mode, but the sandbox image is "
                      "linux-based; switch %s to Linux containers"
                      % (backend["exe"], backend["label"]))

    return tool, (proc.stdout or "").strip()


def fail_no_container(backend, detail):
    fail("the %s isolation is on but %s is not usable: %s"
         % (backend["label"], backend["label"], detail),
         "%s, or turn isolation off with: scode -Setup -Isolation none" % backend["hint"])


SANDBOX_RESERVED_MOUNTS = {
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt",
    "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
}


def sandbox_mount_path(project_dir):
    name = os.path.basename((project_dir or "").rstrip("\\/"))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")

    if not name:
        return "/workspace"

    return "/projects/%s" % name if name.lower() in SANDBOX_RESERVED_MOUNTS else "/%s" % name


def host_gateway_arguments(backend):
    """engines without a built-in host.docker.internal inside containers get the host-gateway
    mapping (podman everywhere; docker only on plain Linux engines, Docker Desktop is native)."""
    if backend.get("host_gateway"):
        return ["--add-host", "host.docker.internal:host-gateway"]
    return []


def sandbox_network_is_internal(tool):
    proc = subprocess.run([tool, "network", "inspect", SANDBOX_NETWORK],
                          capture_output=True, text=True, errors="replace")

    if proc.returncode:
        return False

    try:
        info = json.loads(proc.stdout or "[]")
    except ValueError:
        return False

    if isinstance(info, dict):
        info = [info]

    return bool(info) and bool(info[0].get("internal"))


def ensure_sandbox_network(tool):
    if sandbox_network_is_internal(tool):
        return

    rc, _lines = run_native(tool, ["network", "inspect", SANDBOX_NETWORK])

    if rc == 0:
        rc, container_ids = run_native(tool, ["ps", "-aq", "--filter", "network=%s" % SANDBOX_NETWORK])

        if container_ids:
            run_native(tool, ["rm", "-f"] + container_ids)

        run_native(tool, ["network", "rm", SANDBOX_NETWORK])

    proc = subprocess.run([tool, "network", "create", "--internal", SANDBOX_NETWORK],
                          capture_output=True, text=True, errors="replace")

    if proc.returncode and not sandbox_network_is_internal(tool):
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        fail("could not create the internal sandbox network (%s)." % (detail[0] if detail else "unknown error"),
             "Run '%s network create --internal %s' manually and try again."
             % (os.path.basename(tool), SANDBOX_NETWORK))


def sandbox_image_tag(version):
    return "%s:%s-%s" % (SANDBOX_IMAGE, version or "latest", SANDBOX_IMAGE_REVISION)


def sandbox_opencode_version():
    state_json = read_json(STATE_PATH)

    if isinstance(state_json, dict) and state_json.get("OpenCodeVersion"):
        return str(state_json["OpenCodeVersion"])

    release = get_latest_release("anomalyco/opencode")

    return release["tag_name"] if release else ""


def stage_sandbox_binaries(version, avx2):
    build = os.path.join(SANDBOX_DIR, "build")
    os.makedirs(build, exist_ok=True)

    if not os.path.isfile(os.path.join(build, "opencode")):
        asset = "opencode-linux-x64-baseline.tar.gz" if avx2 is False \
            else "opencode-linux-x64.tar.gz"
        url = "https://github.com/anomalyco/opencode/releases/download/%s/%s" % (version, asset)
        staged = get_downloaded_file(url, os.path.join(DOWNLOAD_DIR, asset),
                                     os.path.join(SANDBOX_DIR, "unpack-opencode"), "opencode")
        shutil.move(staged, os.path.join(build, "opencode"))
        shutil.rmtree(os.path.join(SANDBOX_DIR, "unpack-opencode"), ignore_errors=True)

    if not os.path.isfile(os.path.join(build, "rg")):
        release = get_latest_release("BurntSushi/ripgrep")
        asset = next((a for a in (release or {}).get("assets", [])
                      if fnmatch.fnmatch(a.get("name", ""), "ripgrep-*-x86_64-unknown-linux-musl.tar.gz")),
                     None)

        if not asset:
            fail("the ripgrep release does not contain a linux-musl build.",
                 "Check network access and run the script again.")

        staged = get_downloaded_file(asset["browser_download_url"],
                                     os.path.join(DOWNLOAD_DIR, asset["name"]),
                                     os.path.join(SANDBOX_DIR, "unpack-rg"), "rg")
        shutil.move(staged, os.path.join(build, "rg"))
        shutil.rmtree(os.path.join(SANDBOX_DIR, "unpack-rg"), ignore_errors=True)

    write_text_file(os.path.join(build, "Dockerfile"), SANDBOX_DOCKERFILE)

    return build


def prune_old_sandbox_images(tool, keep_tag):
    rc, tags = run_native(tool, ["image", "ls", "--filter", "reference=%s" % SANDBOX_IMAGE,
                                 "--format", "{{.Repository}}:{{.Tag}}"])

    for old_tag in tags:
        if old_tag and old_tag != keep_tag:
            run_native(tool, ["rmi", old_tag])


def ensure_sandbox_image(backend, tool, avx2):
    version = sandbox_opencode_version()
    tag = sandbox_image_tag(version)

    rc, _lines = run_native(tool, ["image", "inspect", tag])

    if rc == 0:
        write_skip("sandbox image is already built (%s)" % tag)
        prune_old_sandbox_images(tool, tag)
        return tag

    write_info("staging the linux OpenCode and ripgrep binaries for the sandbox image")

    try:
        build = stage_sandbox_binaries(version, avx2)
    except Exception as exc:
        fail("the sandbox image inputs could not be prepared (%s)." % exc,
             "Check network access and run the script again.")

    write_info("building the sandbox image %s "
               "(the first build downloads the debian base and the toolchain, so it can take a few minutes)" % tag)
    proc = subprocess.run([tool, "build", "-t", tag, build],
                          capture_output=True, text=True, errors="replace")

    if proc.returncode:
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-4:]
        fail("the sandbox image build failed (%s)." % " | ".join(tail),
             "Check that %s is running and try again." % backend["hint"].split(";")[0].lower())

    prune_old_sandbox_images(tool, tag)

    return tag


def sandbox_models_reachable(backend, tool, tag, url):
    rc, _lines = run_native(tool, [
        "run", "--rm", "--network", SANDBOX_NETWORK] + host_gateway_arguments(backend) +
        ["--entrypoint", "sh", tag, "-c", "wget -q -O - %s" % url])

    return rc == 0


def start_sandbox_relay(backend, tool, port):
    run_native(tool, ["rm", "-f", SANDBOX_RELAY])

    rc, lines = run_native(tool, [
        "run", "-d", "--rm", "--name", SANDBOX_RELAY] + host_gateway_arguments(backend) +
        [SANDBOX_RELAY_IMAGE,
         "tcp-listen:8080,fork,reuseaddr", "tcp-connect:host.docker.internal:%d" % port])

    if rc != 0:
        return False

    rc, _lines = run_native(tool, ["network", "connect", SANDBOX_NETWORK, SANDBOX_RELAY])

    if rc != 0:
        run_native(tool, ["rm", "-f", SANDBOX_RELAY])
        return False

    return True


def sandbox_config_content(open_code_json, base_url, port):
    config = json.loads(open_code_json)

    try:
        config["provider"]["strata"]["options"]["baseURL"] = "%s:%d/v1" % (base_url, port)
    except (KeyError, TypeError):
        pass

    return json.dumps(config, indent=2)


def run_opencode_sandbox(isolation, tag, port, open_code_json):
    backend = CONTAINER_BACKENDS[isolation]
    tool, detail = container_ready(backend)

    if not tool:
        fail_no_container(backend, detail)

    rc, running = run_native(tool, ["ps", "-q", "--filter", "name=^%s$" % SANDBOX_CONTAINER])

    if running:
        fail("another sandboxed OpenCode is already running in container '%s'." % SANDBOX_CONTAINER,
             "Close that session's window first; sandboxed OpenCode instances share one container name.")

    run_native(tool, ["rm", "-f", SANDBOX_CONTAINER])

    if IS_WINDOWS and PROJECT_DIR.startswith("\\\\"):
        fail("the %s sandbox cannot mount the current folder: %s" % (backend["label"], PROJECT_DIR),
             "%s cannot bind-mount network shares; cd to a local drive folder and run again."
             % backend["label"])

    relay_started = False
    base_url = "http://host.docker.internal"
    probe_port = port

    if not sandbox_models_reachable(backend, tool, tag, "http://host.docker.internal:%d/v1/models" % port):
        write_info("the internal network resolves no outside names (normal); "
                   "routing the model connection through the socat relay")

        if not start_sandbox_relay(backend, tool, port):
            fail("the sandbox relay container could not be started.",
                 "Check that %s can pull %s and run the script again, "
                 "or turn isolation off with: scode -Setup -Isolation none"
                 % (backend["exe"], SANDBOX_RELAY_IMAGE))

        relay_started = True
        base_url = "http://%s" % SANDBOX_RELAY
        probe_port = 8080

        if not sandbox_models_reachable(backend, tool, tag, "http://%s:8080/v1/models" % SANDBOX_RELAY):
            run_native(tool, ["rm", "-f", SANDBOX_RELAY])
            fail("the Strata server refused the sandboxed connection through the relay.",
                 "A server started before the sandbox was turned on listens only on 127.0.0.1 and lacks the "
                 "relay host allowance; run: scode -Stop, then run this script again.")

    sandbox_json = sandbox_config_content(open_code_json, base_url, probe_port)

    no_proxy = "127.0.0.1,localhost,host.docker.internal,%s" % SANDBOX_RELAY

    mount = sandbox_mount_path(PROJECT_DIR)
    arguments = [
        tool, "run", "--rm", "-it", "--name", SANDBOX_CONTAINER,
        "--network", SANDBOX_NETWORK,
        "--hostname", "strata-sandbox",
        "-v", "%s:%s" % (PROJECT_DIR, mount),
        "-w", mount,
        "-e", "OPENCODE_CONFIG_CONTENT=" + sandbox_json,
        "-e", "HTTP_PROXY=" + OFFLINE_PROXY_URL,
        "-e", "HTTPS_PROXY=" + OFFLINE_PROXY_URL,
        "-e", "http_proxy=" + OFFLINE_PROXY_URL,
        "-e", "https_proxy=" + OFFLINE_PROXY_URL,
        "-e", "NO_PROXY=" + no_proxy,
        "-e", "no_proxy=" + no_proxy,
    ]

    arguments += host_gateway_arguments(backend)

    if IS_WINDOWS:
        arguments += ["-v", "%s:/home/sandbox" % SANDBOX_HOME_VOLUME]
    else:
        # on Linux the container must run as the host user or the mounted project
        # files are not writable; each uid gets its own home volume for sessions
        uid_gid = "%d:%d" % (os.getuid(), os.getgid())
        home_volume = "%s-%d" % (SANDBOX_HOME_VOLUME, os.getuid())
        run_native(tool, ["run", "--rm", "--user", "0:0", "-v", "%s:/home/sandbox" % home_volume,
                          "--entrypoint", "chown", tag, "-R", uid_gid, "/home/sandbox"])
        arguments += ["--user", uid_gid, "-v", "%s:/home/sandbox" % home_volume]

    arguments += [tag]

    write_info("project folder mounted at %s (read-write)" % mount)

    try:
        subprocess.run(arguments)
    finally:
        reset_terminal_mouse()

        if relay_started:
            run_native(tool, ["rm", "-f", SANDBOX_RELAY])


# ----------------------------------------------------------------------------------------------------
# WSL sandbox: the same wall without any container engine. systemd-nspawn (a systemd component)
# runs the same Debian rootfs inside a systemd-enabled WSL 2 distro. The container is started with
# --private-network, so it has only the loopback interface: no routes, no DNS, nothing to configure
# or firewall. Its single path to the model is a unix socket directory bind-mounted into it; inside
# the container socat bridges the model port to that socket, and a python relay on the WSL host
# forwards it to the Windows Strata endpoint (WSL mirrored networking shares the Windows loopback).
# Two quirks of systemd 255 on the WSL kernel are handled here: its unified-cgroup mount probe fails
# on this kernel, so nspawn is started through a wrapper that exports
# SYSTEMD_NSPAWN_UNIFIED_HIERARCHY=1, and its short -b option swallows the container command, so
# only the long --bind= form is ever passed.
# ----------------------------------------------------------------------------------------------------

WSL_HOST_PACKAGES = ("systemd-container", "debootstrap", "python3")

WSL_RELAY_PY = '''
import os
import socket
import socketserver
import sys
import threading

SOCK_DIR = sys.argv[1] if len(sys.argv) > 1 else "/run/strata-coder-wsl"
MODEL_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
SOCK_PATH = os.path.join(SOCK_DIR, "relay.sock")


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        # mirrored networking shares the Windows loopback, so the Strata endpoint is 127.0.0.1
        try:
            up = socket.create_connection(("127.0.0.1", MODEL_PORT), timeout=10)
        except Exception:
            return

        def pump(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            try:
                dst.shutdown(socket.SHUT_WR)
            except Exception:
                pass

        thread = threading.Thread(target=pump, args=(up, self.request))
        thread.daemon = True
        thread.start()
        pump(self.request, up)
        thread.join(timeout=3)


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


try:
    os.unlink(SOCK_PATH)
except FileNotFoundError:
    pass

server = Server(SOCK_PATH, Handler)
server.serve_forever()
'''

# wsl.exe re-parses the command line it forwards to a distro (and can expand $ and split on
# spaces), so the project path and the JSON env block never appear as arguments: the wrapper is a
# pushed script that base64-decodes the extra nspawn options, one per line, from WSL_EXTRAS_PATH and
# assembles them. Only the container command (launcher path, numeric port, sanitized mount) stays
# on the wsl.exe command line.
WSL_NSPAWN_WRAPPER = (
    "#!/bin/bash\n"
    "export SYSTEMD_NSPAWN_UNIFIED_HIERARCHY=1\n"
    "base=(--directory=%(rootfs)s --machine=strata-coder --register=no --private-network "
    "--bind=%(relay)s:%(relay)s)\n"
    "extras=()\n"
    "if [ -s '%(extras)s' ]; then\n"
    "  while IFS= read -r line; do extras+=(\"$(printf '%%s' \"$line\" | base64 -d)\"); done < '%(extras)s'\n"
    "fi\n"
    "exec systemd-nspawn \"${base[@]}\" \"${extras[@]}\" -- \"$@\"\n"
)

# wsl.exe re-parses the command line it hands to a distro and expands $ variables on the way in,
# so everything a container command needs lives in these pushed files, never in an inline
# "sh -c" argument. The port arrives as argument 1.

# argument 2 is the mounted project folder: systemd-nspawn has no --working-directory option and
# its --chdir= needs systemd 257, so the working directory is set inside this script instead.
WSL_LAUNCHER_SH = '''#!/bin/sh
cd "$2" || exit 1
socat TCP-LISTEN:"$1",fork,reuseaddr UNIX-CONNECT:/run/strata-coder-wsl/relay.sock &
SPID=$!
sleep 1
runuser -u sandbox -- env HOME=/home/sandbox opencode
RC=$?
kill $SPID 2>/dev/null
exit $RC
'''

WSL_PROBE_SH = '''#!/bin/sh
socat TCP-LISTEN:"$1",fork,reuseaddr UNIX-CONNECT:/run/strata-coder-wsl/relay.sock &
SPID=$!
sleep 1
python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$1/v1/models', timeout=10).read(64)"
RC=$?
kill $SPID 2>/dev/null
exit $RC
'''


def wsl_exe():
    return shutil.which("wsl") or shutil.which("wsl.exe")


def wsl_wslpath(win_path):
    drive, rest = os.path.splitdrive(os.path.abspath(win_path))
    return "/mnt/%s%s" % (drive[0].lower(), rest.replace("\\", "/"))


def wsl_admin_lines(wsl, args_list):
    """wsl.exe management output (-l, --status) is UTF-16; command output inside a distro is UTF-8."""
    proc = subprocess.run([wsl] + list(args_list), capture_output=True)
    raw = proc.stdout or b""

    try:
        text = raw.decode("utf-16-le", errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")

    lines = [line.replace("\ufeff", "").strip() for line in text.splitlines()]
    return proc.returncode, [line for line in lines if line]


def wsl_run_script(wsl, distro, script, tag, as_root=True):
    path = os.path.join(WSL_DIR, "%s.sh" % tag)
    write_text_file(path, script)
    command = [wsl, "-d", distro] + (["-u", "root"] if as_root else []) + \
              ["--", "bash", "-c", "tr -d '\\r' < '%s' | bash -s" % wsl_wslpath(path)]
    return subprocess.run(command, capture_output=True, text=True, errors="replace")


def wsl_tail(proc):
    return " | ".join((((proc.stderr or "") + (proc.stdout or "")).strip().splitlines() or [""])[-3:])


def wsl_ready():
    """Locate a usable WSL 2 distro: the default one (or STRATA_WSL_DISTRO), running WSL 2 with
    systemd as init. Returns a (handle, detail) pair like container_ready."""
    wsl = wsl_exe()

    if not wsl:
        return None, "the wsl command was not found (install WSL on Windows with: wsl --install)"

    rc, lines = wsl_admin_lines(wsl, ["-l", "-v"])

    if rc or len(lines) < 2:
        return None, "no WSL distribution is installed (install one with: wsl --install -d Ubuntu)"

    distros = {}
    default_name = ""

    for line in lines[1:]:
        parts = line.split()

        if len(parts) < 3:
            continue

        name_parts = parts[:-2]

        if name_parts and name_parts[0] == "*":
            name_parts = name_parts[1:]
            default_name = " ".join(name_parts)

        if name_parts:
            distros[" ".join(name_parts)] = parts[-1]

    names = sorted(distros)

    if not names:
        return None, "no usable WSL distribution was found in: wsl -l -v"

    pick = (os.environ.get(WSL_DISTRO_ENV) or "").strip()

    if pick and pick not in distros:
        return None, ("the distro named by %s (%s) is not installed; installed: %s"
                      % (WSL_DISTRO_ENV, pick, ", ".join(names)))

    distro = pick or default_name

    if not distro:
        return None, "could not tell which WSL distribution is the default (set %s to pick one)" % WSL_DISTRO_ENV

    if distros.get(distro) != "2":
        return None, ("%s runs WSL 1; the sandbox needs WSL 2 (convert with: wsl --set-version \"%s\" 2)"
                      % (distro, distro))

    proc = subprocess.run([wsl, "-d", distro, "--", "ps", "-p", "1", "-o", "comm="],
                          capture_output=True, text=True, errors="replace")

    if (proc.stdout or "").strip() != "systemd":
        return None, ("%s does not run systemd as init; the sandbox needs it (add systemd=true to "
                      "/etc/wsl.conf inside %s, then run: wsl --shutdown)" % (distro, distro))

    return {"wsl": wsl, "distro": distro}, "%s (WSL %s)" % (distro, distros.get(distro))


def fail_no_wsl(detail):
    fail("the WSL isolation is on but WSL is not usable: %s" % detail,
         "Fix that first (see above), or turn isolation off with: scode -Setup -Isolation none")


def wsl_is_mirrored(wsl, distro):
    proc = wsl_run_script(wsl, distro, "ip addr show lo | grep -q 10.255.255.254", "mirror", as_root=False)
    return proc.returncode == 0


def ensure_wsl_packages(wsl, distro):
    script = ("missing=''; for p in %s; do dpkg -s \"$p\" >/dev/null 2>&1 || missing=\"$missing $p\"; done; "
              "if [ -n \"$missing\" ]; then export DEBIAN_FRONTEND=noninteractive; "
              "apt-get update -qq || true; apt-get install -y -qq $missing || true; fi; "
              "for p in $missing; do dpkg -s \"$p\" >/dev/null 2>&1 || { echo \"still missing: $p\"; exit 1; }; done"
              % " ".join(WSL_HOST_PACKAGES))
    proc = wsl_run_script(wsl, distro, script, "packages")

    if proc.returncode:
        fail("the WSL sandbox tools (systemd-container, debootstrap, python3) are not installed "
             "in %s and could not be installed (%s)." % (distro, wsl_tail(proc) or "unknown error"),
             "Install them by hand inside %s with: sudo apt install systemd-container debootstrap python3 "
             "and run the script again." % distro)


def wsl_rootfs_stamp(wsl, distro):
    proc = wsl_run_script(wsl, distro, "cat '%s/.strata-stamp' 2>/dev/null || true" % WSL_ROOTFS, "stamp")
    return (proc.stdout or "").strip()


def ensure_wsl_rootfs(wsl, distro, avx2):
    version = sandbox_opencode_version()
    stamp = "%s-%s" % (version, SANDBOX_IMAGE_REVISION)

    if wsl_rootfs_stamp(wsl, distro) == stamp:
        write_skip("WSL sandbox rootfs is already built (opencode %s)" % version)
        return

    write_info("staging the linux OpenCode and ripgrep binaries for the WSL sandbox")

    try:
        build = stage_sandbox_binaries(version, avx2)
    except Exception as exc:
        fail("the WSL sandbox inputs could not be prepared (%s)." % exc,
             "Check network access and run the script again.")

    stage = wsl_wslpath(build)
    packages = " ".join(["ca-certificates", "wget", "socat"] + SANDBOX_APT_PACKAGES)

    script = (
        "set -e\n"
        "if [ \"$(cat '%(rootfs)s/.strata-stamp' 2>/dev/null || true)\" != \"%(stamp)s\" ]; then\n"
        "  rm -rf '%(rootfs)s'\n"
        "fi\n"
        "if [ ! -x '%(rootfs)s/bin/sh' ]; then\n"
        "  debootstrap --variant=minbase --include=socat,ca-certificates %(suite)s '%(rootfs)s' %(mirror)s\n"
        "fi\n"
        "export DEBIAN_FRONTEND=noninteractive\n"
        "chroot '%(rootfs)s' apt-get update -qq\n"
        "chroot '%(rootfs)s' apt-get install -y -qq --no-install-recommends %(packages)s\n"
        "id=$(chroot '%(rootfs)s' id -u %(user)s 2>/dev/null || echo '')\n"
        "if [ -z \"$id\" ]; then chroot '%(rootfs)s' useradd -m %(user)s; fi\n"
        "cp '%(stage)s/opencode' '%(rootfs)s/usr/local/bin/opencode'\n"
        "cp '%(stage)s/rg' '%(rootfs)s/usr/local/bin/rg'\n"
        "chmod +x '%(rootfs)s/usr/local/bin/opencode' '%(rootfs)s/usr/local/bin/rg'\n"
        "mkdir -p '%(home)s'\n"
        "uid=$(chroot '%(rootfs)s' id -u %(user)s)\n"
        "chown -R \"$uid:$uid\" '%(home)s'\n"
        "echo '%(stamp)s' > '%(rootfs)s/.strata-stamp'\n"
    ) % {"rootfs": WSL_ROOTFS, "stamp": stamp, "suite": WSL_DEBIAN_SUITE, "mirror": WSL_DEBIAN_MIRROR,
         "packages": packages, "user": WSL_SANDBOX_USER, "stage": stage, "home": WSL_HOME}

    write_info("building the WSL sandbox rootfs in %s "
               "(the first build downloads the debian base and the toolchain, so it can take a few minutes)"
               % WSL_ROOTFS)
    proc = wsl_run_script(wsl, distro, script, "rootfs")

    if proc.returncode:
        fail("the WSL sandbox rootfs build failed (%s)." % (wsl_tail(proc) or "unknown error"),
             "Check network access inside %s (try: wsl -d %s) and run the script again." % (distro, distro))

    write_ok("WSL sandbox rootfs built (opencode %s)" % version)


def wsl_push_runtime_files(wsl, distro):
    relay_path = os.path.join(WSL_DIR, "relay.py")
    write_text_file(relay_path, WSL_RELAY_PY)
    launcher_path = os.path.join(WSL_DIR, "launcher.sh")
    write_text_file(launcher_path, WSL_LAUNCHER_SH)
    probe_path = os.path.join(WSL_DIR, "probe.sh")
    write_text_file(probe_path, WSL_PROBE_SH)
    wrapper = WSL_NSPAWN_WRAPPER % {"rootfs": WSL_ROOTFS, "relay": WSL_RELAY_DIR,
                                    "extras": WSL_EXTRAS_PATH}
    wrapper_path = os.path.join(WSL_DIR, "nspawn-wrapper.sh")
    write_text_file(wrapper_path, wrapper)

    script = (
        "set -e\n"
        "tr -d '\\r' < '%(relay)s' > /tmp/strata-wsl-relay.py\n"
        "tr -d '\\r' < '%(wrapper)s' > '%(nspawn)s'\n"
        "chmod +x '%(nspawn)s'\n"
        "rm -f '%(extras)s'\n"
        "mkdir -p '%(relay_dir)s'\n"
        "mkdir -p '%(rootfs)s%(script_dir)s'\n"
        "tr -d '\\r' < '%(launcher)s' > '%(rootfs)s%(launcher_in)s'\n"
        "tr -d '\\r' < '%(probe)s' > '%(rootfs)s%(probe_in)s'\n"
        "chmod +x '%(rootfs)s%(launcher_in)s' '%(rootfs)s%(probe_in)s'\n"
        "uid=$(chroot '%(rootfs)s' id -u %(user)s)\n"
        "mkdir -p '%(home)s'\n"
        "chown \"$uid:$uid\" '%(home)s'\n"
    ) % {"relay": wsl_wslpath(relay_path), "wrapper": wsl_wslpath(wrapper_path),
         "nspawn": WSL_NSPAWN_SCRIPT, "relay_dir": WSL_RELAY_DIR, "script_dir": WSL_SCRIPT_DIR,
         "extras": WSL_EXTRAS_PATH,
         "launcher": wsl_wslpath(launcher_path), "probe": wsl_wslpath(probe_path),
         "launcher_in": WSL_LAUNCHER_PATH, "probe_in": WSL_PROBE_PATH,
         "rootfs": WSL_ROOTFS, "user": WSL_SANDBOX_USER, "home": WSL_HOME}

    proc = wsl_run_script(wsl, distro, script, "runtime")

    if proc.returncode:
        fail("the WSL sandbox runtime files could not be prepared (%s)." % (wsl_tail(proc) or "unknown error"))


def wsl_stop_relay(wsl, distro):
    subprocess.run([wsl, "-d", distro, "-u", "root", "--", "systemctl", "stop", WSL_RELAY_UNIT],
                   capture_output=True, text=True, errors="replace")


def wsl_relay_active(wsl, distro):
    proc = subprocess.run([wsl, "-d", distro, "-u", "root", "--",
                           "systemctl", "is-active", WSL_RELAY_UNIT],
                          capture_output=True, text=True, errors="replace")
    return (proc.stdout or "").strip() == "active"


def wsl_start_relay(wsl, distro, port):
    wsl_stop_relay(wsl, distro)
    proc = subprocess.run([wsl, "-d", distro, "-u", "root", "--",
                           "systemd-run", "--collect", "--unit", WSL_RELAY_UNIT,
                           "python3", "/tmp/strata-wsl-relay.py", WSL_RELAY_DIR, str(port)],
                          capture_output=True, text=True, errors="replace")

    if proc.returncode:
        fail("the WSL model relay could not be started (%s)." % (wsl_tail(proc) or "unknown error"),
             "Check that python3 is installed inside %s and run the script again." % distro)

    wait = wsl_run_script(wsl, distro,
                          "for i in $(seq 1 20); do [ -S '%s/relay.sock' ] && exit 0; sleep 0.5; done; exit 1"
                          % WSL_RELAY_DIR, "relaywait")

    if wait.returncode:
        wsl_stop_relay(wsl, distro)
        fail("the WSL model relay started but never created its socket in %s." % WSL_RELAY_DIR,
             "Run 'journalctl -u %s' inside %s to see why it stopped, then try again."
             % (WSL_RELAY_UNIT, distro))


def wsl_nspawn_base(wsl, distro):
    return [wsl, "-d", distro, "-u", "root", "--", "bash", WSL_NSPAWN_SCRIPT]


def wsl_models_reachable(wsl, distro, port):
    probe = wsl_nspawn_base(wsl, distro) + [WSL_PROBE_PATH, str(port)]
    proc = subprocess.run(probe, capture_output=True, text=True, errors="replace")
    return proc.returncode == 0


def wsl_write_extras(wsl, distro, extras):
    path = os.path.join(WSL_DIR, "extras.txt")
    body = "".join("%s\n" % base64.b64encode(item.encode("utf-8")).decode("ascii") for item in extras)
    write_text_file(path, body)
    script = "tr -d '\\r' < '%s' > '%s'\n" % (wsl_wslpath(path), WSL_EXTRAS_PATH)
    proc = wsl_run_script(wsl, distro, script, "extras")

    if proc.returncode:
        fail("the WSL sandbox mount options could not be prepared (%s)." % (wsl_tail(proc) or "unknown error"))


def run_opencode_wsl(handle, port, open_code_json):
    wsl, distro = handle["wsl"], handle["distro"]

    if IS_WINDOWS and PROJECT_DIR.startswith("\\\\"):
        fail("the WSL sandbox cannot mount the current folder: %s" % PROJECT_DIR,
             "WSL cannot bind-mount network shares; cd to a local drive folder and run again.")

    wsl_push_runtime_files(wsl, distro)

    if wsl_relay_active(wsl, distro):
        fail("another sandboxed OpenCode is already running through the WSL relay.",
             "Close that session's window first; WSL-sandboxed OpenCode instances share one relay.")

    wsl_start_relay(wsl, distro, port)

    try:
        if not wsl_models_reachable(wsl, distro, port):
            fail("the WSL sandbox could not reach the Strata endpoint through the relay.",
                 "the WSL sandbox needs mirrored networking so WSL shares the Windows loopback: put "
                 "[wsl2] and networkingMode=mirrored into %USERPROFILE%\\.wslconfig, run wsl --shutdown, "
                 "and try again (with NAT-mode WSL the Hyper-V firewall blocks WSL-to-Windows connections)")

        mount = sandbox_mount_path(PROJECT_DIR)
        sandbox_json = sandbox_config_content(open_code_json, "http://127.0.0.1", port)

        no_proxy = "127.0.0.1,localhost"
        extras = ["--bind=%s:%s" % (wsl_wslpath(PROJECT_DIR), mount),
                  "--bind=%s:/home/sandbox" % WSL_HOME]

        for name, value in (("OPENCODE_CONFIG_CONTENT", sandbox_json),
                            ("HTTP_PROXY", OFFLINE_PROXY_URL), ("HTTPS_PROXY", OFFLINE_PROXY_URL),
                            ("http_proxy", OFFLINE_PROXY_URL), ("https_proxy", OFFLINE_PROXY_URL),
                            ("NO_PROXY", no_proxy), ("no_proxy", no_proxy)):
            extras.append("--setenv=%s=%s" % (name, value))

        wsl_write_extras(wsl, distro, extras)

        arguments = wsl_nspawn_base(wsl, distro) + [WSL_LAUNCHER_PATH, str(port), mount]

        write_info("project folder mounted at %s (read-write)" % mount)
        rc = subprocess.run(arguments).returncode

        if rc:
            write_warn("the WSL container exited with code %d" % rc)
    finally:
        reset_terminal_mouse()
        wsl_stop_relay(wsl, distro)


# ----------------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-CheckOnly", dest="check_only", action="store_true")
    parser.add_argument("-Family", dest="family", choices=["qwen", "swift", "coder", "unsloth"])
    parser.add_argument("-Model", dest="model")
    parser.add_argument("-Context", dest="context", type=int)
    parser.add_argument("-Kv", dest="kv", choices=["int8", "k8v4", "q4_0"])
    parser.add_argument("-DataDir", dest="data_dir")
    parser.add_argument("-Port", dest="port", type=int)
    parser.add_argument("-NoLaunch", dest="no_launch", action="store_true")
    parser.add_argument("-NoAlias", dest="no_alias", action="store_true")
    parser.add_argument("-NoGui", dest="no_gui", action="store_true")
    parser.add_argument("-Update", dest="update", action="store_true")
    parser.add_argument("-Stop", dest="stop", action="store_true")
    parser.add_argument("-Status", dest="status", action="store_true")
    parser.add_argument("-Setup", dest="setup", action="store_true")
    parser.add_argument("-Isolation", dest="isolation", choices=ISOLATION_CHOICES)
    parser.add_argument("-Alias", dest="alias", action="store_true")
    parser.add_argument("-AliasShell", dest="alias_shell")
    parser.add_argument("-Help", "--help", "-h", dest="help", action="store_true")

    args, unknown = parser.parse_known_args()

    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("-Port must be between 1 and 65535")

    if unknown:
        parser.error("unrecognized arguments: %s" % " ".join(unknown))

    return args


def main():
    global TRANSCRIPT, TRANSCRIPT_PATH, SETUP_LOCK, COLOR_ON

    args = parse_args()

    state = {
        "family": args.family or "",
        "model": args.model or "",
        "context": args.context if args.context is not None else 0,
        "kv": args.kv or "",
        "data_dir": args.data_dir or "",
        "port": args.port if args.port is not None else 8080,
        "isolation": args.isolation or "",
        "isolation_specified": args.isolation is not None,
        "context_specified": args.context is not None,
        "kv_specified": args.kv is not None,
        "data_dir_specified": args.data_dir is not None,
        "port_specified": args.port is not None,
        "no_launch": args.no_launch,
        "no_alias": args.no_alias,
        "no_gui": args.no_gui,
        "update": args.update,
        "check_only": args.check_only,
        "setup": args.setup,
    }

    if state["isolation"] == "wsl" and not IS_WINDOWS:
        fail("the WSL isolation needs Windows; it runs the container inside Windows Subsystem for Linux.",
             "For the same wall on this system use docker or podman: scode -Setup -Isolation docker")

    try:
        if args.help:
            show_help()
            sys.exit(0)

        if args.alias:
            target = (args.alias_shell or "").strip().lower() or "auto"

            shell = {
                "auto": None,
                "cmd": "cmd",
                "powershell": "powershell",
                "ps": "powershell",
                "pwsh": "powershell",
                "bash": "bash",
                "git-bash": "bash",
                "gitbash": "bash",
                "zsh": "zsh",
            }.get(target)

            if target == "auto":
                shell = get_calling_shell()
            elif shell is None:
                fail("unknown alias target '%s'" % args.alias_shell,
                     "Valid targets: cmd, powershell, bash, zsh.")

            if shell in ("cmd", "powershell") and not IS_WINDOWS:
                fail("the '%s' alias target is only available on Windows." % shell,
                     "Use bash or zsh on this system.")

            install_scode_alias(shell)
            sys.exit(0)

        if args.stop:
            write_step("Stopping the Strata server")

            if stop_strata_servers() == 0:
                write_skip("no Strata server process was found")

            # best effort: a WSL relay left behind by an interrupted sandbox session
            stop_wsl = wsl_exe()
            stop_distro = (os.environ.get(WSL_DISTRO_ENV) or "").strip()

            if stop_wsl:
                subprocess.run([stop_wsl] + (["-d", stop_distro] if stop_distro else []) +
                               ["-u", "root", "--", "systemctl", "stop", WSL_RELAY_UNIT],
                               capture_output=True, text=True, errors="replace")

            sys.exit(0)

        if args.status:
            port = 8080
            status_setup = read_json(SETUP_PATH)
            if status_setup:
                status_port = get_int_setting(status_setup.get("port"), 0)
                if status_port > 0:
                    port = status_port
            port = args.port if args.port is not None else port
            load_status().run_status(sys.modules[__name__], port, args.no_gui)
            sys.exit(0)

        # --------------------------------------------------------------------------------------------
        # Transcript
        # --------------------------------------------------------------------------------------------

        try:
            os.makedirs(LOG_DIR, exist_ok=True)

            logs = sorted(
                glob.glob(os.path.join(LOG_DIR, "run-*.log")),
                key=lambda p: os.path.getmtime(p),
                reverse=True,
            )
            for stale in logs[LOG_KEEP - 1:]:
                try:
                    os.remove(stale)
                except OSError:
                    pass

            TRANSCRIPT_PATH = os.path.join(
                LOG_DIR, "run-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
            TRANSCRIPT = open(TRANSCRIPT_PATH, "w", encoding="utf-8", newline="\n")
        except Exception:
            TRANSCRIPT = None
            TRANSCRIPT_PATH = None

        # --------------------------------------------------------------------------------------------
        # Hardware check
        # --------------------------------------------------------------------------------------------

        write_step("Checking this PC")

        spec = get_system_spec()

        write_info("OS:   %s" % spec["os"])

        avx2_text = ("unknown" if spec["avx2"] is None
                     else "yes" if spec["avx2"] else "no")
        write_info("CPU:  %s (%s cores, AVX2: %s)" % (spec["cpu"], spec["cores"], avx2_text))
        write_info("RAM:  %s GB" % spec["ram_gb"])

        if spec["gpu"]:
            write_info("GPU:  %s, %.1f GB VRAM, driver %s, compute %s"
                       % (spec["gpu"]["name"], spec["gpu"]["vram_mib"] / 1024,
                          spec["gpu"]["driver"], spec["gpu"]["compute_cap"]))
        else:
            write_info("GPU:  %s (no NVIDIA GPU found)" % ", ".join(spec["other_gpus"]))

        cannot_run = "this PC cannot run any configured Qwen3.8-Flash-Next variant properly"

        if IS_WINDOWS:
            if sys.getwindowsversion().major < 10:
                fail("%s: Windows 10 or Windows 11 is required." % cannot_run)
        elif platform_machine() != "x86_64":
            fail("%s: this script supports x86_64 Linux only." % cannot_run)

        if not spec["gpu"]:
            fail("%s: an NVIDIA GPU with at least 12 GB VRAM is required." % cannot_run,
                 "This script supports NVIDIA GPUs only.")

        if spec["gpu"]["vram_mib"] < MIN_VRAM_MIB:
            fail("%s: the GPU has %.1f GB VRAM; at least 12 GB is required."
                 % (cannot_run, spec["gpu"]["vram_mib"] / 1024))

        driver_major = get_int_setting(str(spec["gpu"]["driver"]).split(".")[0], 0)

        if driver_major < MIN_DRIVER:
            fail("NVIDIA driver %s is too old; Strata requires driver %d or newer."
                 % (spec["gpu"]["driver"], MIN_DRIVER),
                 "Update the NVIDIA driver and run this script again.")

        if spec["gpu"]["compute_cap"] < MIN_COMPUTE_CAP:
            fail("%s: the GPU is older than the supported RTX 20-series baseline." % cannot_run,
                 "This script requires compute capability 7.5 or newer.")

        if spec["avx2"] is False:
            write_warn("this CPU has no AVX2 support; Strata may run slowly")

        smart_app_control = get_smart_app_control_state()

        if smart_app_control == "on":
            fail("Windows Smart App Control is on and will stop the Strata server from starting.",
                 SMART_APP_CONTROL_HINT)

        if smart_app_control == "evaluation":
            write_warn("Smart App Control is in evaluation mode; if Windows switches it on, "
                       "it will block Strata's unsigned engine")

        run_started = time.time()

        # --------------------------------------------------------------------------------------------
        # Saved setup settings
        # --------------------------------------------------------------------------------------------

        saved_setup = read_json(SETUP_PATH)

        if state["setup"] and not state["check_only"]:
            if run_setup_menu(state):
                sys.exit(0)

            saved_setup = prompt_setup_params(spec, state)
        elif state["setup"]:
            write_warn("the -Setup menu was skipped because -CheckOnly changes nothing")

        family_filter = state["family"]
        model_filter = state["model"]

        if saved_setup:
            if not family_filter and saved_setup.get("family"):
                family_filter = saved_setup["family"]

            if not model_filter and saved_setup.get("model"):
                model_filter = saved_setup["model"]

            if not state["context_specified"]:
                state["context"] = get_int_setting(saved_setup.get("context"), state["context"])

            if not state["kv_specified"] and saved_setup.get("kv"):
                state["kv"] = str(saved_setup["kv"])

            if not state["data_dir_specified"] and saved_setup.get("dataDir"):
                state["data_dir"] = saved_setup["dataDir"]

            if not state["port_specified"]:
                saved_port = get_int_setting(saved_setup.get("port"), 0)

                if saved_port > 0:
                    state["port"] = saved_port

            if not state["isolation_specified"]:
                state["isolation"] = saved_isolation(saved_setup)

        isolation = state["isolation"] or "none"

        if isolation == "wsl" and not IS_WINDOWS:
            fail("the WSL isolation needs Windows; it runs the container inside Windows Subsystem for Linux.",
                 "For the same wall on this system use docker or podman: scode -Setup -Isolation docker")

        local_guard = isolation == "local"
        container_backend = isolation if isolation in CONTAINER_ISOLATIONS else None
        wsl_active = isolation == "wsl"

        candidates = get_candidates(family_filter, model_filter)

        variant = pick_best_variant(candidates, spec["ram_gb"])

        family_specified = args.family is not None
        model_specified = args.model is not None

        if not variant and not (family_specified or model_specified):
            variant = pick_best_variant(VARIANTS, spec["ram_gb"])

            if variant:
                write_warn("the saved variant no longer fits this PC's RAM; switching to %s"
                           % variant["title"])

        if not variant:
            smallest = min(candidates, key=lambda v: v["min_ram_gb"]) \
                if (family_specified or model_specified) else min(VARIANTS, key=lambda v: v["min_ram_gb"])

            message = ("%s/%s needs about %d GB RAM; this PC has %s GB."
                       % (smallest["family"], smallest["model"], smallest["min_ram_gb"] + 2,
                          spec["ram_gb"]))

            if family_specified or model_specified:
                fail("the requested variant will not run properly here: %s" % message,
                     "Run without -Family or -Model to select a fitting variant automatically.")

            fail("%s: %s" % (cannot_run, message))

        tag = variant["model"] if variant["family"] == "qwen" else "%s-%s" % (variant["family"], variant["model"])
        tag = tag.lower()

        write_ok("chosen: %s" % variant["title"])

        grade, note = grade_variant(variant, spec["ram_gb"])

        if grade == "yellow":
            write_warn("%s on this PC: %s" % (variant["model"], note))

        # --------------------------------------------------------------------------------------------
        # Setup mutex
        # --------------------------------------------------------------------------------------------

        if not state["check_only"]:
            lock_path = os.path.join(APP_DIR, ".setup.lock")
            os.makedirs(APP_DIR, exist_ok=True)

            try:
                SETUP_LOCK = lock_file(lock_path)
            except OSError:
                write_step("Another strata-coder setup is running; waiting for it to finish")
                SETUP_LOCK = lock_file_blocking(lock_path)

        # --------------------------------------------------------------------------------------------
        # Existing Strata and OpenCode installations
        # --------------------------------------------------------------------------------------------

        write_step("Looking for existing installations")

        strata_settings = read_json(STRATA_SETTINGS_PATH)
        state_json = read_json(STATE_PATH)

        roots = []

        if state_json and state_json.get("StrataRoot"):
            roots.append(state_json["StrataRoot"])

        if os.path.isdir(APP_DIR):
            strata_dirs = [d for d in glob.glob(os.path.join(APP_DIR, "strata*"))
                           if os.path.isdir(d) and d != UNPACK_DIR]
            strata_dirs.sort(key=lambda d: os.path.getmtime(d), reverse=True)
            roots.extend(strata_dirs)

        if strata_settings and strata_settings.get("installs"):
            roots.extend(strata_settings["installs"])

        seen = set()
        roots = [r for r in roots
                 if r and os.path.isfile(os.path.join(r, "setup.py"))
                 and not (r in seen or seen.add(r))]

        strata_root = next((r for r in roots if test_strata_config(r, tag)), None)

        model_ready = bool(strata_root)

        if not strata_root and roots:
            strata_root = roots[0]

        strata_present = bool(strata_root)
        strata_version = get_strata_version(strata_root, state_json) if strata_present else None

        if strata_present:
            write_ok("Strata %s: %s" % (strata_version, strata_root))
        else:
            write_info("Strata: not installed")

        if model_ready:
            write_ok("model %s is configured" % tag)
        else:
            write_info("model %s is not configured" % tag)

        strata_target = None

        if not strata_present:
            strata_target = PINNED_STRATA
        elif state["update"]:
            latest_release = get_latest_release("Niko1221/Strata")

            if not latest_release:
                write_warn("skipping the Strata update check; continuing with the installed version")
            elif latest_release.get("tag_name") != strata_version:
                strata_target = latest_release["tag_name"]
                write_info("Strata update available: %s -> %s" % (strata_version, strata_target))
            else:
                write_ok("Strata is up to date (%s)" % latest_release["tag_name"])

        open_code_present = False
        open_code_version = None

        if os.path.isfile(OPENCODE_EXE):
            rc, version_output = run_native(OPENCODE_EXE, ["--version"])

            if rc == 0:
                open_code_present = True
                open_code_version = (version_output[0] if version_output else "").strip()

        if open_code_present:
            write_ok("OpenCode %s" % open_code_version)
        else:
            write_info("OpenCode: not installed (target: %s)" % OPENCODE_DIR)

        open_code_release = None

        if open_code_present and state["update"]:
            open_code_release = get_latest_release("anomalyco/opencode")

            if not open_code_release:
                write_warn("skipping the OpenCode update check; continuing with the installed version")
            elif str(open_code_release.get("tag_name", "")).lstrip("v") == \
                    (open_code_version or "").strip().lstrip("v"):
                write_ok("OpenCode is up to date")
                open_code_release = None
            else:
                write_info("OpenCode update available: %s -> %s"
                           % (open_code_version, open_code_release.get("tag_name")))

        rg_present = os.path.isfile(RG_EXE)
        rg_release = None

        if rg_present:
            write_ok("ripgrep installed (%s)" % RG_DIR)

            if state["update"]:
                rg_release = get_latest_release("BurntSushi/ripgrep")

                if not rg_release:
                    write_warn("skipping the ripgrep update check; continuing with the installed version")
                elif str(rg_release.get("tag_name", "")).lstrip("v") == \
                        str((state_json or {}).get("RipgrepVersion") or "").strip().lstrip("v"):
                    write_ok("ripgrep is up to date")
                    rg_release = None
                else:
                    write_info("ripgrep update available: %s -> %s"
                               % ((state_json or {}).get("RipgrepVersion"), rg_release.get("tag_name")))
        else:
            write_info("ripgrep: not installed (target: %s)" % RG_DIR)

        server_running = False
        port = state["port"]
        health = strata_health(port)

        if health:
            server_running = True
            write_ok("a Strata server is running on port %d (%s)" % (port, health.get("model")))
        else:
            processes = strata_server_processes()
            existing_server = processes[0] if processes else None

            if existing_server:
                server_running = True
                other_port = server_port(existing_server)

                if other_port != port:
                    if state["port_specified"]:
                        fail("a Strata server is already running on port %d, but port %d was requested."
                             % (other_port, port),
                             "Stop it with -Stop first, or run without -Port to use the running server.")

                    write_warn("a Strata server is already running on port %d; using it" % other_port)
                    port = other_port
                else:
                    write_info("a Strata server is starting on port %d" % port)
            elif port_in_use(port):
                fail("port %d is already in use by a process other than Strata." % port,
                     "Stop that process or run this script again with -Port <another-port>.")

        # --------------------------------------------------------------------------------------------
        # Disk-space validation
        # --------------------------------------------------------------------------------------------

        write_step("Checking disk space")

        known_data_dir = None

        if (strata_settings and strata_settings.get("data_dir")
                and os.path.exists(strata_settings["data_dir"])):
            known_data_dir = strata_settings["data_dir"]

        if state["data_dir"]:
            data_dir = os.path.abspath(os.path.expanduser(state["data_dir"]))
        elif known_data_dir:
            data_dir = known_data_dir
            write_info("using the model folder previously used by Strata")
        else:
            data_dir = os.path.join(os.path.expanduser("~"), "ai-models", "models")

        gguf_dir = None

        if not model_ready:
            shard_filter = model_shard_filter(variant["model"], True)
            any_shard_filter = model_shard_filter(variant["model"], False)

            if os.environ.get("HF_HUB_CACHE"):
                hf_hub = os.environ["HF_HUB_CACHE"]
            elif os.environ.get("HF_HOME"):
                hf_hub = os.path.join(os.environ["HF_HOME"], "hub")
            else:
                hf_hub = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")

            repo_dir = os.path.join(hf_hub, "models--" + variant["repo"].replace("/", "--"))

            if os.path.isdir(os.path.join(repo_dir, "snapshots")):
                for snap in sorted(glob.glob(os.path.join(repo_dir, "snapshots", "*"))):
                    for candidate in (snap, os.path.join(snap, variant["model"])):
                        if (count_matching(candidate, shard_filter) >= 1
                                and count_matching(candidate, any_shard_filter) >= 2):
                            gguf_dir = candidate
                            break
                    if gguf_dir:
                        break

            if not gguf_dir and os.path.isdir(data_dir):
                first_shard = find_model_shard(data_dir, shard_filter)

                if first_shard and count_matching(os.path.dirname(first_shard), any_shard_filter) >= 2:
                    gguf_dir = os.path.dirname(first_shard)

            if gguf_dir:
                write_ok("model files already downloaded: %s" % gguf_dir)

        need_gb = 0

        if not model_ready:
            download_gb = 0 if gguf_dir else variant["download_gb"]

            full_requirement = (download_gb + MTP_GB + variant["arena_gb"] + SLACK_GB) * MARGIN

            existing_gb = get_folder_gb(data_dir)

            need_gb = max(round(full_requirement - existing_gb, 1), 2)

            if existing_gb > 0:
                write_info("%s GB already exists in %s" % (existing_gb, data_dir))

        free_gb = get_free_gb(data_dir)

        tools_need_gb = 0

        if strata_target or not model_ready:
            tools_need_gb += 3

        if not open_code_present or open_code_release:
            tools_need_gb += 1

        if IS_WINDOWS:
            same_drive = (os.path.splitdrive(data_dir)[0].lower()
                          == os.path.splitdrive(APP_DIR)[0].lower())
        else:
            same_drive = True

        tools_free_gb = get_free_gb(APP_DIR)

        if same_drive:
            need_gb += tools_need_gb

        write_info("model folder: %s (%s GB free; %s GB required)" % (data_dir, free_gb, need_gb))

        if free_gb < need_gb:
            alternate = first_mount_with_space(need_gb, data_dir)

            if alternate:
                hint = "Run again with -DataDir %s" % os.path.join(alternate, "ai-models", "models")
            else:
                hint = "Free disk space. No fixed local drive has %s GB free." % need_gb

            fail("not enough free disk space: %s GB required in %s; %s GB available."
                 % (need_gb, data_dir, free_gb), hint)

        if not same_drive and tools_free_gb < tools_need_gb:
            fail("not enough disk space for tools in %s: %d GB required; %s GB available."
                 % (APP_DIR, tools_need_gb, tools_free_gb))

        write_ok("enough disk space")

        if state["check_only"]:
            write_step("Check only: no changes were made")

            if not strata_present:
                strata_line = "would be installed (%s)" % PINNED_STRATA
            elif strata_target:
                strata_line = "would be updated to %s" % strata_target
            else:
                strata_line = "installed (%s)" % strata_version

            if model_ready:
                model_line = "ready"
            elif gguf_dir:
                model_line = "would be configured from existing GGUF files"
            else:
                model_line = "would be downloaded (about %s GB)" % variant["download_gb"]

            if open_code_release:
                opencode_line = "would be updated to %s" % open_code_release["tag_name"]
            elif open_code_present:
                opencode_line = "installed"
            else:
                opencode_line = "would be installed"

            if rg_release:
                rg_line = "would be updated to %s" % rg_release["tag_name"]
            elif rg_present:
                rg_line = "installed"
            else:
                rg_line = "would be installed"

            server_line = ("running on port %d" % port) if server_running \
                else ("would be started on port %d" % port)

            write_info("Strata:   %s" % strata_line)
            write_info("Model:    %s" % model_line)
            write_info("OpenCode: %s" % opencode_line)
            write_info("ripgrep:  %s" % rg_line)
            write_info("Server:   %s" % server_line)
            write_info("Project:  %s" % PROJECT_DIR)

            close_session_resources()
            sys.exit(0)

        os.makedirs(APP_DIR, exist_ok=True)

        # --------------------------------------------------------------------------------------------
        # Strata installation
        # --------------------------------------------------------------------------------------------

        if strata_target:
            is_update = strata_present

            write_step("Updating Strata %s -> %s" % (strata_version, strata_target) if is_update
                       else "Installing Strata %s" % strata_target)

            old_root = strata_root
            new_root = install_strata_code(strata_target)

            write_ok("Strata %s unpacked in %s" % (strata_target, new_root))

            if is_update:
                if server_running:
                    write_info("stopping the running server so the updated version can take over")
                    stop_strata_servers()
                    server_running = False

                exit_code, configured = invoke_model_setup(
                    new_root, variant, data_dir, port, state["context"], state["kv"], gguf_dir, tag)

                if not configured:
                    shutil.rmtree(new_root, ignore_errors=True)

                    assert_not_blocked_by_smart_app_control(run_started)

                    fail("Strata %s setup did not finish successfully (exit code %d)."
                         % (strata_target, exit_code),
                         "The previous version remains installed. Run without -Update to use it.")

                strata_root = new_root
                model_ready = True

                save_state({
                    "StrataRoot": strata_root,
                    "StrataVersion": strata_target,
                    "Model": tag,
                    "DataDir": data_dir,
                })

                if (old_root and old_root.lower().startswith(APP_DIR.lower())
                        and old_root != new_root):
                    try:
                        shutil.rmtree(old_root)
                        write_ok("removed prior Strata version: %s" % old_root)
                    except OSError as exc:
                        write_warn("could not remove old Strata folder %s (%s)" % (old_root, exc))

                write_ok("Strata updated to %s" % strata_target)
            else:
                strata_root = new_root

                save_state({
                    "StrataRoot": strata_root,
                    "StrataVersion": strata_target,
                })
        else:
            write_skip("Strata already installed")

        # --------------------------------------------------------------------------------------------
        # Model configuration
        # --------------------------------------------------------------------------------------------

        config_drift = False

        if model_ready:
            config_drift = test_setup_drift(
                strata_root, tag, state["context"], state["kv"], data_dir)

            if config_drift:
                write_info("the setup settings differ from the configured model; reconfiguring")

        if not model_ready or config_drift:
            reconfigure = config_drift and model_ready

            if reconfigure:
                write_step("Reconfiguring %s with the new settings" % variant["title"])

                if server_running:
                    write_info("stopping the running server so the new settings take effect")
                    stop_strata_servers()
                    server_running = False
            else:
                write_step("Setting up %s" % variant["title"])

            if not reconfigure:
                download_estimate = SETUP_MINUTES if gguf_dir \
                    else round(variant["download_gb"] + SETUP_MINUTES)

                write_info("this may download about %d GB and can take a long time" % download_estimate)
                write_info("If interrupted, run the script again; supported downloads resume.")

            if not model_ready and strata_root and test_strata_engine_missing(strata_root, tag):
                write_warn("the model is configured but its engine file is missing "
                           "(the setup window's Delete Strata engine button removed it)")
                write_info("Strata re-creates the engine during setup, so this run needs network access")

            exit_code, configured = invoke_model_setup(
                strata_root, variant, data_dir, port, state["context"], state["kv"], gguf_dir, tag)

            if not configured:
                assert_not_blocked_by_smart_app_control(run_started)

                fail("Strata model setup did not finish successfully (exit code %d)." % exit_code,
                     "Review the setup output, correct the reported issue, and run the script again.")

            model_ready = True

            save_state({
                "StrataRoot": strata_root,
                "Model": tag,
                "DataDir": data_dir,
            })

            write_ok("model %s is configured" % tag)
        else:
            write_skip("model already configured")

        # --------------------------------------------------------------------------------------------
        # OpenCode installation
        # --------------------------------------------------------------------------------------------

        if not open_code_present or open_code_release:
            write_step("Updating OpenCode" if open_code_release else "Installing OpenCode")

            release = open_code_release or get_latest_release("anomalyco/opencode")

            if not release:
                fail("could not contact GitHub for the OpenCode release.",
                     "Check network access and run the script again.")

            if spec["avx2"] is False:
                asset_name = "opencode-windows-x64-baseline.zip" if IS_WINDOWS \
                    else "opencode-linux-x64-baseline.tar.gz"
            else:
                asset_name = "opencode-windows-x64.zip" if IS_WINDOWS \
                    else "opencode-linux-x64.tar.gz"

            asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)

            if not asset:
                fail("OpenCode release %s does not contain %s."
                     % (release.get("tag_name"), asset_name))

            zip_path = os.path.join(DOWNLOAD_DIR, asset_name)
            stage = os.path.join(APP_DIR, "opencode-unpack")

            try:
                staged_exe = get_downloaded_file(
                    asset["browser_download_url"], zip_path, stage,
                    "opencode.exe" if IS_WINDOWS else "opencode")
            except Exception as exc:
                fail("the OpenCode archive could not be prepared (%s); it was removed." % exc,
                     "Run the script again to download a fresh copy.")

            os.makedirs(OPENCODE_DIR, exist_ok=True)

            old_exe = OPENCODE_EXE + ".old"

            if os.path.isfile(old_exe):
                try:
                    os.remove(old_exe)
                except OSError:
                    pass

            if os.path.isfile(OPENCODE_EXE):
                if os.path.isfile(old_exe):
                    old_exe = "%s.%s.old" % (OPENCODE_EXE, time.strftime("%Y%m%d%H%M%S"))

                try:
                    os.replace(OPENCODE_EXE, old_exe)
                except OSError:
                    pass

            try:
                shutil.move(staged_exe, OPENCODE_EXE)
            except Exception as exc:
                if os.path.isfile(old_exe):
                    try:
                        os.replace(old_exe, OPENCODE_EXE)
                    except OSError:
                        pass

                fail("could not install opencode (%s)." % exc)

            if not IS_WINDOWS:
                try:
                    os.chmod(OPENCODE_EXE, 0o755)
                except OSError:
                    pass

            shutil.rmtree(stage, ignore_errors=True)
            if os.path.isfile(zip_path):
                os.remove(zip_path)

            save_state({"OpenCodeVersion": release["tag_name"]})

            write_ok("OpenCode %s" % release["tag_name"])
        else:
            write_skip("OpenCode already installed")

        # --------------------------------------------------------------------------------------------
        # ripgrep installation
        # --------------------------------------------------------------------------------------------

        if not rg_present or rg_release:
            write_step("Updating ripgrep" if rg_release else "Installing ripgrep")

            install_ripgrep(rg_release)
        else:
            write_skip("ripgrep already installed")

        # --------------------------------------------------------------------------------------------
        # Container sandbox preparation (docker and podman engines)
        # --------------------------------------------------------------------------------------------

        sandbox_tag = None
        sandbox_backend = CONTAINER_BACKENDS.get(container_backend) if container_backend else None

        if sandbox_backend:
            write_step("Preparing the %s sandbox" % sandbox_backend["label"])

            sandbox_tool, sandbox_detail = container_ready(sandbox_backend)

            if not sandbox_tool:
                fail_no_container(sandbox_backend, sandbox_detail)

            write_info("%s version %s" % (sandbox_backend["label"], sandbox_detail))
            ensure_sandbox_network(sandbox_tool)
            sandbox_tag = ensure_sandbox_image(sandbox_backend, sandbox_tool, spec["avx2"])

        # --------------------------------------------------------------------------------------------
        # WSL sandbox preparation (systemd-nspawn inside a WSL 2 distro; no container engine)
        # --------------------------------------------------------------------------------------------

        wsl_handle = None

        if wsl_active:
            write_step("Preparing the WSL sandbox")

            wsl_handle, wsl_detail = wsl_ready()

            if not wsl_handle:
                fail_no_wsl(wsl_detail)

            write_info("distro: %s" % wsl_detail)
            ensure_wsl_packages(wsl_handle["wsl"], wsl_handle["distro"])
            ensure_wsl_rootfs(wsl_handle["wsl"], wsl_handle["distro"], spec["avx2"])

            if not wsl_is_mirrored(wsl_handle["wsl"], wsl_handle["distro"]):
                write_warn("mirrored networking was not detected inside WSL (no 10.255.255.254 on lo)")
                write_warn("if the model connection fails, add [wsl2] and networkingMode=mirrored to "
                           "%UserProfile%\\.wslconfig, then run: wsl --shutdown")

        # --------------------------------------------------------------------------------------------
        # -Setup stops here
        # --------------------------------------------------------------------------------------------

        if state["setup"]:
            write_step("Setup complete")
            write_info("the server was not started and OpenCode was not opened")
            write_info("run scode (or strata-coder) in a project folder when you are ready to use it")

            show_ram_stability(spec, variant, state["context"], state["kv"],
                               strata_root, tag, data_dir, gguf_dir)

            close_session_resources()
            sys.exit(0)

        # --------------------------------------------------------------------------------------------
        # Start Strata
        # --------------------------------------------------------------------------------------------

        server_log = os.path.join(LOG_DIR, "server.log")

        if not server_running:
            write_step("Starting the Strata server")

            strata_config_path = os.path.join(strata_root, "strata-%s.json" % tag)
            strata_config = read_json(strata_config_path)
            python = venv_python(strata_root)

            if not os.path.isfile(python):
                fail("Strata Python environment was not found: %s" % python)

            if not os.path.isfile(strata_config_path):
                fail("Strata model configuration was not found: %s" % strata_config_path)

            os.makedirs(LOG_DIR, exist_ok=True)

            if os.path.isfile(server_log):
                shutil.move(server_log, os.path.join(LOG_DIR, "server.prev.log"))

            env = dict(os.environ)
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            if sandbox_backend:
                env["STRATA_ALLOWED_HOSTS"] = "%s,host.docker.internal" % SANDBOX_RELAY
                write_info("the server will listen on 0.0.0.0 so the container can reach it; "
                           "Windows Firewall blocks other devices by default")

            log_handle = open(server_log, "wb")

            popen_kwargs = {}
            if IS_WINDOWS:
                # CREATE_NO_WINDOW, not DETACHED_PROCESS: the server needs a
                # windowless console so the engine processes it spawns inherit
                # one. A fully detached server makes each console child create
                # its own new visible console window.
                flags = 0
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    flags |= subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                    flags |= subprocess.CREATE_NEW_PROCESS_GROUP
                popen_kwargs["creationflags"] = flags
            else:
                popen_kwargs["start_new_session"] = True

            server_process = subprocess.Popen(
                [python, os.path.join(strata_root, "serve", "server.py"),
                 "--engine", "strata", "--config", strata_config_path, "--port", str(port)] +
                (["--host", "0.0.0.0"] if sandbox_backend else []),
                cwd=strata_root,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
                **popen_kwargs
            )
            log_handle.close()

            write_info("loading the model; the first start may take several minutes")
            write_info("server output: %s" % server_log)

            if not wait_strata_ready(server_process, port):
                reason = "it stopped" if server_process.poll() is not None \
                    else "it was not ready after %d minutes" % LOAD_MINUTES

                show_log_tail(server_log)

                if strata_config and strata_config.get("log"):
                    show_log_tail(strata_config["log"], 15)

                if server_process.poll() is None:
                    stop_strata_servers(port)

                assert_not_blocked_by_smart_app_control(run_started)

                fail("the Strata server did not come up: %s." % reason,
                     "Review the logs above and %s."
                     % os.path.join(strata_root, "START-HERE.bat" if IS_WINDOWS else "setup.sh"))
        elif not strata_ready(port):
            write_step("Waiting for the running Strata server to finish loading")

            if not wait_strata_ready(None, port):
                show_log_tail(server_log)

                fail("the running Strata server did not become ready within %d minutes." % LOAD_MINUTES,
                     "Stop it with -Stop and run the script again.")
        else:
            write_skip("server already running")

        health = strata_health(port)

        if not health:
            fail("the Strata server stopped answering on port %d." % port, "See %s" % server_log)

        write_ok("Strata is serving %s on http://127.0.0.1:%d (context %s)"
                 % (health.get("model"), port, health.get("max_context")))

        # --------------------------------------------------------------------------------------------
        # Verify Strata API and build OpenCode config
        # --------------------------------------------------------------------------------------------

        write_step("Verifying the Strata API for OpenCode")

        served_models = strata_models(port)

        if len(served_models) == 0:
            fail("Strata is healthy on port %d, but /v1/models returned no usable model IDs." % port,
                 "Review %s and check http://127.0.0.1:%d/v1/models." % (server_log, port))

        model_info = next(
            (m for m in served_models if str(m.get("id")) == str(health.get("model"))), None)

        if not model_info and len(served_models) == 1:
            model_info = served_models[0]

        if not model_info:
            available_ids = ", ".join(str(m.get("id")) for m in served_models)
            fail("could not match Strata health model '%s' to a /v1/models model ID."
                 % health.get("model"),
                 "Available model IDs: %s" % available_ids)

        model_id = str(model_info["id"]).strip()

        write_info("Strata health model: %s" % health.get("model"))
        write_info("OpenAI API model ID: %s" % model_id)

        ok, error, _reply = strata_chat_completion(port, model_id)

        if not ok:
            fail("Strata is healthy, but /v1/chat/completions rejected model '%s'." % model_id,
                 "Request error: %s" % error)

        write_ok("Strata accepted an OpenAI-compatible chat request for '%s'" % model_id)

        display_name = variant["title"].rsplit(" (", 1)[0] + " (local)"

        context_limit = DEFAULT_CONTEXT_LIMIT

        try:
            if health.get("max_context"):
                reported_context = int(health["max_context"])

                if reported_context > 0:
                    context_limit = reported_context
        except (TypeError, ValueError):
            pass

        output_limit = min(MAX_OUTPUT_LIMIT, max(MIN_OUTPUT_LIMIT, context_limit // 4))

        open_code_config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "strata": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Strata (local)",
                    "options": {
                        "baseURL": "http://127.0.0.1:%d/v1" % port,
                        "apiKey": "local",
                    },
                    "models": {
                        model_id: {
                            "name": display_name,
                            "limit": {
                                "context": context_limit,
                                "output": output_limit,
                            },
                        }
                    },
                }
            },
            "model": "strata/%s" % model_id,
            "small_model": "strata/%s" % model_id,
            "enabled_providers": ["strata"],
            "share": "disabled",
            "autoupdate": False,
        }

        if isolation != "none":
            open_code_config["permission"] = offline_permission_block()

        open_code_json = json.dumps(open_code_config, indent=2)

        old_open_code_json = ""
        if os.path.isfile(OPENCODE_CFG):
            try:
                with open(OPENCODE_CFG, "r", encoding="utf-8-sig") as handle:
                    old_open_code_json = handle.read()
            except OSError:
                pass

        if old_open_code_json.strip() != open_code_json.strip():
            write_text_file(OPENCODE_CFG, open_code_json)
            write_ok("OpenCode config written: " + OPENCODE_CFG)
        else:
            write_skip("OpenCode config is already current")

        # --------------------------------------------------------------------------------------------
        # Launch OpenCode
        # --------------------------------------------------------------------------------------------

        close_session_resources()

        if state["no_launch"]:
            write_step("Ready")
            write_info("server: http://127.0.0.1:%d/v1" % port)
            write_info("OpenCode config: " + OPENCODE_CFG)
            write_info("OpenCode model: strata/%s" % model_id)

            if local_guard:
                write_info("OpenCode web access: blocked (web tools denied, network shell commands denied)")
                write_info("a dead proxy will be set for OpenCode and its child processes: " + OFFLINE_PROXY_URL)

            if wsl_active:
                write_info("OpenCode will run in a systemd-nspawn WSL container with no network "
                           "interfaces; the model is reached through a WSL-side relay")

            write_info("stop the Strata server with: strata-coder -Stop (or scode -Stop)")
            sys.exit(0)

        write_step("Opening OpenCode in a %s sandbox from %s"
                   % ((sandbox_backend["label"] if sandbox_backend else "WSL"), PROJECT_DIR)
                   if (sandbox_backend or wsl_handle)
                   else "Opening OpenCode in " + PROJECT_DIR)

        os.environ["OPENCODE_CONFIG"] = OPENCODE_CFG

        if os.path.isfile(RG_EXE):
            path_entries = os.environ.get("PATH", "").split(os.pathsep)
            if RG_DIR not in path_entries:
                os.environ["PATH"] = RG_DIR + os.pathsep + os.environ.get("PATH", "")

        os.environ["OPENCODE_CONFIG_CONTENT"] = open_code_json

        write_info("OpenCode config: " + OPENCODE_CFG)
        write_info("OpenCode provider: strata")
        write_info("OpenCode model: strata/%s" % model_id)
        write_info("Strata endpoint: http://127.0.0.1:%d/v1" % port)

        if sandbox_backend:
            write_info("OpenCode runs in container '%s' on internal network '%s'"
                       % (SANDBOX_CONTAINER, SANDBOX_NETWORK))
            write_info("container egress is blocked by %s; only the local model endpoint is reachable"
                       % sandbox_backend["label"])
        elif wsl_handle:
            write_info("OpenCode runs in a systemd-nspawn container in WSL distro '%s'"
                       % wsl_handle["distro"])
            write_info("the container has no network interfaces at all; only the local model endpoint "
                       "is reachable through the WSL-side relay")
        elif local_guard:
            write_info("OpenCode web access: blocked (web tools denied, network shell commands denied)")
            write_info("dead proxy set for OpenCode and its child processes: " + OFFLINE_PROXY_URL)

        if not os.path.isfile(OPENCODE_CFG):
            fail("the generated OpenCode configuration file does not exist: " + OPENCODE_CFG)

        debug_config_path = os.path.join(LOG_DIR, "opencode-resolved-config.log")

        try:
            debug_proc = subprocess.run(
                [OPENCODE_EXE, "debug", "config"],
                capture_output=True, text=True, errors="replace", cwd=PROJECT_DIR)
            debug_text = (debug_proc.stdout or "") + (debug_proc.stderr or "")
            write_text_file(debug_config_path, debug_text)

            if '"strata"' not in debug_text:
                write_warn("OpenCode resolved configuration did not show the Strata provider.")
                write_warn("resolved config log: " + debug_config_path)
            elif "strata/%s" % model_id not in debug_text:
                write_warn("OpenCode resolved configuration did not show strata/%s." % model_id)
                write_warn("resolved config log: " + debug_config_path)
            else:
                write_ok("OpenCode resolved the Strata provider and selected model")
        except Exception as exc:
            write_warn("could not run 'opencode debug config' (%s)" % exc)

        # sinkhole: OpenCode and everything its bash tool spawns inherit a dead
        # proxy; NO_PROXY keeps the local Strata endpoint working. Any pre-existing
        # proxy settings are saved and restored as soon as OpenCode exits; atexit
        # is the fallback in case the run itself is interrupted before the finally.
        # The sandbox container gets its own copies via the engine's -e instead.
        offline_proxy_saved = {}
        if local_guard:
            offline_proxy_saved = set_offline_proxy_sinkhole()
            atexit.register(clear_offline_proxy_sinkhole, offline_proxy_saved)

        try:
            if sandbox_backend:
                run_opencode_sandbox(container_backend, sandbox_tag, port, open_code_json)
            elif wsl_handle:
                run_opencode_wsl(wsl_handle, port, open_code_json)
            else:
                subprocess.run([OPENCODE_EXE, PROJECT_DIR], cwd=PROJECT_DIR)
        finally:
            reset_terminal_mouse()
            os.environ.pop("OPENCODE_CONFIG_CONTENT", None)
            os.environ.pop("OPENCODE_CONFIG", None)
            clear_offline_proxy_sinkhole(offline_proxy_saved)

        write_step("Stopping the Strata server")

        if stop_strata_servers(port) == 0:
            write_skip("no Strata server process was found")
    except SystemExit:
        raise
    except KeyboardInterrupt:
        close_session_resources()
        fail("interrupted by user")
    except Exception as exc:
        line = ""
        tb = exc.__traceback__
        while tb:
            line = " (line %d)" % tb.tb_lineno
            tb = tb.tb_next
        fail("unexpected error: %s%s" % (exc, line),
             "Run the script again. If the issue repeats, inspect the run log.")


def platform_machine():
    import platform
    return platform.machine()


def first_mount_with_space(need_gb, exclude):
    if IS_WINDOWS:
        try:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.kernel32.GetLogicalDriveStringsW(255, buf)
            roots = [r for r in buf.value.split("\0") if r]
            candidates = []
            for root in roots:
                try:
                    if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:
                        if shutil.disk_usage(root).free >= need_gb * (1024 ** 3):
                            candidates.append(root)
                except OSError:
                    pass
            return candidates[0] if candidates else None
        except Exception:
            return None

    best = None
    try:
        with open("/proc/mounts") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) < 2 or not parts[0].startswith("/dev/"):
                    continue
                mount = parts[1]
                if mount == os.path.abspath(exclude) or os.path.abspath(exclude).startswith(mount + "/"):
                    continue
                try:
                    if shutil.disk_usage(mount).free >= need_gb * (1024 ** 3):
                        best = mount
                        break
                except OSError:
                    pass
    except OSError:
        pass
    return best


if __name__ == "__main__":
    main()
