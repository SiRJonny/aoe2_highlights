"""Headless AoE2Control injection (the DLL host for the Lua module).

inject() is a no-op when CONTROL already runs (its RmsIde pipe is up) —
after a PC suspend the pipe is dead and a full bootstrap (game reboot +
inject) is required instead of a mere reconnect.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = ROOT / "AoE2Control_1.1.5" / "AoE2Control.exe"
SETTINGS_OVERRIDE = ROOT / "tools" / "aoe2control" / "settings.timeline.ini"
MODULE_OVERRIDE = ROOT / "tools" / "aoe2control" / "timeline_recorder"


def control_running() -> bool:
    script = (
        "import win32file, pywintypes\n"
        "try:\n"
        "    h = win32file.CreateFile('\\\\\\\\.\\\\pipe\\\\AoE2ControlRmsIdeV1',"
        " 0x80000000 | 0x40000000, 0, None, 3, 0, None)\n"
        "    print('yes')\n"
        "except pywintypes.error:\n"
        "    print('no')\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    return out.stdout.strip() == "yes"


def inject() -> None:
    if control_running():
        print("CONTROL already running; skip injection")
        return
    args = [
        str(LAUNCHER), "--headless", "--timeout-ms", "180000",
        "--override-settings", str(SETTINGS_OVERRIDE),
        "--override-module", str(MODULE_OVERRIDE),
    ]
    print("injecting CONTROL ...")
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(result.stdout.strip())
    if result.returncode != 0:
        sys.exit(f"injection failed, exit {result.returncode}")
