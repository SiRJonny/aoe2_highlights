"""Automated replay start by driving the game UI.

Flow (matches the user's notes):
  main menu -> SINGLE PLAYER -> LOAD GAME -> wait for the savegame list to
  finish (-AUTOSAVE- anchor only appears when loading completes) -> REPLAYS
  tab -> (optional search) -> top row -> LOAD REPLAY

The script also resumes cleanly mid-flow: if the load screen is already open
or the Replays tab is already active, earlier steps are skipped.

Usage:
    py -3.12 tools\\auto\\start_replay.py [--search <text>] [--row N]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import game  # noqa: E402


def step(label, fn, *a, **kw):
    ok = fn(*a, **kw)
    if not ok:
        shot = game.save_debug(f"fail_{label}")
        print(f"FAILED at {label}; screenshot: {shot}")
        sys.exit(3)
    print(f"ok: {label}")
    return ok


def click_single_player():
    if game.locate_anchor("single_player_clicked") is not None:
        return True  # dialog already open
    return game.click_anchor("single_player_button", pause=1.2)


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


def prime_search(term):
    sb = game.locate_anchor("replays_searchbar")
    if not sb:
        game.save_debug("fail_searchbar_not_found")
        sys.exit(4)
    game.pyautogui.click(sb[0], sb[1])
    time.sleep(0.7)
    game.pyautogui.hotkey("ctrl", "a")
    game.pyautogui.press("backspace")
    game.pyautogui.write(term, interval=0.06)
    time.sleep(1.5)


def pick_row(row):
    sb = game.locate_anchor("replays_searchbar")
    if not sb:
        game.save_debug("fail_searchbar_gone")
        sys.exit(5)
    game.focus_window()
    time.sleep(0.3)
    game.pyautogui.click(sb[0], sb[1] + 70 + 28 * (row - 1))
    time.sleep(1.0)


def load():
    hit = game.locate_anchor("load_replay_button")
    if not hit:
        game.save_debug("fail_load_replay_button_gone")
        sys.exit(6)
    game.focus_window()
    time.sleep(0.4)
    cx, cy = hit[0], hit[1]
    # attempt styles: raw press-hold, then pyautogui double-click
    game.press_click(cx, cy)
    time.sleep(4)
    if not game.hit_anchor("load_replay_header"):
        return  # loading screen visible / left the screen
    game.pyautogui.doubleClick(cx, cy)
    time.sleep(4)
    if not game.hit_anchor("load_replay_header"):
        return
    # normal single click again twice as last resort
    game.pyautogui.click(cx, cy)
    time.sleep(4)
    if not game.hit_anchor("load_replay_header"):
        return
    game.save_debug("fail_load_replay_no_effect")
    sys.exit(7)


def leave_replay_end_screen():
    """Replay finished ('FINISHED REPLAYING GAME.') -> click LEAVE MAP."""
    hit = game.locate_anchor("leave_map_button")
    if not hit:
        return False
    game.focus_window()
    time.sleep(0.3)
    game.press_click(hit[0], hit[1])
    time.sleep(3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None, help="typed into the replay search box")
    ap.add_argument("--row", type=int, default=1, help="list row to pick (1-based)")
    args = ap.parse_args()

    game.focus_window()
    time.sleep(0.5)

    if game.locate_anchor("return_to_replays_button") is not None:
        print("postgame statistics -> return to replays")
        hit = game.locate_anchor("return_to_replays_button")
        game.focus_window()
        time.sleep(0.3)
        game.press_click(hit[0], hit[1])
        time.sleep(2)
    elif game.locate_anchor("leave_map_button") is not None:
        print("replay finished screen -> leaving to main menu")
        step("leave_map", leave_replay_end_screen)
    else:
        game.pyautogui.press("escape")
        time.sleep(1.0)

    if game.hit_anchor("load_replay_header"):
        print("replays tab already active")
        print("replays tab already active")
    elif game.hit_anchor("load_sp_header"):
        print("load-game screen already open")
        switch_to_replay_tab()
    else:
        navigate_to_replays()

    if args.search:
        print(f"search '{args.search}'")
        prime_search(args.search)

    print("picking row", args.row)
    pick_row(args.row)
    load()


if __name__ == "__main__":
    main()
