"""Smoke test: fast parse complete DE replay (header + body ops)."""
import os
import sys
import collections

from mgz.fast import header as fast_header
from mgz import fast


def main(path):
    with open(path, 'rb') as d:
        eof = os.fstat(d.fileno()).st_size
        h = fast_header.parse(d)
        print("file:", os.path.basename(path))
        print("game_version:", h["game_version"], "save_version:", h["save_version"])
        for p in h["players"]:
            print("  player", p["number"], "name-len-ok", bool(p.get("name")),
                  "civ", p.get("civilization_id"), "type", p.get("type"), "profile", p.get("profile_id"))
        fast.meta(d)
        counts = collections.Counter()
        bad = 0
        while d.tell() < eof:
            try:
                o = fast.operation(d)
            except Exception:
                bad += 1
                break
            t = o[0].name
            counts[str(t)] += 1
        print("ops:", dict(counts), "bad:", bad, "pos:", d.tell(), "/", eof)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
