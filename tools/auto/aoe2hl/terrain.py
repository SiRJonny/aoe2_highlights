"""Minimap-style terrain image from a replay's mgz header.

mgz's full summary parses the DE header (67.2/68.0 with the local patch
applied) and exposes every tile as {x, y, elevation, terrain_id}. This
renders a top-down PNG: one color per terrain id, slight brightness per
elevation level. Purely cosmetic — the webapp draws objects on top in
player colors, so exact in-game colors don't matter, only that water,
grass, sand and dirt are distinguishable.
"""
from pathlib import Path

import cv2
import numpy as np

# Terrain id -> (R, G, B). DE ids seen on random land maps: grass shades
# (13, 14, 22, 23), water (1, 3), shore/sand (2, 48), misc gaia (0-12).
TERRAIN_COLORS = {
    0: (110, 170, 110),
    1: (190, 160, 70),      # water -> blue (see below)
    2: (134, 168, 60),      # shore/sand
    3: (170, 140, 50),      # deep water
    13: (25, 85, 25),       # forest (tree canopy blobs)
    14: (50, 120, 50),      # main grass
    22: (80, 140, 80),      # grass alt
    23: (60, 105, 55),      # scrub / forest floor
    48: (110, 140, 120),
}
WATER_IDS = {1, 3, 4, 10, 11, 12}
FALLBACK = (90, 140, 90)

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
            c = (200, 150, 60)
        elif tid == 2 or tid == 48:
            c = (110, 170, 200)          # sandy shore
        else:
            c = TERRAIN_COLORS.get(tid, FALLBACK)
        img[y, x] = c
        elev[y, x] = t.get("elevation", 0)
    # elevation shading (skip water): ±10 per level around 0
    lift = (elev.astype(np.int16) * 10).clip(-40, 40)
    img = img.astype(np.int16) + lift[..., None]
    return img.clip(0, 255).astype(np.uint8)


def render_tiles_png(tiles, dimension, out_png, px=MAP_PX):
    img = _tile_array(tiles, dimension)
    img = cv2.resize(img, (px, px), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out_png), img)
    return out_png


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
        img = np.full((px, px, 3), (50, 120, 50), dtype=np.uint8)
        cv2.imwrite(str(out_png), img)
        return out_png, None
