"""Unit tests for select_events v2 (priority planner). Run:
    py -3.12 tools\\auto\\test_select_events.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import select_events as se


def vill_pop(t, counts):
    return {"type": "vill_pop", "time": t,
            "counts": {str(k): v for k, v in counts.items()}}


def army_pop(t, counts):
    return {"type": "army_pop", "time": t,
            "counts": {str(k): v for k, v in counts.items()}}


def test_vill_loss():
    # player 1: 20 vills, raided at t=700..702 (20 -> 19 -> 18 -> 17, 15%)
    rows = [vill_pop(t, {1: 20}) for t in range(0, 700, 1)]
    rows += [vill_pop(700, {1: 19}), vill_pop(701, {1: 18}),
             vill_pop(702, {1: 17})]
    rows += [vill_pop(t, {1: 17}) for t in range(703, 800, 1)]
    rows += [{"type": "vill_death", "time": 700, "player": 1,
              "x": 100, "y": 50, "id": 1},
             {"type": "vill_death", "time": 701, "player": 1,
              "x": 102, "y": 52, "id": 2},
             {"type": "replay_length", "time": 1000}]
    cands = se.build_candidates(rows)
    p1 = [c for c in cands if c["prio"] == 1]
    assert len(p1) == 1, f"expected 1 vill_loss, got {p1}"
    assert p1[0]["t0"] == 700 and p1[0]["t1"] == 702, p1[0]
    assert p1[0]["x"] == 101 and p1[0]["y"] == 51, p1[0]
    assert p1[0]["info"]["lost"] == 3
    print("ok test_vill_loss")


def test_army_loss():
    # player 2: 30 army -> battle at t=500..504 (30 -> 22, 26.7%)
    rows = [army_pop(t, {2: 30}) for t in range(0, 500, 1)]
    rows += [army_pop(500, {2: 28}), army_pop(501, {2: 26}),
             army_pop(502, {2: 25}), army_pop(503, {2: 24}),
             army_pop(504, {2: 22})]
    rows += [army_pop(t, {2: 22}) for t in range(505, 600, 1)]
    rows.append({"type": "unit_death", "time": 502, "player": 2,
                 "x": 200, "y": 300, "id": 9})
    cands = se.build_candidates(rows)
    p2 = [c for c in cands if c["prio"] == 2]
    assert len(p2) == 1, p2
    assert p2[0]["t0"] == 500 and p2[0]["t1"] == 504, p2[0]
    assert p2[0]["x"] == 200 and p2[0]["y"] == 300
    print("ok test_army_loss")


def test_tc_and_castle_fall():
    rows = [
        {"type": "bld_event", "event": "lost", "isTC": True,
         "isCastle": False, "name": "TOWN_CENTER", "id": 5, "owner": 3,
         "time": 800, "x": 60, "y": 70},
        {"type": "bld_event", "event": "lost", "isTC": False,
         "isCastle": True, "name": "CASTLE", "id": 6, "owner": 4,
         "time": 900, "x": 110, "y": 120},
        {"type": "replay_length", "time": 1200},
    ]
    cands = se.build_candidates(rows)
    p3 = [c for c in cands if c["prio"] == 3]
    p4 = [c for c in cands if c["prio"] == 4]
    assert len(p3) == 1 and p3[0]["t0"] == 800 and p3[0]["x"] == 60
    assert len(p4) == 1 and p4[0]["t0"] == 900
    assert se.PADS[3] == (5.0, 2.0) and se.PADS[4] == (3.0, 4.0)
    print("ok test_tc_and_castle_fall")


def test_resign_position_last_bld():
    rows = [
        {"type": "bld_event", "event": "lost", "isTC": False,
         "isCastle": False, "name": "BARRACKS", "id": 7, "owner": 5,
         "time": 1000, "x": 40, "y": 44},
        {"type": "vill_death", "time": 1005, "player": 5,
         "x": 200, "y": 200, "id": 10},
        {"type": "resign", "time": 1008, "player": 5},
        {"type": "replay_length", "time": 1200},
    ]
    cands = se.build_candidates(rows)
    p6 = [c for c in cands if c["prio"] == 6]
    assert len(p6) == 1, p6
    assert (p6[0]["x"], p6[0]["y"]) == (40, 44), p6[0]  # last bld won
    print("ok test_resign_position_last_bld")


def test_castle_near_enemy_tc():
    rows = [
        {"type": "tc_snap", "owner": 2, "x": 50, "y": 50, "kind": "TOWN_CENTER"},
        {"type": "bld_event", "event": "done", "isTC": False,
         "isCastle": True, "name": "CASTLE", "id": 11, "owner": 3,
         "time": 1500, "x": 62, "y": 58},          # 14.4 tiles from p2 TC
        {"type": "replay_length", "time": 2000},
    ]
    cands = se.build_candidates(rows)
    p7 = [c for c in cands if c["prio"] == 7]
    assert len(p7) == 1, p7
    assert p7[0]["info"]["enemy_tcs"][0][0] == 2
    # and one too far away: 40 tiles
    rows[1]["x"], rows[1]["y"] = 90, 90
    cands = se.build_candidates(rows)
    assert not [c for c in cands if c["prio"] == 7]
    print("ok test_castle_near_enemy_tc")


def test_overlap_higher_prio_wins():
    rows = [
        # villager loss right at a TC fall: prio 3 must win
        {"type": "bld_event", "event": "lost", "isTC": True,
         "isCastle": False, "name": "TOWN_CENTER", "id": 5, "owner": 3,
         "time": 800, "x": 60, "y": 70},
    ]
    rows += [vill_pop(t, {1: 20}) for t in range(0, 800, 1)]
    rows += [vill_pop(800, {1: 19}), vill_pop(801, {1: 18}),
             vill_pop(802, {1: 17})]
    rows += [vill_pop(t, {1: 17}) for t in range(803, 900, 1)]
    rows.append({"type": "vill_death", "time": 800, "player": 1,
                 "x": 61, "y": 71, "id": 12})
    rows.append({"type": "replay_length", "time": 1200})
    cands = se.build_candidates(rows)
    segs = se.solve_overlaps(cands)
    kinds = [s["prio"] for s in segs]
    # prio 1 outranks prio 3 (user list order: 1 = most important)
    assert 1 in kinds and 3 not in kinds, (cands, segs)
    print("ok test_overlap_higher_prio_wins")


def test_same_prio_merge():
    rows = [
        {"type": "bld_event", "event": "lost", "isTC": True,
         "isCastle": False, "name": "TOWN_CENTER", "id": 5, "owner": 3,
         "time": 800, "x": 60, "y": 70},
        {"type": "bld_event", "event": "lost", "isTC": True,
         "isCastle": False, "name": "TOWN_CENTER", "id": 6, "owner": 4,
         "time": 804, "x": 61, "y": 71},
        {"type": "replay_length", "time": 1200},
    ]
    cands = se.build_candidates(rows)
    segs = se.solve_overlaps(cands)
    p3 = [s for s in segs if s["prio"] == 3]
    assert len(p3) == 1, p3              # merged into one segment
    assert p3[0]["t0"] == 800 and p3[0]["t1"] == 804
    print("ok test_same_prio_merge")


def test_zero_events():
    rows = [{"type": "replay_length", "time": 500},
            vill_pop(1, {1: 30})]
    cands = se.build_candidates(rows)
    segs = se.solve_overlaps(cands)
    assert segs == []
    print("ok test_zero_events (no video case)")


if __name__ == "__main__":
    test_vill_loss()
    test_army_loss()
    test_tc_and_castle_fall()
    test_resign_position_last_bld()
    test_castle_near_enemy_tc()
    test_overlap_higher_prio_wins()
    test_same_prio_merge()
    test_zero_events()
    print("all select_events tests passed")
