"""Smoke test: parse DE replay header with mgz.fast, list players."""
import os
import sys

from mgz.fast import header


def main(path):
    with open(path, 'rb') as d:
        eof = os.fstat(d.fileno()).st_size
        h = header.parse(d)
        print("bytes read:", d.tell(), "of", eof)
        print("header keys:", list(h.keys()))
        print("meta:", h.get("meta"))
        print("players:")
        for p in h.get("players", []):
            s = p.get("strings", {})
            print("  ", p.get("number"), s.get("name"), s.get("civilization"), "team", p.get("team"))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
