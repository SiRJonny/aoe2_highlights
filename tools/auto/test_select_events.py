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
    assert se.PADS[3] == (5.0, 2.0) and se.PADS[4] == (3.0, 6.0)
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


def test_vill_animal_bottom_prio():
    # a wolf kill produces a prio-8 candidate; a plain vill_death must not
    rows = [
        {"type": "vill_death", "time": 300, "player": 1,
         "x": 20, "y": 30, "id": 21, "by_animal": "WOLF",
         "animal_id": 99, "animal_x": 21, "animal_y": 30},
        {"type": "vill_death", "time": 305, "player": 2,
         "x": 80, "y": 90, "id": 22},                      # no attribution
        {"type": "replay_length", "time": 500},
    ]
    cands = se.build_candidates(rows)
    p8 = [c for c in cands if c["prio"] == 8]
    assert len(p8) == 1, p8
    assert p8[0]["kind"] == "vill_animal" and p8[0]["x"] == 20
    assert p8[0]["info"]["animal"] == "WOLF"
    assert se.PADS[8] == (2.0, 3.0)
    # overlaps are padded: wolf kill t=300 +- (2,3) = 298..303
    assert se.overlaps(p8[0], {"prio": 8, "t0": 302, "t1": 302})
    print("ok test_vill_animal_bottom_prio")


def test_resigned_player_filtered():
    # player 2 resigns at 500: census drops after that are cleanup, and
    # their TC loss post-resign must not fire either
    rows = [vill_pop(t, {2: 20}) for t in range(0, 600, 1)]
    rows += [vill_pop(600, {2: 17}), vill_pop(601, {2: 16})]
    rows += [vill_pop(t, {2: 16}) for t in range(602, 700, 1)]
    rows += [{"type": "vill_death", "time": 600, "player": 2,
              "x": 10, "y": 10, "id": 1},
             {"type": "resign", "time": 500, "player": 2},
             {"type": "bld_event", "event": "lost", "isTC": True,
              "isCastle": False, "name": "TOWN_CENTER", "id": 9,
              "owner": 2, "time": 650, "x": 40, "y": 40},
             {"type": "replay_length", "time": 800}]
    cands = se.build_candidates(rows)
    assert not [c for c in cands if c["prio"] == 1], cands
    assert not [c for c in cands if c["prio"] == 3], cands
    # a drop BEFORE the resign still fires
    rows = [vill_pop(t, {2: 20}) for t in range(0, 460, 1)]
    rows += [vill_pop(460, {2: 17}), vill_pop(461, {2: 16})]
    rows += [vill_pop(t, {2: 16}) for t in range(462, 500, 1)]
    rows += [{"type": "vill_death", "time": 460, "player": 2,
              "x": 10, "y": 10, "id": 2},
             {"type": "resign", "time": 500, "player": 2},
             {"type": "replay_length", "time": 800}]
    cands = se.build_candidates(rows)
    assert [c for c in cands if c["prio"] == 1]
    print("ok test_resigned_player_filtered")


def test_battle_extension_and_interrupt():
    # base vill loss 700-702; deaths keep coming every ~2 s until 708,
    # then a 3 s gap ends the chain
    rows = [vill_pop(t, {1: 20}) for t in range(0, 700, 1)]
    rows += [vill_pop(700, {1: 19}), vill_pop(701, {1: 18}),
             vill_pop(702, {1: 17})]
    rows += [vill_pop(t, {1: 17}) for t in range(703, 800, 1)]
    for i, dt in enumerate((704.0, 706.0, 708.0)):
        rows.append({"type": "unit_death", "time": dt, "player": 3,
                     "x": 100 + i, "y": 50, "id": 100 + i})
    rows.append({"type": "vill_death", "time": 700, "player": 1,
                 "x": 100, "y": 50, "id": 1})
    rows.append({"type": "replay_length", "time": 1200})
    segs = se.solve_overlaps(se.build_candidates(rows))
    p1 = [s for s in segs if s["prio"] == 1]
    assert len(p1) == 1 and p1[0]["t1"] == 708, p1
    assert p1[0]["info"].get("extended") is True, p1[0]
    assert not [s for s in segs if s["prio"] == 9], segs
    # an interrupting TC fall inside the extension kills the extension
    # (t=709: overlaps the extension's padded span 700..711 but not the
    # base's 698..704)
    rows.append({"type": "bld_event", "event": "lost", "isTC": True,
                 "isCastle": False, "name": "TOWN_CENTER", "id": 9,
                 "owner": 3, "time": 709, "x": 103, "y": 51})
    segs = se.solve_overlaps(se.build_candidates(rows))
    p1 = [s for s in segs if s["prio"] == 1]
    assert p1[0]["t1"] == 702, p1           # extension dropped
    assert [s for s in segs if s["prio"] == 3], segs
    print("ok test_battle_extension_and_interrupt")


