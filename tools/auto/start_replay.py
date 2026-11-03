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
    if row != 1:
        sys.exit("only --row 1 is supported: reorder the replays list by "
                 "copying/touching replay files (row heights vary and "
                 "hover-scroll breaks detection)")
    sb = game.locate_anchor("replays_searchbar")
    if not sb:
        sb = (486, 178)             # measured 1080p fallback
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
    sb = game.locate_anchor("replays_searchbar")
    if not sb:
        sb = (486, 178)
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


def close_open_menus():
    """Close the in-match Main Menu (Escape does not close it): Cancel."""
    if game.locate_anchor("match_menu_cancel", threshold=0.80) is not None:
        hit = game.locate_anchor("match_menu_cancel", threshold=0.80)
        game.press_click(hit[0], hit[1])
        time.sleep(1.0)
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--search", default=None, help="typed into the replay search box")
    ap.add_argument("--row", type=int, default=1, help="list row to pick (1-based)")
    args = ap.parse_args()

    game.focus_window()
    time.sleep(0.5)

    # State machine to reach the replay list from anywhere.
    for state in range(12):
        if game.hit_anchor("load_replay_header"):
            print("at replay list")
            break
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

    if args.search:
        print(f"search '{args.search}'")
        prime_search(args.search)
    else:
        clear_search()

    print("picking row", args.row)
    pick_row(args.row)
    load()


if __name__ == "__main__":
    main()
