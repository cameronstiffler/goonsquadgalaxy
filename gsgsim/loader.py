# === IMPORT SENTRY ===
from __future__ import annotations

import json
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .models import Ability
from .models import Card
from .models import Rank


def load_deck_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_rank(text: str | None) -> Rank:
    map_ = {
        "sl": Rank.SL,
        "squad leader": Rank.SL,
        "sg": Rank.SG,
        "bg": Rank.BG,
        "titan": Rank.TITAN,
        "tn": Rank.TN,
        "basic goon": Rank.BG,  # fallback for your data
    }
    t = (text or "").strip().lower()
    return map_.get(t, Rank.BG)


def build_cards(deck_obj: Dict[str, Any], faction: Optional[str] = None) -> List[Card]:
    cards: List[Card] = []
    for raw in deck_obj.get("goons", []):
        raw = _normalize_card_flags(raw, default_faction=faction)
        goon_name = raw["name"]
        rank = parse_rank(raw.get("rank", "Basic Goon"))
        traits = set(raw.get("icons", []))

        # deploy costs (tokens like "2w", "1g", "3m" or schema dict)
        dw = dg = dm = 0
        deploy_cost = raw.get("deploy_cost", [])

        def _to_int(val):
            if isinstance(val, str) and val.strip().upper() == "X":
                return "X"
            try:
                return int(val or 0)
            except Exception:
                return 0

        if isinstance(deploy_cost, dict):
            dw = _to_int(deploy_cost.get("wind", 0))
            dg = _to_int(deploy_cost.get("gear", 0))
            dm = _to_int(deploy_cost.get("meat", 0))
        else:
            for tok in deploy_cost:
                s = str(tok or "").strip().lower()
                m = re.fullmatch(r"(\d+)\s*([wgm])", s)
                if not m:
                    continue
                n = int(m.group(1))
                kind = m.group(2)
                if kind == "w":
                    dw += n
                elif kind == "g":
                    dg += n
                elif kind == "m":
                    dm += n

        abilities: List[Ability] = []
        deploy_requirements_raw = raw.get("deploy_requirements", []) or []
        if isinstance(deploy_requirements_raw, list):
            deploy_requirements = [dict(req) if isinstance(req, dict) else req for req in deploy_requirements_raw]
        else:
            deploy_requirements = []
        for a in raw.get("abilities", []):
            cost: Dict[str, int] = {}
            passive = False
            raw_cost = a.get("cost", [])
            if isinstance(raw_cost, dict):
                cost = {
                    "wind": _to_int(raw_cost.get("wind", 0)),
                    "gear": _to_int(raw_cost.get("gear", 0)),
                    "meat": _to_int(raw_cost.get("meat", 0)),
                }
            else:
                for ctok in raw_cost:
                    t = str(ctok or "").strip().lower()
                    if t == "p":
                        passive = True
                        continue
                    m = re.fullmatch(r"(\d+)\s*([wgm])", t)
                    if not m:
                        continue
                    n = int(m.group(1))
                    k = m.group(2)
                    key = {"w": "wind", "g": "gear", "m": "meat"}[k]
                    cost[key] = cost.get(key, 0) + n
            if "wind" not in cost:
                cost["wind"] = cost.get("wind", 0)
            if "gear" not in cost:
                cost["gear"] = cost.get("gear", 0)
            if "meat" not in cost:
                cost["meat"] = cost.get("meat", 0)
            passive = bool(a.get("passive", passive))
            # effect inference minimal (keep as-is)
            aname = a.get("name", "ABILITY")
            effects = a.get("effects", []) or []
            ab = Ability(aname, cost, effects, passive=passive)
            setattr(ab, "text", a.get("text", ""))
            try:
                setattr(ab, "must_use", bool(a.get("must_use", False)))
            except Exception:
                pass
            abilities.append(ab)

        card = Card(
            name=goon_name,
            rank=rank,
            faction=faction,
            traits=traits,
            abilities=abilities,
            deploy_requirements=deploy_requirements,
            deploy_wind=dw,
            deploy_gear=dg,
            deploy_meat=dm,
        )
        _apply_card_flags(card, raw)
        for attr, key in (("image_url_full", "image_url_full"), ("image_url_mini", "image_url_mini")):
            val = raw.get(key)
            if val:
                try:
                    setattr(card, attr, val)
                except Exception:
                    pass
        cards.append(card)
    return cards


def find_squad_leader(cards: List[Card]) -> Optional[Card]:
    for c in cards:
        if c.rank == Rank.SL:
            return c
    return None


# === Back-compat + normalization for card flags ===
def _normalize_card_flags(d: dict, default_faction: str | None = None) -> dict:
    """
    Supports either explicit booleans or legacy 'icons' list.
    Produces: faction, biological, mechanical, resist, no_unwind on the dict.
    """
    icons = {str(x).strip().lower() for x in (d.get("icons") or [])}

    # faction
    if not d.get("faction"):
        if "narc" in icons:
            d["faction"] = "NARC"
        elif "pcu" in icons:
            d["faction"] = "PCU"
        elif default_faction:
            d["faction"] = default_faction

    def ensure_bool(key: str, token: str):
        if key not in d:
            d[key] = token in icons

    ensure_bool("biological", "biological")
    ensure_bool("mechanical", "mechanical")
    ensure_bool("resist", "resist")
    ensure_bool("no_unwind", "no_unwind")
    return d


def _apply_card_flags(card, data: dict) -> None:
    """
    Ensure the Card instance exposes booleans the UI expects.
    Explicit booleans beat icons. Keep icons on card as fallback.
    """
    icons = {str(x).strip().lower() for x in (data.get("icons") or [])}

    def val(key: str, token: str) -> bool:
        return bool(data.get(key, False) or (token in icons))

    for attr, token in (
        ("biological", "biological"),
        ("mechanical", "mechanical"),
        ("resist", "resist"),
        ("no_unwind", "no_unwind"),
    ):
        try:
            if not getattr(card, attr, False):
                setattr(card, attr, val(attr, token))
        except Exception:
            pass

    # Keep a copy of icons on the card so UI can read it if needed
    try:
        if not getattr(card, "icons", None) and icons:
            card.icons = list(icons)
    except Exception:
        pass
