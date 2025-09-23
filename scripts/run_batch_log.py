import os
import sys
from collections import Counter
from pathlib import Path
from typing import Dict
from typing import List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gsgsim.ai import ai_take_turn
from gsgsim.engine import end_of_turn
from gsgsim.engine import init_game

LOG_PATH = Path("logs/simulation_actions.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

START_SEED = 11030
NUM_GAMES = 100
MAX_TURNS = 200


def card_names(cards: List) -> List[str]:
    return [getattr(c, "name", "?") for c in cards]


def snapshot(gs) -> Dict[str, Dict[str, List[str]]]:
    return {
        "p1": {
            "board": card_names(getattr(gs.p1, "board", [])),
            "hand": card_names(getattr(gs.p1, "hand", [])),
            "retired": card_names(getattr(gs.p1, "retired", [])),
        },
        "p2": {
            "board": card_names(getattr(gs.p2, "board", [])),
            "hand": card_names(getattr(gs.p2, "hand", [])),
            "retired": card_names(getattr(gs.p2, "retired", [])),
        },
        "dead": card_names(getattr(gs, "dead_pool", []) or []),
    }


def list_diff(before: List[str], after: List[str]):
    be = Counter(before)
    af = Counter(after)
    added = list((af - be).elements())
    removed = list((be - af).elements())
    return added, removed


def _consume(name: str, source: List[str], target: List[str]) -> List[str]:
    lines = []
    for card in list(source):
        if card in target:
            lines.append(card)
            source.remove(card)
            target.remove(card)
    return lines


def describe_events(side: str, hand_added, hand_removed, board_added, board_removed, retired_added, retired_removed) -> List[str]:
    name = side.upper()
    events: List[str] = []

    deployed = _consume(name, hand_removed, board_added)
    for card in deployed:
        events.append(f"{name} deployed {card}.")

    revived = _consume(name, retired_removed, board_added)
    for card in revived:
        events.append(f"{name} revived {card} to the board.")

    fell = _consume(name, board_removed, retired_added)
    for card in fell:
        events.append(f"{name}'s {card} was retired.")

    rewound = _consume(name, board_removed, hand_added)
    for card in rewound:
        events.append(f"{name} returned {card} to hand.")

    for card in hand_added:
        events.append(f"{name} drew {card}.")
    for card in hand_removed:
        events.append(f"{name} played {card} from hand.")
    for card in board_added:
        events.append(f"{name} has {card} ready on the board.")
    for card in board_removed:
        events.append(f"{name} lost {card} from the board.")
    for card in retired_added:
        events.append(f"{name}'s {card} entered retirement.")
    for card in retired_removed:
        events.append(f"{name} pulled {card} out of retirement.")

    return events


def log_game(seed: int) -> List[str]:
    os.environ["GSG_RNG_SEED"] = str(seed)
    gs = init_game()
    logs: List[str] = []
    logs.append(f"=== Game seed {seed} ===")
    logs.append(f"Starting: P1 faction {getattr(gs.p1, 'faction', '?')} vs P2 faction {getattr(gs.p2, 'faction', '?')}")

    turn_counter = 0
    while turn_counter < MAX_TURNS and getattr(gs, "loser", None) is None:
        turn_player = gs.turn_player
        before = snapshot(gs)
        ai_take_turn(gs)
        after_action = snapshot(gs)

        logs.append(f"Turn {turn_counter + 1} - {getattr(turn_player, 'name', 'P?')} acts")

        # diff for both players and dead pool
        pre_event_len = len(logs)

        for side in ("p1", "p2"):
            hand_added, hand_removed = list_diff(before[side]["hand"], after_action[side]["hand"])
            board_added, board_removed = list_diff(before[side]["board"], after_action[side]["board"])
            retired_added, retired_removed = list_diff(before[side]["retired"], after_action[side]["retired"])
            events = describe_events(side, hand_added, hand_removed, board_added, board_removed, retired_added, retired_removed)
            for event in events:
                logs.append(f"  {event}")

        added_dead, removed_dead = list_diff(before["dead"], after_action["dead"])
        for card in added_dead:
            logs.append(f"  Dead Pool collected {card}.")
        for card in removed_dead:
            logs.append(f"  Dead Pool released {card}.")

        if len(logs) == pre_event_len:
            logs.append("  No major card changes this turn.")

        if getattr(gs, "loser", None) is not None:
            break

        ended_player = turn_player
        end_of_turn(gs)
        turn_counter += 1

        p1_board = len(getattr(gs.p1, "board", []))
        p2_board = len(getattr(gs.p2, "board", []))
        dead_count = len(getattr(gs, "dead_pool", []) or [])
        logs.append(f"End of Turn {turn_counter}: P1 board {p1_board}, P2 board {p2_board}, Dead Pool {dead_count}")

    loser = getattr(gs, "loser", None)
    if loser == "P1":
        winner = "P2"
    elif loser == "P2":
        winner = "P1"
    else:
        winner = "Draw"
    logs.append(f"Result: winner {winner} | turns {getattr(gs, 'turn_number', turn_counter) - 1}")
    logs.append(f"Retired: P1 {len(getattr(gs.p1, 'retired', []))}, P2 {len(getattr(gs.p2, 'retired', []))}, Dead Pool {len(getattr(gs, 'dead_pool', []) or [])}")
    logs.append("")
    return logs


all_logs: List[str] = []
for seed in range(START_SEED, START_SEED + NUM_GAMES):
    all_logs.extend(log_game(seed))

LOG_PATH.write_text("\n".join(all_logs))

p1_wins = sum(1 for line in all_logs if line.startswith("Result:") and "winner P1" in line)
p2_wins = sum(1 for line in all_logs if line.startswith("Result:") and "winner P2" in line)
draws = sum(1 for line in all_logs if line.startswith("Result:") and "winner Draw" in line)

print(f"Log written to {LOG_PATH}")
print(f"Games: {NUM_GAMES} | P1 wins: {p1_wins} | P2 wins: {p2_wins} | Draws: {draws}")
