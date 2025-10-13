"""Facade for convenient imports and CLI entry (flake8 clean)."""

from gsgsim import Ability
from gsgsim import Card
from gsgsim import GameState
from gsgsim import Player
from gsgsim import Rank
from gsgsim import Status
from gsgsim import apply_wind_with_resist
from gsgsim import build_cards
from gsgsim import deploy_from_hand
from gsgsim import destroy_if_needed
from gsgsim import distribute_wind
from gsgsim import end_of_turn
from gsgsim import find_squad_leader
from gsgsim import init_game
from gsgsim import load_deck_json
from gsgsim import parse_rank
from gsgsim import pay_to_unwind
from gsgsim import select_ui
from gsgsim import start_of_turn
from gsgsim import use_ability_cli
from gsgsim.main import main

__all__ = [
    "Ability",
    "Card",
    "GameState",
    "Player",
    "Rank",
    "Status",
    "load_deck_json",
    "build_cards",
    "find_squad_leader",
    "parse_rank",
    "distribute_wind",
    "apply_wind_with_resist",
    "destroy_if_needed",
    "init_game",
    "deploy_from_hand",
    "pay_to_unwind",
    "use_ability_cli",
    "start_of_turn",
    "end_of_turn",
    "select_ui",
    "main",
]


def _launch_with_flags():
    import argparse
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--ui", choices=["rich", "cli"], default="rich", help="UI to use")
    parser.add_argument("--ai", choices=["p1", "p2", "both", "none"], help="Set which side(s) are AI controlled")  # noqa: E501
    parser.add_argument(
        "--ai-p1",
        "--p1-ai",
        dest="ai_p1_flag",
        action="store_true",
        help="Make P1 AI controlled (alias for --ai p1)",
    )
    parser.add_argument(
        "--ai-p2",
        "--p2-ai",
        dest="ai_p2_flag",
        action="store_true",
        help="Make P2 AI controlled (alias for --ai p2)",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run without prompts (AI plays for any AI-controlled side each turn)",
    )
    parser.add_argument(
        "--p1-faction",
        help="Override the faction label for Player 1 (e.g. PCU or NARC)",
    )
    parser.add_argument(
        "--p2-faction",
        help="Override the faction label for Player 2 (e.g. PCU or NARC)",
    )
    args, unknown = parser.parse_known_args()

    # Encode AI choice into env vars for the UI to read.
    ai_choice = (args.ai or "").strip().lower()
    ai_p1 = ai_choice in ("p1", "both")
    ai_p2 = ai_choice in ("p2", "both")

    if getattr(args, "ai_p1_flag", False):
        ai_p1 = True
    if getattr(args, "ai_p2_flag", False):
        ai_p2 = True

    if ai_choice == "none":
        ai_p1 = False
        ai_p2 = False

    ai_env = None
    if ai_p1 and ai_p2:
        ai_env = "both"
    elif ai_p1:
        ai_env = "p1"
    elif ai_p2:
        ai_env = "p2"

    if ai_env:
        os.environ["GSG_AI"] = ai_env

    if ai_choice or getattr(args, "ai_p1_flag", False) or getattr(args, "ai_p2_flag", False):
        os.environ["GSG_AI_P1"] = "1" if ai_p1 else "0"
        os.environ["GSG_AI_P2"] = "1" if ai_p2 else "0"

    if args.auto:
        os.environ["GSG_AUTO"] = "1"

    if args.p1_faction:
        os.environ["GSG_P1_FACTION"] = args.p1_faction.strip()
    if args.p2_faction:
        os.environ["GSG_P2_FACTION"] = args.p2_faction.strip()

    # Preserve --ui for downstream (if main/main.py inspects it)
    os.environ["GSG_UI"] = args.ui

    # Now call the existing main()
    from gsgsim import main as _main

    _main()


if __name__ == "__main__":
    _launch_with_flags()
