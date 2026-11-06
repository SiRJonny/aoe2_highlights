"""Minimap-style terrain image from a replay's mgz header.

mgz's full summary parses the DE header (67.2/68.0 with the local patch
applied) and exposes every tile as {x, y, elevation, terrain_id}. This
renders a top-down PNG close to the in-game DE minimap: same rotation
(the webapp applies it), similar terrain palette (royal-blue water,
olive-green grass, sand shore, dark-green forest) and a brightness step
per elevation level.
"""
from pathlib import Path

import cv2
import numpy as np

# Terrain id -> (R, G, B), tuned after the in-game DE minimap:
# grass shades (13 forest, 14 main, 22 alt, 23 scrub), water 1 shallow /
# 3 deep (adjacency-checked on a nomad replay), shore/sand (2, 48).
TERRAIN_COLORS = {
    0: (128, 108, 66),       # dirt / gaia patch
    1: (52, 98, 205),        # shallow water
    2: (203, 180, 108),      # sandy shore
    3: (32, 62, 158),        # deep water
    13: (26, 62, 20),        # forest (tree canopy blobs)
    14: (76, 106, 38),       # main grass (olive green)
    22: (86, 116, 46),       # grass alt
    23: (62, 90, 34),        # scrub / forest floor
    48: (188, 168, 100),     # sand
}
WATER_IDS = {1, 3, 4, 10, 11, 12}
FALLBACK = (88, 122, 42)

MAP_PX = 1080            # rendered square resolution


def _tile_array(tiles, dimension):
    """tiles (mgz dicts) -> (dimension, dimension, 3) uint8 BGR array."""
    img = np.zeros((dimension, dimension, 3), dtype=np.uint8)
    elev = np.zeros((dimension, dimension), dtype=np.uint8)
    for t in tiles:
        x, y, tid = t["x"], t["y"], t["terrain_id"]
        if not (0 <= x < dimension and 0 <= y < dimension):
            continue
        if tid in WATER_IDS:
            c = TERRAIN_COLORS.get(tid, TERRAIN_COLORS[1])
        else:
            c = TERRAIN_COLORS.get(tid, FALLBACK)
        img[y, x] = (c[2], c[1], c[0])          # table is RGB, cv2 is BGR
        elev[y, x] = t.get("elevation", 0)
    # elevation shading (not on water): one visible step per level, as
    # in-game; base level ~2 renders unlifted
    lift = ((elev.astype(np.int16) - 2) * 14).clip(-56, 56)
    img = img.astype(np.int16) + lift[..., None]
    return img.clip(0, 255).astype(np.uint8)


def render_tiles_png(tiles, dimension, out_png, px=MAP_PX):
    img = _tile_array(tiles, dimension)
    img = cv2.resize(img, (px, px), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out_png), img)
    return out_png


def player_info(replay_path):
    """player number -> {name, colorId, team} from the replay's mgz header.

    colorId is the DE lobby color index (0=Blue,1=Red,2=Green,3=Yellow,
    4=Teal,5=Purple,6=Gray,7=Orange per mgz.reference) — the color the
    player actually used in-game, which need not match their slot.
    team is the team number from the lobby (players grouped as in-game).
    Empty dict when the header can't be parsed."""
    try:
        import mgz.summary as mgz_summary
        with open(replay_path, "rb") as fh:
            summ = mgz_summary.Summary(fh)
            team_of = {}
            for ti, members in enumerate(summ.get_teams()):
                for pid in members:
                    team_of[pid] = ti + 1
            info = {}
            for pl in summ.get_players():
                if not pl.get("human"):
                    continue
                info[pl["number"]] = {"name": pl["name"],
                                      "colorId": pl["color_id"],
                                      "team": team_of.get(pl["number"], 0)}
            return info
    except Exception:                        # noqa: BLE001
        return {}


def render_replay_png(replay_path, out_png, px=MAP_PX):
    """Terrain PNG + map name from a replay file; returns (png, name).

    Falls back to a flat grass image (name None) when the header can't
    be parsed — the animation still works, just without real terrain."""
    try:
        import mgz.summary as mgz_summary
        with open(replay_path, "rb") as fh:
            summ = mgz_summary.Summary(fh)
            m = summ.get_map()
            name = m.get("name")
            render_tiles_png(m["tiles"], m["dimension"], out_png, px)
            return out_png, name
    except Exception as exc:                       # noqa: BLE001
        print(f"terrain: mgz parse failed ({type(exc).__name__}: "
              f"{str(exc)[:80]}); flat fallback")
        img = np.full((px, px, 3), (50, 120, 50)[::-1], dtype=np.uint8)
        cv2.imwrite(str(out_png), img)
        return out_png, None
