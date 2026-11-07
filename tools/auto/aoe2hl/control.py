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


def unload_key():
    """The CONTROL module's unload hotkey from settings.timeline.ini
    (VK code) mapped to a pyautogui key name. Delete was the original
    key — on the replay list it hits the delete-replay confirmation,
    so the default is now Insert."""
    vk_names = {46: "delete", 45: "insert", 36: "home", 35: "end",
                33: "pageup", 34: "pagedown", 19: "pause",
                123: "f12", 144: "numlock", 27: "esc"}
    ini = ROOT / "tools" / "aoe2control" / "settings.timeline.ini"
    vk = 45
    try:
        for line in ini.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("unload key"):
                vk = int(line.split("=")[1].strip())
                break
    except Exception:                        # noqa: BLE001
        pass
    return vk_names.get(vk, "insert")


def unload_module(max_tries=3):
    """Press the module's unload hotkey until the pipe is gone.

    Defuse: when the replay list is open, click into the searchbar text
    field first so a Delete keypress edits text instead of triggering
    the delete-replay confirmation. Tries the configured key, then
    'delete' (the previous default still live in already-running
    modules)."""
    from aoe2hl import gameui
    import time
    keys = [unload_key(), "delete"]
    for attempt in range(max_tries):
        gameui.focus_window()
        sb = gameui.locate_anchor("replays_searchbar")
        if sb:
            gameui.pyautogui.click(sb[0], sb[1])
            time.sleep(0.3)
        for key in keys:
            gameui.pyautogui.press(key)
            time.sleep(2)
            if not control_running():
                return True
    return not control_running()


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
