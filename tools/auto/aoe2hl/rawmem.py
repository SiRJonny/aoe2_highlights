"""ReadProcessMemory access to CONTROL's packed snapshot buffers.

GetObjectsPtr() / GetMapTilesPtr() return (ptr, count): an address in
the game process and an entry count. The Lua module ships the two
numbers over the pipe; the external program reads the packed
little-endian entries with one ReadProcessMemory call (AoE2Control IPC
API, "Snapshot Buffers For IPC / ML"). The buffer stays valid until the
module requests a buffer in a later callback or unloads.
"""

import ctypes
import struct

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = ctypes.c_void_p
_k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
_k32.ReadProcessMemory.restype = ctypes.c_int
_k32.ReadProcessMemory.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

# Object entry, 12 bytes packed, little-endian: uint32 id,
# uint16 unitObjectType, uint16 x, uint16 y (tile), uint8 playerId
# (255 = no owner), uint8 flags (bit 0 alive).
OBJ_STRUCT = "<IHHHBB"
OBJ_SIZE = 12


def read_buffer(pid, ptr, nbytes):
    """Reads nbytes at ptr in the game process, returns the bytes."""
    handle = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
                              False, pid)
    if not handle:
        raise OSError("OpenProcess(%d) failed: error %d"
                      % (pid, ctypes.get_last_error()))
    try:
        buf = ctypes.create_string_buffer(nbytes)
        got = ctypes.c_size_t(0)
        if not _k32.ReadProcessMemory(handle, ctypes.c_void_p(ptr), buf,
                                      nbytes, ctypes.byref(got)):
            raise OSError("ReadProcessMemory failed: error %d"
                          % ctypes.get_last_error())
        return buf.raw[:got.value]
    finally:
        _k32.CloseHandle(handle)


def parse_objects(data):
    """Parses packed object entries into
    (id, unitObjectType, x, y, playerId, alive) tuples."""
    return [struct.unpack_from(OBJ_STRUCT, data, off)
            for off in range(0, len(data) - 11, 12)]
