from __future__ import annotations

import json
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

# ---- Types mirroring deck.schema.json ----
Rank = Literal["SL", "SG", "BG", "T"]
Faction = Literal["NARC", "PCU"]
CostNumber = Union[int, Literal["X"]]


@dataclass(frozen=True)
class DeployCost:
    wind: CostNumber
    gear: CostNumber
    meat: CostNumber


@dataclass(frozen=True)
class Effect:
    effect_type: str
    amount: Optional[Union[int, Literal["any", "X"]]] = None
    target: Optional[List[str]] = None
    duration: Optional[str] = None


@dataclass(frozen=True)
class Requirement:
    type: str
    card_name: Optional[str] = None
    side: Optional[Literal["self", "opponent"]] = None
    count: Optional[int] = None
    value: Optional[str] = None


@dataclass(frozen=True)
class Ability:
    name: str
    cost: DeployCost
    passive: bool
    must_use: bool
    text: str
    effects: List[Effect]


@dataclass(frozen=True)
class Goon:
    name: str
    rank: Rank
    duplicates: int
    faction: Faction
    deploy_cost: DeployCost
    biological: bool
    mechanical: bool
    resist: bool
    no_unwind: bool
    abilities: List[Ability]
    requirements: List[Requirement] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    flavor_text: Optional[str] = None
    image_url_full: Optional[str] = None
    image_url_mini: Optional[str] = None


@dataclass(frozen=True)
class Deck:
    id: str
    faction: Faction
    goons: List[Goon]


# ---- Parsing helpers (schema-constrained, but no external deps) ----
def _need(obj: Dict[str, Any], keys: List[str], where: str) -> None:
    miss = [k for k in keys if k not in obj]
    if miss:
        raise ValueError(f"Missing {miss} in {where}")


def _is_costnum(x: Any) -> bool:
    return (isinstance(x, int) and x >= 0) or x == "X"


def _cost(d: Dict[str, Any], where: str) -> DeployCost:
    _need(d, ["wind", "gear", "meat"], where)
    w, g, m = d["wind"], d["gear"], d["meat"]
    if not (_is_costnum(w) and _is_costnum(g) and _is_costnum(m)):
        raise ValueError(f"Invalid cost in {where}: {d}")
    return DeployCost(wind=w, gear=g, meat=m)


def _effect(d: Dict[str, Any], where: str) -> Effect:
    _need(d, ["effect_type"], where)
    amt = d.get("amount")
    if amt is not None and not (isinstance(amt, int) or amt in ("any", "X")):
        raise ValueError(f"Invalid amount in {where}: {amt}")
    tgt = d.get("target")
    if tgt is not None and not (isinstance(tgt, list) and all(isinstance(t, str) for t in tgt)):
        raise ValueError(f"Invalid target in {where}: {tgt}")
    dur = d.get("duration")
    if dur is not None and not isinstance(dur, str):
        raise ValueError(f"Invalid duration in {where}: {dur}")
    return Effect(effect_type=d["effect_type"], amount=amt, target=tgt, duration=dur)


def _req(d: Dict[str, Any], where: str) -> Requirement:
    _need(d, ["type"], where)
    side = d.get("side")
    if side is not None and side not in ("self", "opponent"):
        raise ValueError(f"Invalid side in {where}: {side}")
    cnt = d.get("count")
    if cnt is not None and not (isinstance(cnt, int) and cnt >= 0):
        raise ValueError(f"Invalid count in {where}: {cnt}")
    return Requirement(
        type=d["type"],
        card_name=d.get("card_name"),
        side=side,
        count=cnt,
        value=d.get("value"),
    )


def _ability(d: Dict[str, Any], where: str) -> Ability:
    _need(d, ["name", "cost", "passive", "must_use", "text", "effects"], where)
    if not isinstance(d["name"], str):
        raise ValueError(f"Ability.name must be string in {where}")
    if not isinstance(d["passive"], bool):
        raise ValueError(f"Ability.passive must be bool in {where}")
    if not isinstance(d["must_use"], bool):
        raise ValueError(f"Ability.must_use must be bool in {where}")
    if not isinstance(d["text"], str):
        raise ValueError(f"Ability.text must be string in {where}")
    cost = _cost(d["cost"], f"{where}.cost")
    effs = d["effects"]
    if not isinstance(effs, list):
        raise ValueError(f"Ability.effects must be array in {where}")
    return Ability(
        name=d["name"],
        cost=cost,
        passive=d["passive"],
        must_use=d["must_use"],
        text=d["text"],
        effects=[_effect(e, f"{where}.effects[{i}]") for i, e in enumerate(effs)],
    )


