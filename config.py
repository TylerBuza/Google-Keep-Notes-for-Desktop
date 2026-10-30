"""Configuration and persistent state management for KeepDesktop."""

import json
import os
import subprocess
import sys
import winreg

APP_NAME = "KeepDesktop"
# Stamped at release time by .github/workflows/release.yml from the
# git tag (e.g. v2.1.1 → "2.1.1"). Leave this as the dev sentinel in
# source — do NOT bump manually for releases. Runtime update networking is removed.
APP_VERSION = "0.0.0"
# Source attribution only; never used for runtime HTTP requests.
GITHUB_REPO = "TylerBuza/Google-Keep-Notes-for-Desktop"
# Private builds must never install unsigned upstream binaries over our fixes.
AUTO_UPDATE_ENABLED = False
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.environ.get("APPDATA", APP_DIR), APP_NAME)
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
TOKEN_FILE = os.path.join(DATA_DIR, "keep_token.dat")
POSITIONS_FILE = os.path.join(DATA_DIR, "positions.json")
NOTES_FILE = os.path.join(DATA_DIR, "notes.dat")
LEGACY_NOTES_FILE = os.path.join(DATA_DIR, "notes.json")

# Path of the Startup-folder shortcut that controls "start with Windows".
# This MUST be the same path the Inno Setup installer creates (see
# installer.iss → [Icons] {userstartup}\KeepDesktop.lnk) so the in-app
# toggle and the installer checkbox are literally the same setting.
STARTUP_FOLDER = os.path.join(
    os.environ.get("APPDATA", APP_DIR),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
)
STARTUP_SHORTCUT = os.path.join(STARTUP_FOLDER, f"{APP_NAME}.lnk")
# Legacy registry path used by older versions (<= 2.0.2) — we still read
# it for migration but the canonical store is the .lnk above.
_LEGACY_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# Google Keep note colors mapped to hex (keys match gkeepapi ColorValue names)
KEEP_COLORS = {
    "White": "#FFFFFF",
    "Red": "#F28B82",
    "Orange": "#FBBC04",
    "Yellow": "#FFF475",
    "Green": "#CCFF90",
    "Teal": "#A7FFEB",
    "Blue": "#CBF0F8",
    "DarkBlue": "#AECBFA",
    "Purple": "#D7AEFB",
    "Pink": "#FDCFE8",
    "Brown": "#E6C9A8",
    "Gray": "#E8EAED",
}

# Dark variants for the same Keep colour names. These are LOCAL-ONLY: the
# value sent back to Google Keep is always the matching LIGHT colour from
# KEEP_COLORS above. Picking a dark variant flips ``KeepNote.dark_mode``
# but leaves ``color_hex`` pointing at the light value Keep understands.
KEEP_COLORS_DARK = {
    "White":    "#202124",
    "Red":      "#5C2B29",
    "Orange":   "#614A19",
    "Yellow":   "#635D19",
    "Green":    "#345920",
    "Teal":     "#16504B",
    "Blue":     "#2D555E",
    "DarkBlue": "#1E3A5F",
    "Purple":   "#42275E",
    "Pink":     "#5B2245",
    "Brown":    "#442F19",
    "Gray":     "#3C3F43",
}

# Default window dimensions
DEFAULT_WIDTH = 260
DEFAULT_HEIGHT = 300
MIN_WIDTH = 180
MIN_HEIGHT = 120

# Sync interval in milliseconds (30 seconds)
SYNC_INTERVAL_MS = 30_000


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    ensure_data_dir()
    from secure_storage import atomic_write
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))


def load_config():
    defaults = {
        "sync_enabled": False,
        "autostart": False,
        "always_on_top": False,
        "opacity": 0.95,
        "cloud_writes_enabled": True,
        "cloud_writes_consent_version": 1,
        "cloud_writes_policy_version": 2,
        "remember_google_account": False,
        "experimental_auth_consent": 0,
        "start_offline": False,
        # Legacy preference is tolerated for old configs, but the desktop
        # always uses the hardened v2 path and never falls back to gkeepapi.
        "keep_protocol_v2": True,
        # Global text-size multiplier applied across every note window
        # (body, headings, toolbar, checklist). 1.0 = 100%.
        "font_scale": 1.0,
    }
    saved = load_json(CONFIG_FILE, {})
    # Merge: saved values win, defaults fill in any missing keys
    merged = {**defaults, **saved}
    # Earlier builds persisted temporary read failures as an OFF preference.
    # Upgrade to the normal two-way default once. The separate durable
    # uncertain-write journal is NEVER cleared here. Subsequent manual pauses
    # are preserved by policy version 2, including across sign-out/sign-in.
    if saved.get("cloud_writes_policy_version") != 2:
        merged["cloud_writes_enabled"] = True
        merged["cloud_writes_consent_version"] = 1
        merged["cloud_writes_policy_version"] = 2
    return merged


def save_config(cfg):
    save_json(CONFIG_FILE, cfg)


def load_positions():
    return load_json(POSITIONS_FILE, {})


def save_positions(positions):
    save_json(POSITIONS_FILE, positions)


def get_position(note_id):
    positions = load_positions()
    return positions.get(note_id, None)