def test_late_gate():
    # after minute 30, a drop of <10 units does not trigger; >=10 does
    base = [vill_pop(t, {1: 20}) for t in range(0, 1900, 1)]
    rows = base + [vill_pop(1850, {1: 19}), vill_pop(1851, {1: 18}),
                   vill_pop(1852, {1: 17})]
    rows += [vill_pop(t, {1: 17}) for t in range(1853, 2000, 1)]
    rows += [{"type": "vill_death", "time": 1850, "player": 1,
              "x": 10, "y": 10, "id": 1},
             {"type": "replay_length", "time": 2100}]
    assert not [c for c in se.build_candidates(rows) if c["prio"] == 1]
    rows += [vill_pop(1950, {1: 14}), vill_pop(1951, {1: 11}),
             vill_pop(1952, {1: 7})]
    rows += [vill_pop(t, {1: 7}) for t in range(1953, 2000, 1)]
    rows.append({"type": "vill_death", "time": 1951, "player": 1,
                 "x": 11, "y": 11, "id": 2})
    got = [c for c in se.build_candidates(rows) if c["prio"] == 1]
    assert got and got[0]["info"]["lost"] == 10, got
    print("ok test_late_gate")


def test_buildings_count_as_units():
    # t > 1200: 4 units (20% of 20) + 3 destroyed buildings (=6) = 10
    # -> passes the late gate; units alone would not
    rows = [army_pop(t, {1: 20}) for t in range(1200, 1400, 1)]
    rows += [army_pop(1400, {1: 18}), army_pop(1401, {1: 17}),
             army_pop(1402, {1: 16}), army_pop(1403, {1: 16})]
    rows += [army_pop(t, {1: 16}) for t in range(1404, 1500, 1)]
    for i in range(4):
        rows.append({"type": "unit_death", "time": 1400 + i * 0.5,
                     "player": 1, "x": 60, "y": 60, "id": 1 + i})
    for i in range(3):
        rows.append({"type": "bld_event", "event": "lost", "isTC": False,
                     "isCastle": False, "name": "STABLE", "id": 20 + i,
                     "owner": 1, "time": 1401 + i, "x": 62 + i, "y": 61})
    rows.append({"type": "replay_length", "time": 1600})
    p2 = [c for c in se.build_candidates(rows) if c["prio"] == 2]
    assert len(p2) == 1, p2
    assert p2[0]["info"]["lost"] == 10, p2[0]
    assert p2[0]["info"]["buildings"] == 3 and p2[0]["info"]["units"] == 4
    print("ok test_buildings_count_as_units")


def test_krepost_is_castle():
    rows = [{"type": "bld_event", "event": "lost", "isTC": False,
             "isCastle": False, "name": "KREPOST", "id": 30,
             "owner": 5, "time": 900, "x": 70, "y": 80},
            {"type": "replay_length", "time": 1200}]
    p4 = [c for c in se.build_candidates(rows) if c["prio"] == 4]
    assert len(p4) == 1 and p4[0]["info"]["name"] == "KREPOST", p4
    print("ok test_krepost_is_castle")


