"""Automated replay start by driving the game UI.

Flow (matches the user's notes):
  main menu -> SINGLE PLAYER -> LOAD GAME -> wait for the savegame list to
  finish (-AUTOSAVE- anchor only appears when loading completes) -> REPLAYS
  tab -> (optional search) -> top row -> LOAD REPLAY

The script also resumes cleanly mid-flow: if the load screen is already open
or the Replays tab is already active, earlier steps are skipped.

Usage:
    py -3.12 -m aoe2hl.replays_ui [--search <text>] [--row N]
"""
import argparse
import sys
import time
from pathlib import Path

from aoe2hl import gameui as game


def step(label, fn, *a, **kw):
    ok = fn(*a, **kw)
    if not ok:
        shot = game.save_debug(f"fail_{label}")
        raise UIError(f"FAILED at {label}; screenshot: {shot}")
    print(f"ok: {label}")
    return ok


def click_single_player():
    for attempt in range(2):
        if game.locate_anchor("single_player_clicked") is not None:
            return True
        if game.locate_anchor("single_player_button") is not None:
            return game.click_anchor("single_player_button", pause=1.2)
        if attempt == 0:
            # no main menu anchors -> we may be inside a session (postgame
            # map view). Escape opens the in-match menu with Quit Current Game;
            # on the real main menu Escape is inert.
            game.focus_window()
            game.pyautogui.press("escape")
            time.sleep(1.2)
            hit = game.locate_anchor("quit_current_game", threshold=0.80)
            if hit:
                game.press_click(hit[0], hit[1])
                time.sleep(3)
                continue
    return False


def navigate_to_replays():
    step("single_player", click_single_player)
    step("load_game", game.click_anchor, "load_game_button", pause=1.5)
    step("list_loaded", game.wait_anchor, "first_line_in_savegame_list_autosave", timeout=60)
    time.sleep(0.5)
    step("replays_tab", game.click_anchor, "replays_button", pause=1.5)
    step("replays_screen_open", game.wait_anchor, "load_replay_header", timeout=15)
    step("searchbar", game.wait_anchor, "replays_searchbar", timeout=15)


def switch_to_replay_tab():
    step("replays_tab", game.click_anchor, "replays_button", pause=1.5)
    step("replays_screen_open", game.wait_anchor, "load_replay_header", timeout=15)
    step("searchbar", game.wait_anchor, "replays_searchbar", timeout=15)


def searchbar_pos():
    """Searchbar coords: template match when possible, window-relative
    fallback otherwise (a focused searchbar's glow breaks the anchor
    template; the fallback was measured on the 1080p fullscreen
    layout)."""
    sb = game.locate_anchor("replays_searchbar")
    if sb:
        return sb[0], sb[1]
    wr = game.window_rect()
    return wr["left"] + 486, wr["top"] + 178


def prime_search(term):
    sb = searchbar_pos()
    game.focus_window()
    time.sleep(0.3)
    game.pyautogui.click(sb[0], sb[1])
    time.sleep(0.7)
    game.pyautogui.hotkey("ctrl", "a")
    game.pyautogui.press("backspace")
    game.pyautogui.write(term, interval=0.06)
    time.sleep(1.5)


def pick_row(row):
    if row != 1:
        raise UIError("only row 1 is supported: reorder the replays list "
                      "by copying/touching replay files (row heights vary "
                      "and hover-scroll breaks detection)")
    sb = searchbar_pos()
    game.focus_window()
    time.sleep(0.3)
    # row 1 center: the list re-scans on entry and newest file is on top
    game.pyautogui.click(sb[0], sb[1] + 81)
    time.sleep(1.0)


def clear_search():
    """Empty the (sticky, haunted) replay search box, then blur.

    The box keeps its text across screens and pyautogui keystrokes
    sometimes land late; blur afterwards so the searchbar anchor
    template matches again (the focus glow breaks it)."""
    sb = searchbar_pos()
    game.focus_window()
    time.sleep(0.3)
    game.pyautogui.click(sb[0] + 40, sb[1])
    time.sleep(0.4)
    game.pyautogui.press("end")
    for _ in range(30):
        game.pyautogui.press("backspace")
    time.sleep(1.5)
    game.pyautogui.click(sb[0], sb[1] + 81)   # blur into the list
    time.sleep(0.5)


def load(row):
    # the button click sometimes starts loading late (loading screen
    # anchor arrives >4 s later); wait long enough between styles and
    # keep one retry cycle before giving up. Re-select the row each
    # attempt: a missed selection click leaves LOAD inert.
    for attempt in range(2):
        hit = game.locate_anchor("load_replay_button")
        if not hit:
            game.save_debug("fail_load_replay_button_gone")
            raise UIError("load_replay_button gone")
        pick_row(row)
        game.focus_window()
        time.sleep(0.4)
        cx, cy = hit[0], hit[1]
        for style in ("click", "double", "click"):
            getattr(game.pyautogui, {"click": "click", "double": "doubleClick"}[style])(cx, cy)
            for _ in range(12):            # up to 12 s for the loading screen
                time.sleep(1)
                if not game.hit_anchor("load_replay_header"):
                    return
            print(f"load style {style} had no effect (attempt {attempt})")
    game.save_debug("fail_load_no_effect")
    raise UIError("load click had no effect")