def set_position(note_id, x, y, w, h, pinned=None, auto_size=None):
    positions = load_positions()
    entry = positions.get(note_id, {}) or {}
    entry.update({"x": x, "y": y, "w": w, "h": h})
    if pinned is not None:
        entry["pinned"] = bool(pinned)
    if auto_size is not None:
        entry["auto_size"] = bool(auto_size)
    positions[note_id] = entry
    save_positions(positions)


def remove_position(note_id):
    positions = load_positions()
    positions.pop(note_id, None)
    save_positions(positions)


def _autostart_target() -> tuple[str, str, str]:
    """Return (target_exe, arguments, working_dir) for the Startup
    shortcut. Differs between frozen builds and source runs."""
    if getattr(sys, "frozen", False):
        return sys.executable, "", os.path.dirname(sys.executable)
    # Running from source — prefer pythonw.exe so we don't get a
    # console window flash on every login.
    py = sys.executable
    pyw = py.replace("python.exe", "pythonw.exe")
    if os.path.exists(pyw):
        py = pyw
    main_py = os.path.join(APP_DIR, "main.py")
    return py, f'"{main_py}"', APP_DIR


def _remove_legacy_run_key() -> None:
    """Strip the old HKCU Run-key entry written by versions <= 2.0.2.

    Keeping it around would cause double-launch when both the legacy
    key AND the new Startup-folder shortcut are present.
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _LEGACY_RUN_KEY, 0, winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    """True iff a Startup-folder shortcut for KeepDesktop exists.

    This is the SOURCE OF TRUTH — both the installer's tickbox and the
    in-app "Start with Windows" toggle write to the same .lnk path.
    """
    return os.path.isfile(STARTUP_SHORTCUT)


def set_autostart(enabled: bool) -> bool:
    """Add or remove the Startup-folder shortcut.

    Uses PowerShell's WScript.Shell COM helper (no extra deps). Returns
    True on success. Idempotent.
    """
    if not enabled:
        try:
            os.remove(STARTUP_SHORTCUT)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        _remove_legacy_run_key()
        return True

    target, args, workdir = _autostart_target()
    try:
        os.makedirs(STARTUP_FOLDER, exist_ok=True)
    except OSError:
        return False

    # Build the PowerShell snippet. Single-quoted string literals are
    # safest because backslashes don't need escaping; only embedded
    # single quotes do (we double them per PS rules).
    def _ps_quote(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$lnk = $ws.CreateShortcut({_ps_quote(STARTUP_SHORTCUT)}); "
        f"$lnk.TargetPath = {_ps_quote(target)}; "
        f"$lnk.Arguments = {_ps_quote(args)}; "
        f"$lnk.WorkingDirectory = {_ps_quote(workdir)}; "
        "$lnk.WindowStyle = 1; "
        "$lnk.Save()"
    )
    CREATE_NO_WINDOW = 0x08000000
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", ps],
            creationflags=CREATE_NO_WINDOW,
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return False

    _remove_legacy_run_key()
    return os.path.isfile(STARTUP_SHORTCUT)


def save_token(token, email="", android_id=None):
    from secure_storage import save_protected
    record = {"token": token, "email": email}
    if android_id:
        record["android_id"] = android_id
    save_protected(TOKEN_FILE, record)


def load_token(email=None):
    from pathlib import Path
    from secure_storage import MAGIC, load_protected
    try:
        raw = Path(TOKEN_FILE).read_bytes()
    except FileNotFoundError:
        return None
    if raw.startswith(MAGIC):
        record = load_protected(TOKEN_FILE)
        if email and record.get("email") != email:
            return None
        return record.get("token")
    # Legacy tokens had no account binding. Only migrate when the caller
    # supplies the account from the existing config; never guess an account.
    if not email or email != load_config().get("email"):
        return None
    token = raw.decode("utf-8").strip()
    if token:
        save_token(token, email)
    return token or None


def load_token_record(email):
    from secure_storage import load_protected
    token = load_token(email)
    if not token:
        return None
    record = load_protected(TOKEN_FILE)
    return {"token": token, "android_id": record.get("android_id")}


def clear_token(email=None):
    from keep_protocol.auth import clear_credentials
    # Clear both legacy keyring copies and the DPAPI file. Fail visibly if
    # the credential store cannot be cleared rather than claiming sign-out.
    failed = False
    try:
        clear_credentials(email)
    except Exception:
        failed = True
    try:
        os.remove(TOKEN_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        failed = True
    if failed:
        raise RuntimeError("Some stored credentials could not be cleared.")


def save_notes(data):
    from secure_storage import save_protected
    save_protected(NOTES_FILE, data)


def load_notes():
    from secure_storage import load_protected
    if os.path.exists(NOTES_FILE):
        return load_protected(NOTES_FILE)
    if not os.path.exists(LEGACY_NOTES_FILE):
        return {}
    # Do not treat malformed legacy data as an empty notebook.
    with open(LEGACY_NOTES_FILE, encoding="utf-8") as source:
        data = json.load(source)
    save_notes(data)
    if load_protected(NOTES_FILE) != data:
        raise RuntimeError("Note cache migration could not be verified.")
    os.remove(LEGACY_NOTES_FILE)
    return data
