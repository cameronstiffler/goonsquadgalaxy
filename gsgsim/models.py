# === IMPORT SENTRY ===
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from enum import auto
from typing import Any  # fallback for typing; replace with real Effect when available
from typing import Any as Effect
from typing import Dict
from typing import List
from typing import Optional
from typing import Set


# Enums / simple types
class Rank(Enum):
    SL = auto()
    SG = auto()
    BG = auto()
    TITAN = auto()
    TN = TITAN

    def __str__(self) -> str:
        return self.name


@dataclass
class Ability:
    name: str
    cost: Dict[str, int] = field(default_factory=dict)
    effects: List["Effect"] = field(default_factory=list)
    passive: bool = False


@dataclass
class Status:
    name: str
    data: Dict[str, int] = field(default_factory=dict)


@dataclass
class Card:
    # non-defaults first (Python 3.13 rule)
    name: str
    rank: Rank
    faction: Optional[str] = None

    # defaults after
    abilities: List[Ability] = field(default_factory=list)
    deploy_requirements: List[Dict[str, Any]] = field(default_factory=list)
    traits: Set[str] = field(default_factory=set)
    statuses: Dict[str, Status] = field(default_factory=dict)

    wind: int = 0
    deploy_wind: int = 0
    deploy_gear: int = 0
    deploy_meat: int = 0
    new_this_turn: bool = False


@dataclass
class GameState:
    p1: "Player"
    p2: "Player"
    turn_player: "Player"
    phase: str
    turn_number: int
    rng: Any | None = None
    shared_dead: List[Card] = field(default_factory=list)


@dataclass
class Player:
    name: str
    board: List[Card] = field(default_factory=list)
    hand: List[Card] = field(default_factory=list)
    deck: List[Card] = field(default_factory=list)
    retired: List[Card] = field(default_factory=list)
    gear: int = 0
    meat: int = 0
    power: int = 0
