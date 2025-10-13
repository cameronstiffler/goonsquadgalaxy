# === IMPORT SENTRY (do not move/duplicate) ===
from __future__ import annotations

from .engine import deploy_from_hand
from .engine import end_of_turn
from .engine import init_game
from .engine import pay_to_unwind
from .engine import select_ui
from .engine import start_of_turn
from .engine import use_ability_cli
from .loader import build_cards
from .loader import find_squad_leader
from .loader import load_deck_json
from .loader import parse_rank

# Convenient entrypoint
from .main import main
from .models import Ability
from .models import Card
from .models import GameState
from .models import Player
from .models import Rank
from .models import Status
from .payments import distribute_wind
from .rules import apply_wind_with_resist
from .rules import destroy_if_needed

__all__ = [
    "Rank",
    "Ability",
    "Status",
    "Card",
    "Player",
    "GameState",
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