def leave_replay_end_screen():
    """Replay finished ('FINISHED REPLAYING GAME.') -> click LEAVE MAP."""
    hit = game.locate_anchor("leave_map_button")
    if not hit:
        return False
    game.focus_window()
    time.sleep(0.3)
    game.press_click(hit[0], hit[1])
    time.sleep(3)


def close_open_menus():
    """Close the in-match Main Menu (Escape does not close it): Cancel."""
    if game.locate_anchor("match_menu_cancel", threshold=0.80) is not None:
        hit = game.locate_anchor("match_menu_cancel", threshold=0.80)
        game.press_click(hit[0], hit[1])
        time.sleep(1.0)
        return True
    return False


class UIError(RuntimeError):
    """A UI step failed (screenshot saved under out/auto_debug)."""


def run(row, search):
    """Drive the UI to load replay `row` (optionally filtered by
    `search`). Returns True on success; False instead of exiting when
    used in-process."""
    try:
        return _run(row, search)
    except SystemExit as exc:          # step()/load() legacy exits
        print(f"replays_ui: exit {exc.code}")
        return False


def reach_replay_list(max_loops=12):
    """Run the state machine until the replay list is on screen: handles
    finished-replay end screens, statistics, open match menus and
    mid-replay quits along the way. True when at the list."""
    game.focus_window()
    time.sleep(0.5)

    # State machine to reach the replay list from anywhere.
    for state in range(max_loops):
        if game.hit_anchor("load_replay_header"):
            print("at replay list")
            return True
        elif game.locate_anchor("return_to_replays_button") is not None:
            hit = game.locate_anchor("return_to_replays_button")
            print("statistics -> return to replays")
            game.focus_window(); time.sleep(0.3)
            game.press_click(hit[0], hit[1]); time.sleep(2)
        elif game.locate_anchor("leave_map_button") is not None:
            print("finished replaying -> leave map")
            hit = game.locate_anchor("leave_map_button")
            game.focus_window(); time.sleep(0.3)
            game.press_click(hit[0], hit[1]); time.sleep(3)
        elif game.locate_anchor("quit_confirm_dialog", threshold=0.80) is not None:
            hit = game.locate_anchor("quit_confirm_yes", threshold=0.80)
            print("quit confirm -> yes")
            game.focus_window(); time.sleep(0.3)
            game.press_click(hit[0], hit[1]); time.sleep(3)
        elif game.locate_anchor("quit_current_game", threshold=0.80) is not None:
            hit = game.locate_anchor("quit_current_game", threshold=0.80)
            print("match menu open -> quit current game")
            game.focus_window(); time.sleep(0.3)
            game.press_click(hit[0], hit[1]); time.sleep(2)
        elif game.hit_anchor("load_sp_header"):
            print("saved-games screen -> replays tab")
            step("replays_tab", game.click_anchor, "replays_button", pause=1.5)
        elif game.locate_anchor("single_player_button") is not None:
            navigate_to_replays()
        elif game.locate_anchor("match_menu_cancel", threshold=0.80) is not None:
            hit = game.locate_anchor("match_menu_cancel", threshold=0.80)
            print("match menu open -> cancel")
            game.focus_window(); time.sleep(0.3)
            game.press_click(hit[0], hit[1]); time.sleep(1)
        else:
            # possibly a live session (postgame map view): Escape opens its
            # menu; on the real main menu Escape is inert
            print("unknown state -> escape")
            game.focus_window()
            game.pyautogui.press("escape")
            time.sleep(1.5)
    return game.hit_anchor("load_replay_header")


def wind_down(max_loops=8):
    """After a collect: stop the replay (leave end screen / quit a still-
    running replay) and park the game at the replay list."""
    try:
        ok = reach_replay_list(max_loops)
        print("wind down:", "at replay list" if ok else "unresolved state")
        return ok
    except Exception as exc:                    # noqa: BLE001
        print("wind down failed:", exc)
        return False


def _run(row, search):
    reach_replay_list(12)

    if search:
        print(f"search '{search}'")
        prime_search(search)
    else:
        clear_search()

    print("picking row", row)
    pick_row(row)
    load(row)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None, help="typed into the replay search box")
    ap.add_argument("--row", type=int, default=1, help="list row to pick (1-based)")
    args = ap.parse_args()
    ok = run(args.row, args.search)
    sys.exit(0 if ok else 3)


if __name__ == "__main__":
    main()
