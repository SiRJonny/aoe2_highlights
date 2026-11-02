"""Probe CONTROL pipes: prints name -> EXISTS/NO for each."""
import sys

import pywintypes
import win32file

for name in ["AoE2ControlRmsIdeV1", "AoE2_Highlights"]:
    try:
        win32file.CreateFile(f"\\\\.\\pipe\\{name}", 0x80000000 | 0x40000000,
                             0, None, 3, 0, None)
        print(name, "EXISTS")
    except pywintypes.error:
        print(name, "NO")