def _goon(d: Dict[str, Any], where: str) -> Goon:
    _need(
        d,
        ["name", "rank", "duplicates", "faction", "deploy_cost", "biological", "mechanical", "resist", "no_unwind", "abilities"],
        where,
    )
    rank = d["rank"]
    if rank not in ("SL", "SG", "BG", "T"):
        raise ValueError(f"Invalid rank in {where}: {rank}")
    faction = d["faction"]
    if faction not in ("NARC", "PCU"):
        raise ValueError(f"Invalid faction in {where}: {faction}")
    dup = d["duplicates"]
    if not (isinstance(dup, int) and dup >= 1):
        raise ValueError(f"'duplicates' must be integer >= 1 in {where}")
    abilities = d["abilities"]
    if not isinstance(abilities, list):
        raise ValueError(f"Goon.abilities must be array in {where}")
    reqs_raw = d.get("requirements", [])
    if not isinstance(reqs_raw, list):
        raise ValueError(f"Goon.requirements must be array in {where}")
    notes = d.get("notes", [])
    if not isinstance(notes, list) or not all(isinstance(x, str) for x in notes):
        raise ValueError(f"Goon.notes must be array of strings in {where}")
    flavor = d.get("flavor_text")
    if flavor is not None and not isinstance(flavor, str):
        raise ValueError(f"Goon.flavor_text must be string in {where}")
    img_full = d.get("image_url_full")
    img_mini = d.get("image_url_mini")
    for k, v in (("image_url_full", img_full), ("image_url_mini", img_mini)):
        if v is not None and not isinstance(v, str):
            raise ValueError(f"{k} must be string in {where}")

    return Goon(
        name=d["name"],
        rank=rank,
        duplicates=dup,
        faction=faction,
        deploy_cost=_cost(d["deploy_cost"], f"{where}.deploy_cost"),
        biological=bool(d["biological"]),
        mechanical=bool(d["mechanical"]),
        resist=bool(d["resist"]),
        no_unwind=bool(d["no_unwind"]),
        abilities=[_ability(a, f"{where}.abilities[{i}]") for i, a in enumerate(abilities)],
        requirements=[_req(r, f"{where}.requirements[{i}]") for i, r in enumerate(reqs_raw)],
        notes=notes,
        flavor_text=flavor,
        image_url_full=img_full,
        image_url_mini=img_mini,
    )


# ---- Public API ----
def load_deck(path_or_dict: Union[str, os.PathLike, Dict[str, Any]]) -> Deck:
    """
    Load a JSON deck that follows deck.schema.json and return a Deck.
    """
    data = path_or_dict
    if isinstance(path_or_dict, (str, os.PathLike)):
        with open(path_or_dict, "r", encoding="utf-8") as f:
            data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Deck JSON must be an object")

    _need(data, ["id", "faction", "goons"], "deck")
    deck_id = data["id"]
    if not (isinstance(deck_id, str) and deck_id.strip()):
        raise ValueError("deck.id must be a non-empty string")

    faction = data["faction"]
    if faction not in ("NARC", "PCU"):
        raise ValueError(f"deck.faction must be NARC or PCU (got {faction})")

    goons_raw = data["goons"]
    if not (isinstance(goons_raw, list) and len(goons_raw) >= 1):
        raise ValueError("deck.goons must be a non-empty array")

    goons = [_goon(g, f"deck.goons[{i}]") for i, g in enumerate(goons_raw)]
    return Deck(id=deck_id, faction=faction, goons=goons)


def deck_size(deck: Deck) -> int:
    return sum(g.duplicates for g in deck.goons)


def expand_duplicates(deck: Deck) -> List[Goon]:
    out: List[Goon] = []
    for g in deck.goons:
        out.extend([g] * g.duplicates)
    return out


def assert_legal_deck(
    deck: Deck,
    *,
    min_size: Optional[int] = 52,
    max_copies: int = 4,
    titan_rank: Rank = "T",
    titan_max: int = 1,
) -> None:
    """
    Enforce deckbuilding rules:
      - (optional) minimum size check
      - Max 4 copies of any card, except Titans (rank 'T') = 1
    """
    if min_size is not None:
        n = deck_size(deck)
        if n < min_size:
            raise ValueError(f"Deck must contain at least {min_size} cards (got {n})")

    counts: Dict[str, int] = {}
    for g in deck.goons:
        counts[g.name] = counts.get(g.name, 0) + g.duplicates
        if g.rank == titan_rank and g.duplicates > titan_max:
            raise ValueError(f"Titan '{g.name}' may appear at most {titan_max} time(s)")
    for name, cnt in counts.items():
        # Titans exempt; others capped
        sample = next(g for g in deck.goons if g.name == name)
        if sample.rank != titan_rank and cnt > max_copies:
            raise ValueError(f"'{name}' exceeds max copies ({cnt} > {max_copies})")
