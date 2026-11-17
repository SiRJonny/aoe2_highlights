"""Unit tests for nohud (local HUD-removal mod). Run:
    py -3.12 tools\\auto\\test_nohud.py
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import aoe2hl.nohud as nohud


def _sample_collection(widgets):
    """widgetui-shaped doc: 2 widgets, the first with nested ChildWidgets."""
    return {"Collection": {
        "Name": "ResourcePanel",
        "ViewPort": {"xorigin": 0, "yorigin": 0, "width": 1935,
                     "height": 228, "alignment": "TopLeft"},
        "Widgets": [
            {"Widget": {"Type": "Base", "Name": "Background",
                        "ZPlane": 1,
                        "StateMaterials": {"UserStates": 1},
                        "ChildWidgets": [
                            {"Widget": {"Type": "Label", "Name": "Wood",
                                        "Text": "10000",
                                        "ChildWidgets": []}}]}},
            {"Widget": {"Type": "Button", "Name": "Idle",
                        "ChildWidgets": []}},
        ],
        "TrailingKey": "kept"}}


def test_empty_collection():
    doc = _sample_collection(None)
    out = nohud.empty_collection(doc, name="resourcepanel.json")
    coll = out["Collection"]
    assert coll["Widgets"] == [], coll
    # ONLY Widgets changed: same keys in same order, everything else equal
    assert list(coll.keys()) == list(doc["Collection"].keys())
    for k in coll:
        if k != "Widgets":
            assert coll[k] == doc["Collection"][k], k
    assert list(out.keys()) == list(doc.keys())
    # original untouched
    assert doc["Collection"]["Widgets"], "input was mutated"
    print("ok test_empty_collection")


def test_empty_collection_raises():
    for bad in ({}, {"Collection": {"Name": "X"}},
                {"Collection": None},
                {"Other": {}}):
        try:
            nohud.empty_collection(bad)
        except ValueError as e:
            assert "Collection" in str(e) or "Widgets" in str(e), e
        else:
            raise AssertionError(f"no ValueError for {bad}")
    print("ok test_empty_collection_raises")


def test_generate_mod():
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "widgetui"
        game.mkdir()
        # plain panel
        (game / "resourcepanel.json").write_text(
            json.dumps(_sample_collection(None)), encoding="utf-8")
        # panel with a BOM
        (game / "scorepanel.json").write_text(
            "\ufeff" + json.dumps(_sample_collection(None)), encoding="utf-8")
        # commandpanel.json deliberately missing -> warn + skip, no crash
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            written = nohud.generate_mod(game, Path(td) / "mod")
        dest = Path(td) / "mod"
        names = sorted(p.name for p in written)
        assert names == ["resourcepanel.json", "scorepanel.json"], names
        assert sorted(p.name for p in (dest / "widgetui").iterdir()) == names
        for name in names:
            doc = json.loads((dest / "widgetui" / name).read_text("utf-8-sig"))
            assert doc["Collection"]["Widgets"] == []
            assert doc["Collection"]["Name"] == "ResourcePanel"
            assert doc["Collection"]["TrailingKey"] == "kept"
        info = json.loads((dest / "info.json").read_text("utf-8"))
        assert info == {"Author": "aoe2hl", "CacheStatus": 0,
                        "Description": "Recording mod: removes in-game HUD "
                                       "for replay capture. Replays only.",
                        "Title": "hl_nohud"}
        assert "commandpanel.json" in out.getvalue()   # warning was logged
        print("ok test_generate_mod")


def _status(entries):
    return {"Mods": entries, "Unsub": []}


def _entry(pid, prio, title):
    return {"CheckSum": "x", "Enabled": True, "LastUpdate": "123",
            "Path": f"subscribed//{pid}_{title}", "Priority": prio,
            "PublishID": 0, "Title": title, "WorkshopID": pid}


def test_enable_priority_and_backup():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "resourcepanel.json").write_text(
            json.dumps(_sample_collection(None)), encoding="utf-8")
        orig = _status([_entry(1, 1, "a"), _entry(2, 5, "b"),
                        _entry(3, 12, "c")])
        raw = json.dumps(orig, separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")

        bak = nohud.enable(pm, game_widgetui=game)
        assert bak == pm / "mod-status.json.bak"
        # .bak is the ORIGINAL, byte for byte
        assert (pm / "mod-status.json.bak").read_bytes() == raw.encode()
        e = nohud.entry(pm)
        assert e["Path"] == "local//hl_nohud", e
        assert e["Enabled"] is True
        assert e["Priority"] == 13, e          # max(1,5,12) + 1
        assert e["Title"] == "hl_nohud"
        assert e["LastUpdate"] == "0" and isinstance(e["LastUpdate"], str)
        assert e["CheckSum"] == "0" and e["WorkshopID"] == 0
        assert len(orig["Mods"]) == 3          # other entries still there
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert len(live["Mods"]) == 4
        assert live["Unsub"] == []
        # mod payload generated under local/
        assert json.loads((pm / "local" / "hl_nohud" / "widgetui" /
                           "resourcepanel.json").read_text())[
            "Collection"]["Widgets"] == []
        info = json.loads((pm / "local" / "hl_nohud" /
                           "info.json").read_text())
        assert info["Title"] == "hl_nohud"

        # max priority 24 -> Priority 25 (the real profile's situation)
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(9, 24, "big")])
        (pm / "mod-status.json").write_text(
            json.dumps(orig, separators=(",", ":")), encoding="utf-8")
        nohud.enable(pm, game_widgetui=game)
        assert nohud.entry(pm)["Priority"] == 25, nohud.entry(pm)
    print("ok test_enable_priority_and_backup")


def test_prepare_self_registers_without_entry():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "scorepanel.json").write_bytes(
            _crlf_fixture().encode("utf-8"))
        original = _status([_entry(1, 12, "a"), _entry(2, 24, "b")])
        raw = json.dumps(original, separators=(",", ":")).encode()
        (pm / "mod-status.json").write_bytes(raw)

        backup = nohud.prepare(pm, game_widgetui=game,
                               panels=["scorepanel.json"], solo=True)
        assert backup == pm / "mod-status.json.bak"
        assert backup.read_bytes() == raw
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert not any(m.get("Enabled") for m in live["Mods"])
        assert not any(m.get("Path") in nohud.OUR_PATHS
                       for m in live["Mods"])
        payload = pm / "local" / nohud.MOD_NAME
        assert (payload / "widgetui" / "scorepanel.json").is_file()
        generated = json.loads((payload / "widgetui" /
                                "scorepanel.json").read_text("utf-8"))
        assert generated["Collection"]["ViewPort"]["width"] == 1

        nohud.restore(pm, backup)
        nohud.remove_payload(pm)
        assert (pm / "mod-status.json").read_bytes() == raw
        assert not payload.exists()
    print("ok test_prepare_self_registers_without_entry")


def test_enable_twice():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(1, 12, "a")])
        raw = json.dumps(orig, separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")
        nohud.enable(pm, game_widgetui=game)
        nohud.enable(pm, game_widgetui=game)   # idempotent
        # .bak still the ORIGINAL, not the once-enabled state
        assert (pm / "mod-status.json.bak").read_bytes() == raw.encode()
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        ours = [m for m in live["Mods"] if m["Path"] == "local//hl_nohud"]
        assert len(ours) == 1, live            # upsert, not duplicated
        assert live["Mods"][-1]["Priority"] == 13
    print("ok test_enable_twice")


def test_restore_from_backup():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        raw = json.dumps(_status([_entry(1, 12, "a")]), separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")
        nohud.enable(pm, game_widgetui=game)
        assert nohud.is_enabled(pm)
        nohud.restore(pm)
        assert (pm / "mod-status.json").read_bytes() == raw.encode()
        assert nohud.entry(pm) is None
    print("ok test_restore_from_backup")


def test_restore_without_backup():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(1, 12, "a"), _entry(2, 3, "b")])
        (pm / "mod-status.json").write_text(
            json.dumps(orig, separators=(",", ":")), encoding="utf-8")
        nohud.enable(pm, game_widgetui=game)
        (pm / "mod-status.json.bak").unlink()  # force the defensive path
        nohud.restore(pm)
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert [m["Path"] for m in live["Mods"]] == \
            ["subscribed//1_a", "subscribed//2_b"], live

        # no entry and no backup: no-op, no exception
        nohud.restore(pm)
        assert len(json.loads((pm / "mod-status.json").read_text(
            "utf-8"))["Mods"]) == 2
    print("ok test_restore_without_backup")


def test_entry_and_is_enabled_states():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        assert nohud.entry(pm) is None and not nohud.is_enabled(pm)
        nohud.enable(pm, game_widgetui=game)
        assert nohud.entry(pm)["Path"] == "local//hl_nohud"
        assert nohud.is_enabled(pm) is True
        nohud.restore(pm)
        assert nohud.entry(pm) is None and not nohud.is_enabled(pm)
    print("ok test_entry_and_is_enabled_states")


def test_transform_collection_shrink():
    doc = _sample_collection(None)
    out = nohud.transform_collection(doc, mode="shrink",
                                     name="resourcepanel.json")
    vp = out["Collection"]["ViewPort"]
    assert vp == {"xorigin": 0, "yorigin": 0, "width": 1, "height": 1,
                  "alignment": "TopLeft"}, vp
    # ONLY ViewPort numbers changed: Widgets untouched (same objects),
    # all other keys identical
    assert out["Collection"]["Widgets"] is doc["Collection"]["Widgets"], \
        "Widgets list was copied"
    assert len(out["Collection"]["Widgets"]) == 2
    names = [w["Widget"]["Name"] for w in out["Collection"]["Widgets"]]
    assert names == ["Background", "Idle"], names
    child = out["Collection"]["Widgets"][0]["Widget"]["ChildWidgets"]
    assert child[0]["Widget"]["Name"] == "Wood"
    assert out["Collection"]["Name"] == "ResourcePanel"
    assert out["Collection"]["TrailingKey"] == "kept"
    assert list(out.keys()) == list(doc.keys())
    assert list(out["Collection"].keys()) == list(doc["Collection"].keys())
    # original ViewPort untouched
    assert doc["Collection"]["ViewPort"]["width"] == 1935
    print("ok test_transform_collection_shrink")


def test_transform_collection_shrink_raises():
    try:
        nohud.transform_collection({}, mode="shrink")
    except ValueError as e:
        assert "Collection" in str(e), e
    else:
        raise AssertionError("no ValueError when Collection missing")
    try:
        nohud.transform_collection({"Collection": {"Name": "X"}},
                                   mode="shrink")
    except ValueError as e:
        assert "ViewPort" in str(e), e
    else:
        raise AssertionError("no ValueError when ViewPort missing")
    print("ok test_transform_collection_shrink_raises")


def test_transform_collection_empty_delegates():
    doc = _sample_collection(None)
    out = nohud.transform_collection(doc, mode="empty",
                                     name="resourcepanel.json")
    assert out == nohud.empty_collection(doc, name="resourcepanel.json")
    assert out["Collection"]["Widgets"] == []
    print("ok test_transform_collection_empty_delegates")


def test_transform_collection_unknown_mode():
    for mode in ("nope", "", None):
        try:
            nohud.transform_collection(_sample_collection(None), mode=mode)
        except ValueError as e:
            assert "empty" in str(e) and "shrink" in str(e), e
        else:
            raise AssertionError(f"no ValueError for mode={mode!r}")
    print("ok test_transform_collection_unknown_mode")


def test_generate_mod_shrink():
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "resourcepanel.json").write_text(
            json.dumps(_sample_collection(None)), encoding="utf-8")
        nohud.generate_mod(game, Path(td) / "mod", mode="shrink")
        doc = json.loads((Path(td) / "mod" / "widgetui" /
                          "resourcepanel.json").read_text("utf-8-sig"))
        assert doc["Collection"]["ViewPort"]["width"] == 1
        assert doc["Collection"]["ViewPort"]["height"] == 1
        assert doc["Collection"]["ViewPort"]["alignment"] == "TopLeft"
        assert len(doc["Collection"]["Widgets"]) == 2
    print("ok test_generate_mod_shrink")


def test_enable_solo_disables_others():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(1, 1, "a"), _entry(2, 5, "b"),
                        _entry(3, 12, "c")])
        (pm / "mod-status.json").write_text(
            json.dumps(orig, separators=(",", ":")), encoding="utf-8")
        nohud.enable(pm, game_widgetui=game, solo=True)
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert len(live["Mods"]) == 4, live
        ours = [m for m in live["Mods"] if m["Path"] == "local//hl_nohud"]
        assert len(ours) == 1 and ours[0]["Enabled"] is True
        others = [m for m in live["Mods"] if m["Path"] != "local//hl_nohud"]
        assert len(others) == 3, live
        assert all(m["Enabled"] is False for m in others), others
    print("ok test_enable_solo_disables_others")


def test_enable_not_solo_leaves_others():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(1, 1, "a"), _entry(2, 5, "b")])
        (pm / "mod-status.json").write_text(
            json.dumps(orig, separators=(",", ":")), encoding="utf-8")
        nohud.enable(pm, game_widgetui=game)   # solo defaults False
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert len(live["Mods"]) == 3, live
        others = [m for m in live["Mods"] if m["Path"] != "local//hl_nohud"]
        assert all(m["Enabled"] is True for m in others), others
    print("ok test_enable_not_solo_leaves_others")


def test_solo_backup_survives_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        orig = _status([_entry(1, 1, "a"), _entry(2, 5, "b")])
        raw = json.dumps(orig, separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")
        nohud.enable(pm, game_widgetui=game, solo=True)
        assert (pm / "mod-status.json.bak").read_bytes() == raw.encode()
        nohud.restore(pm)
        assert (pm / "mod-status.json").read_bytes() == raw.encode()
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert all(m["Enabled"] is True for m in live["Mods"]), live
    print("ok test_solo_backup_survives_roundtrip")


def _crlf_fixture():
    """Synthetic panel text with CRLF endings + 2-space indent (the game's
    format), 2 widgets, the first with a nested ViewPort AND nested
    ChildWidgets, plus a '[' ']' pair INSIDE a string value."""
    doc = {
        "Collection": {
            "Name": "ResourcePanel",
            "ViewPort": {"xorigin": 3, "yorigin": 12, "width": 1935,
                         "height": 228, "alignment": "TopLeft"},
            "Widgets": [
                {"Widget": {"Type": "Base", "Name": "Background",
                            "ViewPort": {"xorigin": 5, "yorigin": 5,
                                         "width": 100, "height": 20,
                                         "alignment": "BottomLeft"},
                            "ChildWidgets": [
                                {"Widget": {"Type": "Label", "Name": "Wood",
                                            "Text": "brackets [ in ] str"}}]}},
                {"Widget": {"Type": "Button", "Name": "Idle"}},
            ],
            "TrailingKey": "kept"}}
    return json.dumps(doc, indent=2).replace("\n", "\r\n")


def test_surgical_shrink_bytes():
    raw = _crlf_fixture()
    assert raw.count("\r\n") > 0, "fixture must use CRLF"
    out = nohud._surgical_shrink(raw, name="resourcepanel.json")
    doc = json.loads(out)
    coll_vp = doc["Collection"]["ViewPort"]
    assert coll_vp == {"xorigin": 0, "yorigin": 0, "width": 1, "height": 1,
                       "alignment": "TopLeft"}, coll_vp
    # nested widget ViewPort NEVER touched (first ViewPort after Collection)
    nested = doc["Collection"]["Widgets"][0]["Widget"]["ViewPort"]
    assert nested == {"xorigin": 5, "yorigin": 5, "width": 100,
                      "height": 20, "alignment": "BottomLeft"}, nested
    # bytes before the ViewPort block and after it are identical
    vp_raw = raw.index('"ViewPort"')
    end_raw = raw.index("}", raw.index("{", vp_raw))
    vp_out = out.index('"ViewPort"')
    end_out = out.index("}", out.index("{", vp_out))
    assert out[:vp_out] == raw[:vp_raw], "bytes before ViewPort changed"
    assert out[end_out + 1:] == raw[end_raw + 1:], "bytes after ViewPort changed"
    # CRLF count preserved
    assert out.count("\r\n") == raw.count("\r\n")
    print("ok test_surgical_shrink_bytes")


def test_surgical_empty_bytes():
    raw = _crlf_fixture()
    assert raw.count("\r\n") > 0, "fixture must use CRLF"
    out = nohud._surgical_empty(raw, name="resourcepanel.json")
    doc = json.loads(out)
    assert doc["Collection"]["Widgets"] == [], doc["Collection"]
    assert doc["Collection"]["Name"] == "ResourcePanel"
    assert doc["Collection"]["TrailingKey"] == "kept"
    assert doc["Collection"]["ViewPort"]["width"] == 1935
    # all bytes before '"Widgets": [' preserved
    w_raw = raw.index('"Widgets"')
    w_out = out.index('"Widgets"')
    assert out[:w_out] == raw[:w_raw], "bytes before Widgets changed"
    # everything from TrailingKey on preserved (sits after the matched ])
    assert out[out.index('"TrailingKey"'):] == \
        raw[raw.index('"TrailingKey"'):], "bytes after the ] changed"
    print("ok test_surgical_empty_bytes")


def test_surgical_raises():
    # shrink: no ViewPort anchor
    try:
        nohud._surgical_shrink('{"Collection": {"Name": "X"}}', name="p")
    except ValueError as e:
        assert "ViewPort" in str(e) and "p" in str(e), e
    else:
        raise AssertionError("no ValueError (shrink, no ViewPort)")
    # empty: no Widgets anchor
    try:
        nohud._surgical_empty('{"Collection": {"Name": "X"}}', name="p")
    except ValueError as e:
        assert "Widgets" in str(e) and "p" in str(e), e
    else:
        raise AssertionError("no ValueError (empty, no Widgets)")
    # empty: unterminated array
    try:
        nohud._surgical_empty('{"Collection": {"Widgets": [}}', name="p")
    except ValueError as e:
        assert "unterminated" in str(e), e
    else:
        raise AssertionError("no ValueError (empty, unterminated)")
    print("ok test_surgical_raises")


def test_generate_mode_surgical_bytes():
    for mode in ("empty", "shrink", "raw"):
        with tempfile.TemporaryDirectory() as td:
            game = Path(td) / "widgetui"
            game.mkdir()
            src = game / "resourcepanel.json"
            src.write_bytes(_crlf_fixture().encode("utf-8"))
            dest = Path(td) / "mod"
            nohud.generate_mod(game, dest, panels=["resourcepanel.json"],
                               mode=mode)
            data = dest / "widgetui" / "resourcepanel.json"
            if mode == "raw":
                assert data.read_bytes() == src.read_bytes(), mode
                continue
            raw_bytes = data.read_bytes()
            assert b"\r\n" in raw_bytes, f"{mode}: CRLF lost"
            doc = json.loads(raw_bytes.decode("utf-8"))
            if mode == "empty":
                assert doc["Collection"]["Widgets"] == [], mode
            else:
                assert doc["Collection"]["ViewPort"]["width"] == 1, mode
                assert doc["Collection"]["ViewPort"]["alignment"] == "TopLeft"
                assert len(doc["Collection"]["Widgets"]) == 2, mode
    print("ok test_generate_mode_surgical_bytes")


def _replay_fixture_doc():
    """The dict behind _replay_fixture (kept separate so tests can build
    the expected byte output with the same serializer)."""
    return {
        "Collection": {
            "Name": "CommandPanel",
            "ViewPort": {"xorigin": 0, "yorigin": 0, "width": 3840,
                         "height": 2160, "alignment": "TopLeft"},
            "Widgets": [
                {"Widget": {"Type": "Base", "Name": "BackgroundRight",
                            "ChildWidgets": [
                                {"Widget": {"Type": "Panel",
                                            "Name": "CmdGrid",
                                            "ChildWidgets": []}}]}},
                {"Widget": {"Type": "Base", "Name": "BackgroundLeft",
                            "ChildWidgets": [
                                {"Widget": {"Type": "Panel",
                                            "Name": "Buttons",
                                            "ChildWidgets": []}},
                                {"Widget": {"Type": "Panel",
                                            "Name": "ReplayButtons",
                                            "ChildWidgets": [
                                                {"Widget": {
                                                    "Type": "Checkbox",
                                                    "Name": "AllVisible",
                                                    "ChildWidgets": []}}]}},
                                {"Widget": {"Type": "Label",
                                            "Name": "FlightNameLabel",
                                            "ChildWidgets": []}}]}},
            ],
            "TrailingKey": "kept"}}


def _replay_fixture():
    """commandpanel-like CRLF/2-space panel: TWO top-level wrappers —
    BackgroundRight (HUD) and BackgroundLeft (Buttons grid + ReplayButtons
    bar with AllVisible + FlightNameLabel)."""
    return json.dumps(_replay_fixture_doc(),
                      indent=2).replace("\n", "\r\n")


def test_surgical_keep_replay():
    raw = _replay_fixture()
    out = nohud._surgical_keep_replay(raw, name="commandpanel.json")
    doc = json.loads(out)
    widgets = doc["Collection"]["Widgets"]
    assert len(widgets) == 1, len(widgets)
    assert widgets[0]["Widget"]["Name"] == "BackgroundLeft"
    txt = out[out.index('"Widgets"'):]
    assert '"ReplayButtons"' in txt and '"AllVisible"' in txt, txt
    for gone in ("BackgroundRight", "CmdGrid", "FlightNameLabel",
                 '"Buttons"'):
        assert gone not in txt, gone
    # ViewPort NOT shrunk and byte-identical: prefix up to "Widgets" equal
    w_out = out.index('"Widgets"')
    w_raw = raw.index('"Widgets"')
    assert out[:w_out] == raw[:w_raw], "prefix/ViewPort bytes changed"
    # closing bytes after the array's ']' identical
    assert out[out.rindex("]") + 1:] == raw[raw.rindex("]") + 1:], \
        "closing bytes changed"
    # kept element's CRLFs preserved: rebuilt == original serializer output
    # with element 1 (BackgroundRight) dropped from the source dict AND
    # BackgroundLeft's children filtered to ReplayButtons only
    doc2 = _replay_fixture_doc()
    doc2["Collection"]["Widgets"] = doc2["Collection"]["Widgets"][1:]
    bl = doc2["Collection"]["Widgets"][0]["Widget"]
    bl["ChildWidgets"] = [c for c in bl["ChildWidgets"]
                          if c["Widget"]["Name"] == "ReplayButtons"]
    expected = json.dumps(doc2, indent=2).replace("\n", "\r\n")
    assert out == expected, "kept element not byte-preserved"
    print("ok test_surgical_keep_replay")


def test_generate_mod_shrink_commandpanel():
    with tempfile.TemporaryDirectory() as td:
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "commandpanel.json").write_bytes(
            _replay_fixture().encode("utf-8"))
        (game / "scorepanel.json").write_bytes(
            _crlf_fixture().encode("utf-8"))
        dest = Path(td) / "mod"
        nohud.generate_mod(game, dest,
                           panels=["commandpanel.json", "scorepanel.json"],
                           mode="shrink")
        cmd = json.loads((dest / "widgetui" / "commandpanel.json")
                         .read_text("utf-8"))
        ws = cmd["Collection"]["Widgets"]
        assert len(ws) == 2
        # commandpanel uses the same known-good structure-preserving shrink.
        assert cmd["Collection"]["ViewPort"]["width"] == 1
        # the other panel got the NORMAL 1x1 shrink
        score = json.loads((dest / "widgetui" / "scorepanel.json")
                           .read_text("utf-8"))
        assert score["Collection"]["ViewPort"]["width"] == 1
        assert len(score["Collection"]["Widgets"]) == 2
    print("ok test_generate_mod_shrink_commandpanel")


def test_surgical_keep_replay_requires_anchor():
    try:
        nohud._surgical_keep_replay(_crlf_fixture(),
                                    name="commandpanel.json")
    except ValueError as e:
        assert "ReplayButtons" in str(e) and "commandpanel.json" in str(e), e
    else:
        raise AssertionError("no ValueError without ReplayButtons element")
    print("ok test_surgical_keep_replay_requires_anchor")


def test_enable_subscribed_channel():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "resourcepanel.json").write_text(
            json.dumps(_sample_collection(None)), encoding="utf-8")
        orig = _status([_entry(1, 12, "big")])
        raw = json.dumps(orig, separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")
        nohud.enable(pm, game_widgetui=game, channel="subscribed")
        e = nohud.entry(pm)
        assert e["Path"] == "subscribed//999999_hl_nohud", e
        assert e["WorkshopID"] == 999999, e
        assert e["Enabled"] is True
        assert e["Priority"] == 13, e          # max(12) + 1
        # payload under the subscribed dir, not local/
        assert (pm / "subscribed" / "999999_hl_nohud" / "widgetui" /
                "resourcepanel.json").is_file()
        assert not (pm / "local" / "hl_nohud").exists()
        # .bak still byte-original
        assert (pm / "mod-status.json.bak").read_bytes() == raw.encode()
    print("ok test_enable_subscribed_channel")


def test_enable_subscribed_preserves_game_checksum():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        (game / "resourcepanel.json").write_text(
            json.dumps(_sample_collection(None)), encoding="utf-8")
        orig = _status([
            _entry(1, 12, "big"),
            {"CheckSum": "4001785817", "Enabled": False,
             "LastUpdate": "1715432109", "PublishID": 0,
             "Path": "subscribed//999999_hl_nohud", "Priority": 3,
             "Title": "hl_nohud", "WorkshopID": 999999},
        ])
        (pm / "mod-status.json").write_text(
            json.dumps(orig, separators=(",", ":")), encoding="utf-8")
        nohud.enable(pm, game_widgetui=game, channel="subscribed")
        e = nohud.entry(pm)
        assert e["Enabled"] is True, e
        assert e["CheckSum"] == "4001785817", e    # NOT zeroed
        assert e["LastUpdate"] == "1715432109", e  # NOT reset to "0"
        assert e["Priority"] == 13, e              # ours, not the game's 3
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        ours = [m for m in live["Mods"]
                if m["Path"] == "subscribed//999999_hl_nohud"]
        assert len(ours) == 1, live                # upsert, not duplicated
    print("ok test_enable_subscribed_preserves_game_checksum")


def test_restore_removes_both_channels():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        raw = json.dumps(_status([_entry(1, 12, "a")]),
                         separators=(",", ":"))
        (pm / "mod-status.json").write_text(raw, encoding="utf-8")
        nohud.enable(pm, game_widgetui=game, channel="subscribed")
        assert nohud.is_enabled(pm)
        (pm / "mod-status.json.bak").unlink()  # force the in-place removal
        nohud.restore(pm)
        assert nohud.entry(pm) is None
        live = json.loads((pm / "mod-status.json").read_text("utf-8"))
        assert [m["Path"] for m in live["Mods"]] == ["subscribed//1_a"], live
    print("ok test_restore_removes_both_channels")


def test_enable_bad_channel_raises():
    with tempfile.TemporaryDirectory() as td:
        pm = Path(td) / "mods"
        pm.mkdir()
        game = Path(td) / "widgetui"
        game.mkdir()
        try:
            nohud.enable(pm, game_widgetui=game, channel="workshop")
        except ValueError as e:
            assert "channel" in str(e) and "subscribed" in str(e), e
        else:
            raise AssertionError("no ValueError for bad channel")
    print("ok test_enable_bad_channel_raises")


if __name__ == "__main__":
    test_empty_collection()
    test_empty_collection_raises()
    test_generate_mod()
    test_surgical_shrink_bytes()
    test_surgical_empty_bytes()
    test_surgical_raises()
    test_generate_mode_surgical_bytes()
    test_transform_collection_shrink()
    test_transform_collection_shrink_raises()
    test_transform_collection_empty_delegates()
    test_transform_collection_unknown_mode()
    test_generate_mod_shrink()
    test_surgical_keep_replay()
    test_generate_mod_shrink_commandpanel()
    test_surgical_keep_replay_requires_anchor()
    test_enable_priority_and_backup()
    test_prepare_self_registers_without_entry()
    test_enable_solo_disables_others()
    test_enable_not_solo_leaves_others()
    test_solo_backup_survives_roundtrip()
    test_enable_subscribed_channel()
    test_enable_subscribed_preserves_game_checksum()
    test_restore_removes_both_channels()
    test_enable_bad_channel_raises()
    test_enable_twice()
    test_restore_from_backup()
    test_restore_without_backup()
    test_entry_and_is_enabled_states()
    print("all nohud tests passed")
