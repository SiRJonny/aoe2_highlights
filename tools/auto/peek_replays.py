"""Navigate to the replay list and screenshot it (no replay started).

py -3.12 tools\\auto\\peek_replays.py
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import game  # noqa: E402
from start_replay import (navigate_to_replays, switch_to_replay_tab,
                          leave_replay_end_screen, close_open_menus) \
    # noqa: E402


def main():
    game.focus_window()
    time.sleep(0.5)

    for _state in range(12):
        if game.hit_anchor("load_replay_header"):
            print("at replay list")
            break
        elif game.locate_anchor("return_to_replays_button") is not None:
            hit = game.locate_anchor("return_to_replays_button")
            print("statistics -> return to replays")
            game.focus_window()
            time.sleep(0.3)
            game.press_click(hit[0], hit[1])
            time.sleep(2)
        elif game.locate_anchor("leave_map_button") is not None:
            print("finished replaying -> leave map")
            leave_replay_end_screen()
        elif game.locate_anchor("quit_confirm_dialog", threshold=0.80) \
                is not None:
            hit = game.locate_anchor("quit_confirm_yes", threshold=0.80)
            print("quit confirm -> yes")
            game.focus_window()
            time.sleep(0.3)
            game.press_click(hit[0], hit[1])
            time.sleep(3)
        elif game.locate_anchor("quit_current_game", threshold=0.80) \
                is not None:
            hit = game.locate_anchor("quit_current_game", threshold=0.80)
            print("match menu open -> quit current game")
            game.focus_window()
            time.sleep(0.3)
            game.press_click(hit[0], hit[1])
            time.sleep(2)
        elif game.hit_anchor("load_sp_header"):
            print("saved-games screen -> replays tab")
            switch_to_replay_tab()
        elif game.locate_anchor("single_player_button") is not None:
            navigate_to_replays()
        elif game.locate_anchor("match_menu_cancel", threshold=0.80) \
                is not None:
            hit = game.locate_anchor("match_menu_cancel", threshold=0.80)
            print("match menu open -> cancel")
            game.focus_window()
            time.sleep(0.3)
            game.press_click(hit[0], hit[1])
            time.sleep(1)
        else:
            print("unknown state -> escape")
            game.focus_window()
            game.pyautogui.press("escape")
            time.sleep(1.5)

    print(game.save_debug("peek_replay_list"))


if __name__ == "__main__":
    main()