def test_near_tc_midpoint():
    # two enemy TCs in range; camera focuses the midpoint to the NEAREST
    rows = [{"type": "tc_snap", "owner": 2, "x": 50, "y": 50,
             "kind": "TOWN_CENTER"},
            {"type": "tc_snap", "owner": 3, "x": 78, "y": 60,
             "kind": "TOWN_CENTER"},
            {"type": "bld_event", "event": "done", "isTC": False,
             "isCastle": True, "name": "CASTLE", "id": 40,
             "owner": 4, "time": 1500, "x": 62, "y": 58},
            {"type": "replay_length", "time": 2000}]
    p7 = [c for c in se.build_candidates(rows) if c["prio"] == 7]
    assert len(p7) == 1, p7
    assert p7[0]["info"]["midpoint_of"] == 2, p7[0]
    assert abs(p7[0]["x"] - 56) < 0.01 and abs(p7[0]["y"] - 54) < 0.01
    print("ok test_near_tc_midpoint")


def test_resign_team_last():
    # teams row: p1+p2 team 7, p3 team 9; p1 and p2 resign 10 s apart ->
    # only p2 (the last of team 7) and p3 fire; focus = team deaths
    rows = [
        {"type": "teams", "teams": {"1": 7, "2": 7, "3": 9}},
        {"type": "tc_snap", "owner": 3, "x": 200, "y": 200,
         "kind": "TOWN_CENTER"},
        {"type": "unit_death", "time": 1005, "player": 1,
         "x": 50, "y": 50, "id": 1},
        {"type": "unit_death", "time": 1006, "player": 2,
         "x": 51, "y": 51, "id": 2},
        {"type": "resign", "time": 1000, "player": 1},
        {"type": "resign", "time": 1010, "player": 2},
        {"type": "resign", "time": 1100, "player": 3},
        {"type": "replay_length", "time": 1300},
    ]
    p6 = [c for c in se.build_candidates(rows) if c["prio"] == 6]
    assert len(p6) == 2, p6
    last1 = [c for c in p6 if c["info"]["player"] == 2]
    assert last1 and last1[0]["t0"] == 1010, p6
    # focus: densest bucket of team-7 deaths in [1000, 1010] = (50.5, 50.5)
    assert abs(last1[0]["x"] - 50.5) < 0.01, last1[0]
    assert last1[0]["info"]["team_pids"] == [1, 2]
    p3 = [c for c in p6 if c["info"]["player"] == 3]
    assert p3 and p3[0]["x"] == 200 and p3[0]["y"] == 200, p3
    print("ok test_resign_team_last")


def test_castle_build_safe():
    # a castle foundation with no deaths nearby films at FAST (prio 5);
    # a contested one does not fire
    def rows_with(death):
        rows = [{"type": "bld_event", "event": "start", "isTC": False,
                 "isCastle": True, "name": "CASTLE", "id": 50,
                 "owner": 4, "time": 1200, "x": 100, "y": 100},
                {"type": "bld_event", "event": "done", "isTC": False,
                 "isCastle": True, "name": "CASTLE", "id": 50,
                 "owner": 4, "time": 1260, "x": 100, "y": 100},
                {"type": "replay_length", "time": 1500}]
        if death:
            rows.insert(2, {"type": "unit_death", "time": 1250,
                            "player": 4, "x": 103, "y": 102, "id": 9})
        return rows
    p5 = [c for c in se.build_candidates(rows_with(False))
          if c["prio"] == 5]
    assert len(p5) == 1 and p5[0]["speedName"] == "FAST", p5
    assert p5[0]["t0"] == 1200 and p5[0]["t1"] == 1260
    assert not [c for c in se.build_candidates(rows_with(True))
                if c["prio"] == 5]
    print("ok test_castle_build_safe")


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
    test_vill_animal_bottom_prio()
    test_resigned_player_filtered()
    test_battle_extension_and_interrupt()
    test_late_gate()
    test_buildings_count_as_units()
    test_krepost_is_castle()
    test_near_tc_midpoint()
    test_resign_team_last()
    test_castle_build_safe()
    test_zero_events()
    print("all select_events tests passed")
