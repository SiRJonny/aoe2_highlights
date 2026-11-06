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

# Terrain id -> (R, G, B), straight from the official DE minimap colors
# in SiegeEngineers/aoc-reference-data data/datasets/100.json (grouped by
# identical "level" color): grass shades, shallow/medium/deep water,
# beach, dirt, farm, forest (deeper green than grass, as in-game), ice.
TERRAIN_COLORS = {}
for _ids, _c in [
    ((0, 5, 9, 12, 16, 60, 68, 69, 70, 71, 72, 73, 74, 76, 77, 83, 84,
      85, 86, 87, 100), (51, 151, 39)),      # grass / dry grass
    ((1, 15, 28, 95, 96, 97, 98, 99), (48, 93, 182)),     # water shallow
    ((2, 14, 45, 46, 51, 52, 53, 79, 80, 81, 82, 101, 102, 107, 108,
      109), (232, 180, 120)),                # beach / desert / sand
    ((3, 6, 11, 24, 25, 27, 36, 40, 41, 42, 75, 78), (228, 162, 82)),
    ((4, 54, 59, 90, 93, 94), (84, 146, 176)),            # shallows
    ((7, 8, 29, 30, 31, 32, 63, 64, 65, 66, 67), (130, 136, 77)),  # farm
    ((10, 13, 17, 18, 19, 20, 21, 48, 49, 50, 55, 56, 88, 89, 91, 92,
      104, 105, 106, 113), (21, 118, 21)),    # forest (deeper green;
      # 113 = the DE forest terrain missing from the reference dataset)
    ((22, 57), (0, 74, 161)),                # water deep
    ((23,), (0, 74, 187)),                   # water medium
    ((26, 35, 37), (152, 192, 240)),         # ice
    ((47,), (28, 28, 28)),                   # black
    ((58,), (0, 84, 176)),                   # water azure
]:
    for _i in _ids:
        TERRAIN_COLORS[_i] = _c
WATER_IDS = {1, 3, 4, 10, 11, 12, 15, 22, 23, 28, 54, 57, 58, 59, 90,
             93, 94, 95, 96, 97, 98, 99}
FALLBACK = (51, 151, 39)

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
    water = np.zeros((dimension, dimension), dtype=bool)
    for t in tiles:
        x, y = t["x"], t["y"]
        if 0 <= x < dimension and 0 <= y < dimension:
            water[y, x] = t["terrain_id"] in WATER_IDS
    lift = ((elev.astype(np.int16) - 2) * 14).clip(-56, 56)
    lift[water] = 0
    img = img.astype(np.int16) + lift[..., None]
    return img.clip(0, 255).astype(np.uint8)


def render_tiles_png(tiles, dimension, out_png, px=MAP_PX):
    img = _tile_array(tiles, dimension)
    img = cv2.resize(img, (px, px), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out_png), img)
    return out_png


# Civilization names by mgz header index — the game's own
# dat/civilizations.json civilization_list (index 0 = Gaia), snapshot
# from the local DE install so no game files are needed at runtime.
CIVS = [
    "Gaia", "Britons", "Franks", "Goths", "Teutons", "Japanese", "Chinese",
    "Byzantines", "Persians", "Saracens", "Turks", "Vikings", "Mongols",
    "Celts", "Spanish", "Aztecs", "Mayans", "Huns", "Koreans", "Italians",
    "Indians", "Incas", "Magyars", "Slavs", "Portuguese", "Ethiopians",
    "Malians", "Berbers", "Khmer", "Malay", "Burmese", "Vietnamese",
    "Bulgarians", "Tatars", "Cumans", "Lithuanians", "Burgundians",
    "Sicilians", "Poles", "Bohemians", "Dravidians", "Bengalis", "Gurjaras",
    "Romans", "Armenians", "Georgians", "Achaemenids", "Athenians",
    "Spartans", "Shu", "Wu", "Wei", "Jurchens", "Khitans", "Macedonians",
    "Thracians", "Puru", "Muisca", "Mapuche", "Tupi", "Saxons",
    "Varangians", "Danes",
]


def player_info(replay_path):
    """player number -> {name, colorId, team, civ} from the mgz header.

    colorId is the DE lobby color index (0=Blue,1=Red,2=Green,3=Yellow,
    4=Teal,5=Purple,6=Gray,7=Orange per mgz.reference) — the color the
    player actually used in-game, which need not match their slot.
    team is the team number from the lobby (players grouped as in-game).
    civ is the civilization name (CIVS lookup, empty when unknown).
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
                civ_idx = pl.get("civilization")
                civ = CIVS[civ_idx] if isinstance(civ_idx, int) \
                    and 0 <= civ_idx < len(CIVS) else ""
                info[pl["number"]] = {"name": pl["name"],
                                      "colorId": pl["color_id"],
                                      "team": team_of.get(pl["number"], 0),
                                      "civ": civ}
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
