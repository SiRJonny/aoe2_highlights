"""Game UI helper: screen capture + anchor template matching + clicks.

Anchors are small PNG crops in anchors/. Matching uses OpenCV TM_CCOEFF_NORMED
on grayscale. Game runs on monitor 1 (mss monitors[1], the main one).
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import mss
import numpy as np
import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

ROOT = Path(__file__).resolve().parents[3]
ANCHORS = ROOT / "anchors"
DEBUG_DIR = ROOT / "out" / "auto_debug"

WINDOW_TITLE = "Age of Empires II: Definitive Edition"


def _box():
    # whole virtual desktop: the game may live on any monitor
    with mss.mss() as sct:
        return sct.monitors[0]


_last_box = {"left": 0, "top": 0}


def dump_screen() -> np.ndarray:
    global _last_box
    box = _box()
    with mss.mss() as sct:
        img = np.array(sct.grab(box))
    _last_box = box
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def screen_offset():
    """Global origin of the last dump_screen grab."""
    return _last_box["left"], _last_box["top"]


def crop_screen(x0, y0, x1, y1):
    """Crop a region given in global screen coordinates."""
    img = dump_screen()
    ox, oy = _last_box["left"], _last_box["top"]
    return img[y0 - oy:y1 - oy, x0 - ox:x1 - ox]


def window_rect():
    """Game window position on the desktop {left, top, right, bottom}."""
    import win32gui
    handle = win32gui.FindWindow(None, WINDOW_TITLE)
    if not handle:
        raise RuntimeError(f"game window not found ({WINDOW_TITLE!r})")
    l, t, r, b = win32gui.GetWindowRect(handle)
    return {"left": l, "top": t, "right": r, "bottom": b}


def save_debug(name="debug"):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"{name}.png"
    cv2.imwrite(str(path), dump_screen())
    return str(path)


def find_anchor(img: np.ndarray, anchor: str, threshold=0.86):
    """Returns (x, y, confidence) of the best match of an anchor PNG in img."""
    template_path = ANCHORS / f"{anchor}.png"
    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        raise FileNotFoundError(template_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    result = cv2.matchTemplate(gray, cv2.cvtColor(template, cv2.COLOR_BGR2GRAY),
                               cv2.TM_CCOEFF_NORMED)
    _, confidence, _, loc = cv2.minMaxLoc(result)
    if confidence < threshold:
        return None
    return loc[0], loc[1], float(confidence)


def locate_anchor(anchor: str, threshold=0.86):
    img = dump_screen()
    hit = find_anchor(img, anchor, threshold)
    if hit is None:
        return None
    x, y, conf = hit
    ox, oy = _last_box["left"], _last_box["top"]
    # anchor mid-point, converted to global screen coordinates
    template = cv2.imread(str(ANCHORS / f"{anchor}.png"))
    cx = x + template.shape[1] // 2 + ox
    cy = y + template.shape[0] // 2 + oy
    return cx, cy, conf, hit


def click_anchor(anchor: str, threshold=0.86, tries=3, pause=1.0, double=False) -> bool:
    """Find an anchor and click its centre. Retries."""
    for i in range(tries):
        hit = locate_anchor(anchor, threshold)
        if hit:
            cx, cy, conf, _ = hit
            focus_window()
            time.sleep(0.15)
            if double:
                pyautogui.doubleClick(cx, cy)
            else:
                press_click(cx, cy)
            time.sleep(pause)
            return True
        time.sleep(pause)
    return False


def hit_anchor(anchor: str, threshold=0.86) -> bool:
    return locate_anchor(anchor, threshold) is not None


def wait_anchor(anchor: str, timeout=30, threshold=0.86, poll=0.6) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if hit_anchor(anchor, threshold):
            return True
        time.sleep(poll)
    return False


def focus_window():
    import win32con
    import win32gui
    import ctypes
    handle = win32gui.FindWindow(None, WINDOW_TITLE)
    if not handle:
        raise RuntimeError(f"game window not found ({WINDOW_TITLE!r})")

    def have_focus():
        return win32gui.GetForegroundWindow() == handle

    for attempt in range(4):
        if have_focus():
            return handle
        try:
            if win32gui.IsIconic(handle):
                win32gui.ShowWindow(handle, win32con.SW_MAXIMIZE)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(handle)
        except Exception:
            pass
        if have_focus():
            return handle
        try:
            # an ALT press+release unlocks SetForegroundWindow for our
            # thread; SwitchToThisWindow is the heavier fallback
            pyautogui.keyDown("alt")
            pyautogui.keyUp("alt")
            win32gui.SetForegroundWindow(handle)
        except Exception:
            pass
        if have_focus():
            return handle
        try:
            ctypes.windll.user32.SwitchToThisWindow(handle, True)
        except Exception:
            pass
        time.sleep(0.3)
        if have_focus():
            return handle
        fg = win32gui.GetForegroundWindow()
        if fg and fg != handle:
            try:
                win32gui.ShowWindow(fg, win32con.SW_MINIMIZE)
            except Exception:
                pass
    if not have_focus():
        raise RuntimeError("could not bring the game window to front")
    return handle


def press_click(x, y, hold_s=0.12):
    """Raw SendInput click with an explicit press-hold-release.

    Some game UI buttons swallow pyautogui's instantaneous click; a hold
    presses the physical device path more convincingly."""
    import ctypes

    # move first with SetCursorPos so hover state settles
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    time.sleep(0.08)

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                    ("mouseData", ctypes.c_uint32), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ctypes.POINTER(ctypes.c_uint32))]

    class INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT)]
        _fields_ = [("type", ctypes.c_uint32), ("u", _U)]

    INPUT_MOUSE = 0
    # dx/dy 0 with no ABSOLUTE flag = stay in place: press and release only

    def send(flags):
        payload = INPUT(type=INPUT_MOUSE, u=INPUT._U(mi=MOUSEINPUT(0, 0, 0, flags, 0, None)))
        ctypes.windll.user32.SendInput(1, ctypes.byref(payload), ctypes.sizeof(payload))

    send(0x0002)
    time.sleep(hold_s)
    send(0x0004)
    time.sleep(0.1)

