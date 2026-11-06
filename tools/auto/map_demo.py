"""Synthetic map-animation dataset (out/demo/) to develop/test the webapp
without the game: 3 players on a 120x120 map, TCs + houses + villagers
wandering + two squads that march to the middle and fight at t=60.

    py -3.12 tools\\auto\\map_demo.py
    py -3.12 tools\\auto\\map_app.py --tag demo
"""
import json
import math
import random
from pathlib import Path

import numpy as np
import cv2

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out" / "demo"
DIM = 120
SECS = 120

TYPE_NAMES = {"0": "TOWN_CENTER", "1": "CASTLE", "2": "HOUSE",
              "3": "BARRACKS", "4": "MAN_AT_ARMS", "5": "ARCHER",
              "6": "VILLAGER"}


def tc(x, y): return [x, y, 0]
def house(x, y): return [x, y, 2]
def barracks(x, y): return [x, y, 3]


def walk(pos, home, r, rng):
    a = rng.random() * 2 * math.pi
    nx = pos[0] + math.cos(a) * 0.8
    ny = pos[1] + math.sin(a) * 0.8
    if abs(nx - home[0]) > r or abs(ny - home[1]) > r:
        a = math.atan2(home[1] - pos[1], home[0] - pos[0])
        nx = pos[0] + math.cos(a) * 0.8
        ny = pos[1] + math.sin(a) * 0.8
    return [round(nx, 1), round(ny, 1)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)

    players = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"},
               {"id": 3, "name": "Carol"}]
    bases = {1: (30, 30), 2: (90, 30), 3: (60, 90)}

    # squads: p1+2 march to the middle, fight at t=60 (casualties vanish);
    # member 0-1 infantry (type 4), member 2 archer, member 3 cavalry
    squads = {
        1: [[35.0, 35.0], [36.0, 34.5], [34.6, 36.2], [35.5, 37.0]],
        2: [[85.0, 85.0], [84.2, 84.6], [85.6, 84.1], [84.0, 82.8]],
    }
    march = {1: [60, 60], 2: [60, 60]}
    dead = {1: set(), 2: set()}

    villies = {p: [] for p in bases}
    for p, (bx, by) in bases.items():
        for k in range(8):
            villies[p].append([bx + rng.uniform(-6, 6),
                               by + rng.uniform(-6, 6)])

    with open(OUT / "positions.jsonl", "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "type": "meta", "time": 0, "mapWidth": DIM, "mapHeight": DIM,
            "players": players, "sampleInterval": 1.0,
            "typeNames": TYPE_NAMES,
        }) + "\n")
        fh.write(json.dumps({
            "type": "names",
            "names": {"1": {"name": "Alice", "colorId": 0, "team": 1},
                      "2": {"name": "Bob", "colorId": 7, "team": 2},
                      "3": {"name": "Carol", "colorId": 5, "team": 1}},
        }) + "\n")

        for t in range(SECS + 1):
            b, v, m = [], [], []
            for p, (bx, by) in bases.items():
                b.append([1, p, 0, bx, by])                 # TC
                b.append([100 + p, p, 2, bx + 5, by + 2])   # house
                if t >= 40:                                  # barracks up
                    b.append([200 + p, p, 3, bx - 5, by + 4])
                if t >= 60 and p == 3:
                    b.append([300, p, 1, bx + 3, by - 6])    # Carol castle
                for k, pos in enumerate(villies[p]):
                    v.append([1000 + p * 100 + k, p, 6,
                              round(pos[0], 1), round(pos[1], 1)])
                    villies[p][k] = walk(pos, (bx, by), 7, rng)
            if t >= 30:                                      # squads out
                for p in (1, 2):
                    mx, my = march[p]
                    for k, pos in enumerate(squads[p]):
                        if k in dead[p]:
                            continue
                        if t < 60:
                            a = math.atan2(my - pos[1], mx - pos[0])
                            pos[0] += math.cos(a) * 0.9
                            pos[1] += math.sin(a) * 0.9
                        # row[5] = class tag: i infantry, c cavalry
                        m.append([2000 + p * 100 + k, p,
                                  4 if k < 2 else (5 if k == 2 else 38),
                                  round(pos[0], 1), round(pos[1], 1),
                                  "i" if k < 2 else ("c" if k == 3 else None)])
            if t == 60:
                dead[1] = {0}            # one casualty each side
                dead[2] = {2}
            age = 0 if t < 40 else (1 if t < 80 else 2)
            stats = [None]
            for p in (1, 2, 3):
                stats.append([age, 800 + t * 12, 600 + t * 9,
                              200 + t * 2, 350 + t * 5, 25 + t // 10,
                              1200 + t * 14 + p * 40])
            fh.write(json.dumps({"type": "stats", "time": t,
                                 "stats": stats}) + "\n")
            fh.write(json.dumps({
                "type": "objects", "time": t, "b": b, "v": v, "m": m,
            }) + "\n")

    # terrain: grass (official id 0) + lake bottom-left + forest blobs
    rng2 = np.random.default_rng(3)
    tiles = []
    for y in range(DIM):
        for x in range(DIM):
            tid = 0
            d = math.hypot(x - 20, y - 100)
            if d < 18:
                tid = 1 if d < 15 else 2
            for cx, cy, r in ((35, 35, 5), (80, 60, 6), (55, 80, 5),
                              (95, 25, 4), (25, 65, 4)):
                if math.hypot(x - cx, y - cy) < r:
                    tid = 13
            if tid == 0 and rng2.random() < 0.01:
                tid = 100
            tiles.append({"x": x, "y": y, "elevation": 0, "terrain_id": tid})

    sys_path = str(Path(__file__).parent)
    import sys
    if sys_path not in sys.path:
        sys.path.insert(0, sys_path)
    from aoe2hl import terrain
    terrain.render_tiles_png(tiles, DIM, OUT / "map.png")
    print(f"demo data -> {OUT} ({SECS + 1} snapshots)")


if __name__ == "__main__":
    main()
