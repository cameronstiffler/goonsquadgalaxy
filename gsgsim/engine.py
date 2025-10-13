from __future__ import annotations

import copy
import os
import random
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple

from .abilities import use_ability
from .loader import build_cards
from .loader import find_squad_leader
from .loader import load_deck_json
from .models import Card
from .models import GameState
from .models import Player
from .payments import Chooser as PaymentChooser
from .payments import distribute_wind
from .rules import apply_wind
from .rules import can_target_card
from .rules import cannot_spend_wind
from .rules import destroy_if_needed

Chooser = PaymentChooser


# --- runtime wrapper for frozen dataclass cards -------------------------------
class _RuntimeCard:
    """
    Lightweight proxy that lets the engine attach mutable, runtime-only fields
    (wind, just_deployed, new_this_turn, ability_used_this_turn, etc.) to cards
    that are defined as frozen dataclasses by the loader.
    """

    __slots__ = ("_base", "__dict__")

    def __init__(self, base):
        object.__setattr__(self, "_base", base)

    def __getattr__(self, name):
        # Delegate to the underlying dataclass if not found on the proxy
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name, value):
        # Store runtime fields on the proxy itself
        self.__dict__[name] = value

    def __repr__(self):
        base = object.__getattribute__(self, "_base")
        return f"<RuntimeCard {getattr(base, 'name', '?')}>"


def _wrap_runtime(card):
    # idempotent: don't double-wrap
    if isinstance(card, _RuntimeCard):
        return card
    return _RuntimeCard(card)


def _is_squad_goon(card) -> bool:
    rank = getattr(card, "rank", None)
    if isinstance(rank, str):
        return rank.strip().upper() in {"SG", "SQUAD GOON"}
    return str(getattr(rank, "name", "")).upper() == "SG"


def _has_squad_goon_duplicate(player: Player, card) -> bool:
    if not _is_squad_goon(card):
        return False
    name = str(getattr(card, "name", "")).strip().lower()
    if not name:
        return False
    for existing in getattr(player, "board", []):
        if existing is card:
            continue
        other_name = str(getattr(existing, "name", "")).strip().lower()
        if other_name == name and _is_squad_goon(existing):
            return True
    return False


def _opponent_for(gs: GameState, player: Player) -> Optional[Player]:
    if getattr(gs, "p1", None) is player:
        return getattr(gs, "p2", None)
    if getattr(gs, "p2", None) is player:
        return getattr(gs, "p1", None)
    if hasattr(gs, "p1") and hasattr(gs, "p2"):
        return gs.p2 if player is getattr(gs, "p1", None) else getattr(gs, "p1", None)
    return None


def _req_field(req: Any, key: str, default: Any = None) -> Any:
    if isinstance(req, dict):
        return req.get(key, default)
    return getattr(req, key, default)


def _req_count(req: Any, default: int = 1) -> int:
    value = _req_field(req, "count", default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _board_for_side(gs: GameState, player: Player, side: Optional[str]) -> List[Card]:
    side_token = (side or "self").strip().lower()
    if side_token == "opponent":
        opp = _opponent_for(gs, player)
        return list(getattr(opp, "board", [])) if opp else []
    return list(getattr(player, "board", []))


def _rank_token(card: Any) -> str:
    rank = getattr(card, "rank", None)
    if isinstance(rank, str):
        return rank.strip().upper()
    if hasattr(rank, "name"):
        return str(rank.name).upper()
    return str(rank).strip().upper()


def _check_deploy_requirement(gs: GameState, player: Player, card: Card, req: Any) -> tuple[bool, Optional[str]]:
    req_type = str(_req_field(req, "type", "")).strip().lower()
    side = _req_field(req, "side", "self")
    board = _board_for_side(gs, player, side)

    if req_type == "requires_card_in_play":
        name = _req_field(req, "card_name")
        if not name:
            return True, None
        count_needed = _req_count(req, 1)
        matched = sum(1 for c in board if str(getattr(c, "name", "")).strip().lower() == str(name).strip().lower())
        if matched < count_needed:
            side_text = "your" if (side or "self").strip().lower() != "opponent" else "the opponent's"
            needed_text = f"{count_needed}x " if count_needed > 1 else ""
            return False, f"cannot deploy: requires {needed_text}{name} on {side_text} board"
        return True, None

    if req_type == "requires_rank_in_play":
        rank_value = str(_req_field(req, "value", "")).strip().upper()
        if not rank_value:
            return True, None
        count_needed = _req_count(req, 1)
        matched = sum(1 for c in board if _rank_token(c) == rank_value)
        if matched < count_needed:
            side_text = "your" if (side or "self").strip().lower() != "opponent" else "the opponent's"
            return False, f"cannot deploy: requires {count_needed} rank {rank_value} goon on {side_text} board"
        return True, None

    if req_type == "requires_faction":
        faction_value = str(_req_field(req, "value", "")).strip().upper()
        if not faction_value:
            return True, None
        count_needed = _req_count(req, 1)
        matched = sum(1 for c in board if str(getattr(c, "faction", "")).strip().upper() == faction_value)
        if matched < count_needed:
            side_text = "your" if (side or "self").strip().lower() != "opponent" else "the opponent's"
            return False, f"cannot deploy: requires {count_needed} {faction_value} goon on {side_text} board"
        return True, None

    if req_type == "requires_unique_in_play":
        name = str(getattr(card, "name", "")).strip().lower()
        if not name:
            return True, None
        # Unique requirements consider all boards.
        in_play: List[Any] = []
        if hasattr(gs, "p1"):
            in_play.extend(getattr(gs.p1, "board", []))
        if hasattr(gs, "p2"):
            in_play.extend(getattr(gs.p2, "board", []))
        if any(str(getattr(c, "name", "")).strip().lower() == name for c in in_play):
            return False, f"cannot deploy: {getattr(card, 'name', 'This goon')} is unique and already in play"
        return True, None

    # Text or unknown requirement types are treated as informational only.
    return True, None


def _check_deploy_requirements(gs: GameState, player: Player, card: Card) -> tuple[bool, Optional[str]]:
    requirements = getattr(card, "deploy_requirements", None) or []
    for req in requirements:
        ok, message = _check_deploy_requirement(gs, player, card, req)
        if not ok:
            return False, message
    return True, None


def draw(player: Player, n: int = 1) -> List[Card]:
    """Draw up to `n` cards from `player.deck` into `player.hand`."""
    if player is None or n <= 0:
        return []

    deck = getattr(player, "deck", None)
    hand = getattr(player, "hand", None)
    if not isinstance(deck, list) or hand is None:
        return []

    drawn: List[Card] = []
    for _ in range(int(n)):
        if not deck:
            break
        card = deck.pop(0)
        try:
            card.new_in_hand = True
        except Exception:
            pass
        hand.append(card)
        drawn.append(card)
    return drawn


def refresh_board_state(gs: GameState) -> None:
    try:
        players = [gs.p1, gs.p2]
    except Exception:
        players = []
    for pl in filter(None, players):
        try:
            count = sum(1 for c in getattr(pl, "board", []) if str(getattr(c, "name", "")).lower() == "shield array node")
            setattr(pl, "_shield_array_protection", count >= 2)
        except Exception:
            setattr(pl, "_shield_array_protection", False)


def _passive_chooser(kind, options):
    if kind == "choose_targets":
        pool = options.get("pool", []) if isinstance(options, dict) else []
        need = options.get("need", ["any"]) if isinstance(options, dict) else ["any"]
        n = 1
        if need and need[0].startswith("two"):
            n = 2
        if need and need[0].startswith("three"):
            n = 3
        return pool[:n]
    if kind == "distribute_wind":
        return {}
    if kind == "search_deck":
        deck = options if isinstance(options, list) else options.get("deck", [])
        return deck[0] if deck else None
    if kind == "select_ability":
        return 0
    if kind == "choose_targets_for_copy":
        return []
    return []


def _apply_passive_abilities(gs: GameState, card: Card) -> None:
    for ability in getattr(card, "abilities", []) or []:
        if getattr(ability, "passive", False):
            try:
                run_machine_effects(gs, card, ability, chooser=_passive_chooser, preset_targets=None)
            except Exception:
                continue


def deploy_from_hand(
    gs: GameState,
    player: Player,
    hand_idx: int,
    chooser: Optional[Chooser] = None,
    contributors: Optional[List[int]] = None,
) -> bool:
    if hand_idx < 0 or hand_idx >= len(player.hand):
        print("invalid hand index")
        return False

    card = player.hand[hand_idx]
    wind_cost = getattr(card, "deploy_wind", 0)
    gear_cost = getattr(card, "deploy_gear", 0)
    meat_cost = getattr(card, "deploy_meat", 0)

    if _has_squad_goon_duplicate(player, card):
        print("cannot deploy: squad goon of this type already in play")
        return False

    ok, reason = _check_deploy_requirements(gs, player, card)
    if not ok:
        print(reason or "cannot deploy: deployment requirements not met")
        return False

    resource_cards: List[Any] = []
    if gear_cost:
        spent = _consume_dead_pool_for_cost(gs, int(gear_cost), "gear", chooser)
        if spent is None:
            _return_dead_pool_cards(gs, resource_cards)
            print("insufficient gear resources in dead pool")
            return False
        resource_cards.extend(spent)
    if meat_cost:
        spent = _consume_dead_pool_for_cost(gs, int(meat_cost), "meat", chooser)
        if spent is None:
            _return_dead_pool_cards(gs, resource_cards)
            print("insufficient meat resources in dead pool")
            return False
        resource_cards.extend(spent)

    # Pay wind
    if not distribute_wind(player, wind_cost, gs=gs, chooser=chooser, contributors=contributors):
        _return_dead_pool_cards(gs, resource_cards)
        return False
    # Move card to board (wrap to allow runtime mutable attrs on frozen dataclasses)
    runtime = _wrap_runtime(card)
    if resource_cards:
        setattr(runtime, "_deploy_dead_pool_sources", list(resource_cards))
    player.board.append(runtime)
    player.hand.pop(hand_idx)
    runtime.wind = 0
    runtime.new_this_turn = True
    runtime.just_deployed = True
    _apply_passive_abilities(gs, runtime)
    _sweep_board_for_kills(gs)
    refresh_board_state(gs)
    return True


def start_of_turn(gs: GameState) -> None:
    _clear_turn_locks(gs)
    # Draw 1 for current player; lose if deck empty
    p = gs.turn_player
    if hasattr(p, "deck") and isinstance(p.deck, list):
        if not p.deck:
            print(f"{p.name} loses: deck empty at draw step!")
            setattr(gs, "loser", getattr(p, "name", "?"))
            _cleanup_until_player_next_turn(gs)
            return
        draw(p, 1)
    # Clear new_this_turn and just_deployed on both players' boards
    # inside start_of_turn(gs), early in the function:

    for side in (gs.p1, gs.p2):
        for c in getattr(side, "board", []):
            if getattr(c, "just_deployed", False):
                c.just_deployed = False
            if getattr(c, "new_this_turn", False):
                c.new_this_turn = False
            if getattr(c, "ability_used_this_turn", False):
                c.ability_used_this_turn = False
            if hasattr(c, "_abilities_used_this_turn"):
                try:
                    c._abilities_used_this_turn = set()
                except Exception:
                    setattr(c, "_abilities_used_this_turn", set())
            if hasattr(c, "_abilities_used_this_turn"):
                try:
                    c._abilities_used_this_turn = set()
                except Exception:
                    setattr(c, "_abilities_used_this_turn", set())
    # Auto-unwind only the current turn player's board, skipping no_unwind
    from .rules import apply_wind_with_resist
    from .rules import consume_status
    from .rules import has_status

    for c in gs.turn_player.board:
        skip_unwind = getattr(c, "no_unwind", False) or has_status(c, "no_unwind_flag")
        if getattr(c, "wind", 0) > 0 and not skip_unwind:
            # Enforce prevent_unwind status
            if has_status(c, "prevent_unwind"):
                consume_status(c, "prevent_unwind", lambda t: t.get("duration") in ("next_unwind", "until_end_of_turn"))
                continue
            apply_wind_with_resist(gs, c, -1, hostile=False)

    # Clean up until_end_of_turn and next_turn statuses
    for side in (gs.p1, gs.p2):
        for c in getattr(side, "board", []):
            status = getattr(c, "status", {})
            # Remove all until_end_of_turn tokens
            for tag, arr in list(status.items()):
                status[tag] = [t for t in arr if t.get("duration") != "until_end_of_turn"]
                if not status[tag]:
                    status.pop(tag)
            # Remove next_turn tokens if added on previous turn
            for tag, arr in list(status.items()):
                status[tag] = [t for t in arr if not (t.get("duration") == "next_turn" and t.get("turn_tag") == gs.turn_number - 1)]
                if not status[tag]:
                    status.pop(tag)
            # Remove until_player_next_turn tokens when owner start turn
            for tag, arr in list(status.items()):
                filtered = []
                for token in arr:
                    if token.get("duration") == "until_player_next_turn":
                        token_owner = token.get("owner")
                        if token_owner is gs.turn_player or (token_owner is not None and getattr(token_owner, "name", None) == getattr(gs.turn_player, "name", None)):
                            continue
                    filtered.append(token)
                if filtered:
                    status[tag] = filtered
                else:
                    status.pop(tag, None)
            if "marked" not in status and getattr(c, "marked", False):
                try:
                    delattr(c, "marked")
                except Exception:
                    c.marked = False

    _cleanup_until_player_next_turn(gs)


def _clear_turn_locks(gs):
    # clear deploy/turn locks and per-turn flags (defensive: both sides)
    for side in (gs.p1, gs.p2):
        for c in getattr(side, "board", []):
            if getattr(c, "just_deployed", False):
                c.just_deployed = False
            if getattr(c, "new_this_turn", False):
                c.new_this_turn = False
            if getattr(c, "ability_used_this_turn", False):
                c.ability_used_this_turn = False


def _sweep_board_for_kills(gs) -> None:
    """
    Belt-and-suspenders: if any card is sitting at wind >= 4, destroy it now.
    This guarantees we never render/go forward with illegal board state even if
    a caller forgot to route through a destroy path after applying wind.
    """
    try:
        sides = (gs.p1, gs.p2)
    except Exception:
        return
    for side in sides:
        for card in list(getattr(side, "board", [])):
            try:
                if int(getattr(card, "wind", 0) or 0) >= 4:
                    destroy_if_needed(gs, card)
            except Exception:
                # never crash on bookkeeping
                continue


def end_of_turn(gs: GameState) -> None:
    # Pass turn and then clear new_this_turn for the next player
    gs.turn_player = gs.p2 if gs.turn_player is gs.p1 else gs.p1
    gs.turn_number += 1
    _sweep_board_for_kills(gs)
    start_of_turn(gs)


def use_ability_cli(gs, src_idx: int, abil_idx: int, target_spec: str | None = None) -> None:
    try:
        card = gs.turn_player.board[src_idx]
    except Exception:
        print("ability failed")
        return
    targets = parse_targets(target_spec or "", gs)
    print(f"DEBUG: use_ability called for {getattr(card, 'name', '?')} abil {abil_idx}")
    ok = use_ability(gs, card, abil_idx, targets)
    print("ability ok" if ok else f'ability failed (passive/new/handler/cost): {getattr(card, "name", "<??>")} [{abil_idx}]')
    _sweep_board_for_kills(gs)
    return


def select_ui(name: str):
    """Lazy import to avoid engine<->UI circular imports."""
    if name and name.lower() == "rich":
        from .ui.rich_ui import RichUI

        return RichUI()
    from .ui.terminal import TerminalUI

    return TerminalUI()


def parse_targets(spec: str, gs) -> list:
    spec = (spec or "").strip()
    if not spec:
        return []
    side, _, rest = spec.partition(":")
    side = side.lower()
    player = gs.p1 if side in ("p1", "self", "me") else gs.p2
    if rest.lower() == "all":
        return list(getattr(player, "board", []))
    idxs = []
    for tok in rest.split(","):
        tok = tok.strip()
        if tok.isdigit():
            idxs.append(int(tok))
    out = []
    board = list(getattr(player, "board", []))
    for i in idxs:
        if 0 <= i < len(board):
            out.append(board[i])
    return out


def parse_payplan(spec: str):
    """Return (side, plan:list[(idx, amt)], force:bool). 'spec' like 'p1:0x2,3x1 [force]'."""
    spec = (spec or "").strip()
    tokens = spec.split()
    if not tokens:
        return None, [], False
    base = tokens[0]
    force = any(t.lower() == "force" for t in tokens[1:])
    side, _, rest = base.partition(":")
    side = side.lower()
    plan = []
    for part in filter(None, (p.strip() for p in rest.split(","))):
        if "x" in part:
            i, x, a = part.partition("x")
            if i.isdigit() and a.isdigit():
                plan.append((int(i), int(a)))
        else:
            # default 1 if no 'xN'
            if part.isdigit():
                plan.append((int(part), 1))
    return side, plan, force


def pay_cli(gs, amount: int, spec: str) -> None:
    from .payments import manual_pay

    side, plan, force = parse_payplan(spec)
    player = gs.p1 if side in ("p1", "self", "me") else gs.p2
    ok = manual_pay(player, amount, plan, allow_lethal_sl=force)
    print("pay ok" if ok else "pay failed")


def pay_to_unwind(gs, target, contributions: List[Tuple[Any, int]]) -> bool:
    owner = _me_owner_of(gs, target)
    if owner is None:
        return False
    if target not in getattr(owner, "board", []):
        return False
    if owner is not getattr(gs, "turn_player", None):
        return False
    status_map = getattr(target, "status", {}) or {}
    if not status_map.get("pay_to_unwind"):
        return False

    resolved: List[Tuple[Any, int]] = []
    total = 0
    for card, amount in contributions or []:
        if amount is None:
            continue
        try:
            amt = int(amount)
        except Exception:
            continue
        if amt <= 0 or card not in getattr(owner, "board", []):
            return False
        if cannot_spend_wind(gs, card):
            return False
        resolved.append((card, amt))
        total += amt
    if total <= 0:
        return False

    before = [(card, int(getattr(card, "wind", 0) or 0)) for card, _ in resolved]
    target_before = int(getattr(target, "wind", 0) or 0)

    try:
        for card, amt in resolved:
            apply_wind(gs, card, amt)
        apply_wind(gs, target, -total)
    except Exception:
        for card, wind in before:
            try:
                setattr(card, "wind", wind)
            except Exception:
                pass
        try:
            setattr(target, "wind", target_before)
        except Exception:
            pass
        return False
    return True


# === canonical wind mutation and checks (rules-backed) ===
def add_wind_and_check(gs, card, delta: int, *, hostile: bool = False) -> int:
    """Mutate wind by delta. KO and retire at >= 4. Return applied delta."""
    from .rules import apply_wind_with_resist
    from .rules import destroy_if_needed

    old = int(getattr(card, "wind", 0) or 0)
    apply_wind_with_resist(gs, card, int(delta), hostile=hostile)
    new = int(getattr(card, "wind", 0) or 0)
    try:
        destroy_if_needed(gs, card)
    except Exception:
        pass
    return new - old


def manual_pay(gs, total: int, targets: list[tuple[str, int]]) -> bool:
    """
    Spend 'total' wind across target board indices (p1|p2,idx).
    Applies KO-at-4 immediately via add_wind_and_check.
    """
    from .rules import apply_wind_with_resist

    if total <= 0:
        return True

    pool = []
    for side, idx in targets:
        pl = gs.p1 if side == "p1" else gs.p2
        try:
            c = pl.board[idx]
        except Exception:
            continue
        if not cannot_spend_wind(c):
            pool.append(c)

    paid = 0
    while paid < total and pool:
        progressed, next_pool = False, []
        for c in pool:
            if c not in getattr(gs.p1, "board", []) and c not in getattr(gs.p2, "board", []):
                continue
            apply_wind_with_resist(c, +1, hostile=True)
            paid += 1
            progressed = True
            if (c in getattr(gs.p1, "board", []) or c in getattr(gs.p2, "board", [])) and not cannot_spend_wind(c):
                next_pool.append(c)
            if paid >= total:
                break
        pool = next_pool
        if not progressed:
            break

    return paid >= total


def manual_pay_cli(gs, amount: int, target_spec: str) -> None:
    from .engine import parse_targets

    targets = parse_targets(target_spec, gs)
    ok = manual_pay(gs, amount, targets)
    print("pay ok" if ok else "pay failed (insufficient eligible goons)")


def _resolve_deck_path(raw: str, root: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def _matches_dead_pool_kind(card, kind: str) -> bool:
    token = (kind or "").strip().lower()
    if token in ("biological", "meat"):
        if token == "meat":
            return bool(getattr(card, "biological", False) or getattr(card, "mechanical", False))
        return bool(getattr(card, "biological", False))
    if token in ("mechanical", "gear"):
        return bool(getattr(card, "mechanical", False))
    return True


def _return_dead_pool_cards(gs, cards: List[Any]) -> None:
    if not cards:
        return
    for card in cards:
        getattr(gs, "dead_pool", []).append(card)
        try:
            if getattr(card, "biological", False):
                gs.dead_pool_bio = int(getattr(gs, "dead_pool_bio", 0) or 0) + 1
            if getattr(card, "mechanical", False):
                gs.dead_pool_mech = int(getattr(gs, "dead_pool_mech", 0) or 0) + 1
        except Exception:
            pass


def _cleanup_until_player_next_turn(gs) -> None:
    owner = getattr(gs, "turn_player", None)
    if owner is None:
        return
    for side in (getattr(gs, "p1", None), getattr(gs, "p2", None)):
        if side is None:
            continue
        for card in getattr(side, "board", []):
            status = getattr(card, "status", {}) or {}
            changed = False
            for tag, arr in list(status.items()):
                new_tokens = []
                for token in arr:
                    if token.get("duration") == "until_player_next_turn":
                        token_owner = token.get("owner")
                        if token_owner is owner or (token_owner is not None and getattr(token_owner, "name", None) == getattr(owner, "name", None)):
                            changed = True
                            continue
                    new_tokens.append(token)
                if new_tokens:
                    status[tag] = new_tokens
                else:
                    status.pop(tag, None)
            if changed:
                setattr(card, "status", status)


def _consume_dead_pool_for_cost(gs, amount: int, kind: str, chooser: Optional[Chooser]) -> Optional[List[Any]]:
    if amount <= 0:
        return []
    pool = [card for card in getattr(gs, "dead_pool", []) or [] if _matches_dead_pool_kind(card, kind)]
    if len(pool) < amount:
        return None

    selected: List[Any] = []
    if chooser is not None:
        choice = chooser(
            "select_dead_pool_cards",
            {
                "kind": kind,
                "amount": amount,
                "options": pool,
            },
        )
        if isinstance(choice, list):
            for card in choice:
                if card in pool and card not in selected:
                    selected.append(card)
                if len(selected) >= amount:
                    break
        elif choice in pool:
            selected.append(choice)
    if len(selected) < amount:
        for card in pool:
            if card not in selected:
                selected.append(card)
            if len(selected) >= amount:
                break
    removed = []
    for card in selected[:amount]:
        removed.extend(_me_banish_from_dead_pool(gs, 1, kind))
    if len(removed) < amount:
        _return_dead_pool_cards(gs, removed)
        return None
    return removed


def trigger_hostile_target_reaction(gs, source, target, amount: int) -> None:
    if target is None or isinstance(target, tuple):
        return
    owner = _me_owner_of(gs, source)
    _me_apply_alter_wind(gs, source, owner, [target], amount, _passive_chooser)


def _expand_deck_cards(deck_obj: Dict[str, Any]) -> List[Card]:
    base_cards = build_cards(deck_obj, faction=deck_obj.get("faction"))
    goons_raw = deck_obj.get("goons", []) or []
    expanded: List[Card] = []
    for idx, card in enumerate(base_cards):
        copies_raw = 1
        if idx < len(goons_raw):
            copies_raw = goons_raw[idx].get("duplicates", 1)
        try:
            copies = int(copies_raw)
        except Exception:
            copies = 1
        if copies <= 0:
            copies = 1
        for _ in range(copies):
            expanded.append(copy.deepcopy(card))
    return expanded


def _pop_squad_leader(deck: List[Card]) -> Optional[Card]:
    sl = find_squad_leader(deck)
    if sl is not None:
        try:
            deck.remove(sl)
        except ValueError:
            pass
    return sl


class _RulesFacade:
    def __init__(self, gs: GameState) -> None:
        self._gs = gs

    def apply_wind(self, card: Card, delta: int) -> int:
        return apply_wind(self._gs, card, delta)

    def destroy_goon(self, card: Card) -> bool:
        return destroy_if_needed(self._gs, card)


def init_game(
    *,
    p1_deck: Optional[str] = None,
    p2_deck: Optional[str] = None,
    rng_seed: Optional[int] = None,
    starting_hand_size: int = 6,
) -> GameState:
    """Construct a fresh `GameState` with decks loaded and ready to play."""

    root = Path(__file__).resolve().parent.parent

    default_p1, default_p2 = ("pcu_deck_strict.json", "narc_deck_strict.json")
    deck1_raw = p1_deck or default_p1
    deck2_raw = p2_deck or default_p2

    deck1_path = _resolve_deck_path(deck1_raw, root)
    deck2_path = _resolve_deck_path(deck2_raw, root)

    if not deck1_path.exists():
        raise FileNotFoundError(f"Deck not found for P1: {deck1_path}")
    if not deck2_path.exists():
        raise FileNotFoundError(f"Deck not found for P2: {deck2_path}")

    deck1_obj = load_deck_json(str(deck1_path))
    deck2_obj = load_deck_json(str(deck2_path))

    cards_p1 = _expand_deck_cards(deck1_obj)
    cards_p2 = _expand_deck_cards(deck2_obj)

    if rng_seed is None:
        seed_env = os.environ.get("GSG_RNG_SEED")
        if seed_env:
            try:
                rng_seed = int(seed_env)
            except Exception:
                rng_seed = None
    rng = random.Random(rng_seed)
    rng.shuffle(cards_p1)
    rng.shuffle(cards_p2)

    p1 = Player(name="P1")
    p2 = Player(name="P2")
    p1.deck = cards_p1
    p2.deck = cards_p2
    p1.hand = []
    p2.hand = []
    p1.board = []
    p2.board = []
    p1.retired = []
    p2.retired = []
    setattr(p1, "faction", deck1_obj.get("faction"))
    setattr(p2, "faction", deck2_obj.get("faction"))
    setattr(p1, "controller", "human")
    setattr(p2, "controller", "human")

    sl1 = _pop_squad_leader(p1.deck)
    sl2 = _pop_squad_leader(p2.deck)
    if sl1 is not None:
        sl1 = _wrap_runtime(sl1)
        sl1.wind = int(getattr(sl1, "wind", 0) or 0)
        sl1.just_deployed = False
        sl1.new_this_turn = False
        p1.board.append(sl1)
    if sl2 is not None:
        sl2 = _wrap_runtime(sl2)
        sl2.wind = int(getattr(sl2, "wind", 0) or 0)
        sl2.just_deployed = False
        sl2.new_this_turn = False
        p2.board.append(sl2)

    draw(p1, starting_hand_size)
    draw(p2, starting_hand_size)

    first_player = p1 if rng.random() < 0.5 else p2

    gs = GameState(p1=p1, p2=p2, turn_player=first_player, phase="main", turn_number=1, rng=rng)
    setattr(gs, "starting_player", first_player)
    gs.dead_pool = []
    gs.dead_pool_bio = 0
    gs.dead_pool_mech = 0
    gs.rules = _RulesFacade(gs)

    def _gs_draw(player: Player, n: int = 1) -> List[Card]:
        return draw(player, n)

    gs.draw = _gs_draw
    gs.deck_paths = {"p1": str(deck1_path), "p2": str(deck2_path)}

    for goon in list(getattr(gs.p1, "board", [])) + list(getattr(gs.p2, "board", [])):
        _apply_passive_abilities(gs, goon)

    _sweep_board_for_kills(gs)
    refresh_board_state(gs)
    return gs


# === MACHINE EFFECTS (Wave A) ==================================================
# Standalone runtime for machine-readable effects. Safe to import and call from
# your ability execution path. Does not print; UI owns messaging/prompts.
#
# Wave A coverage:
# - alter_wind (±N or "X")
# - prevent_unwind
# - disable_abilities, disable_contribution
# - destroy
# - retire_on_destroy, return_to_hand_on_destroy (registered as status flags)
# - grant_resist
# - cannot_be_targeted, must_be_destroyed_first
# - search_deck, draw_cards
# Targets: self, any, ally_all, opponent_all, ally_all_except:self,
#          all_opponent_playergoons, two_goons, three_goons, card_name:*,
#          dead_pool
# Durations: instant, until_end_of_turn, next_turn, next_unwind, persistent


# -- helpers: normalize dataclass Ability/Effect objects to dict-like mappings
def _me_as_mapping_ability(ability):
    """Return a dict with at least 'effects' for either a dict or a dataclass Ability."""
    if isinstance(ability, dict):
        return ability
    out = {}
    for k in ("name", "passive", "must_use", "text"):
        if hasattr(ability, k):
            out[k] = getattr(ability, k)
    effs = getattr(ability, "effects", None)
    out["effects"] = [_me_as_mapping_effect(e) for e in (effs or [])]
    return out


def _me_as_mapping_effect(eff):
    """Return a dict with keys used by the interpreter for either dict or dataclass Effect."""
    if isinstance(eff, dict):
        return eff
    d = {}
    for k in ("effect_type", "target", "duration", "amount", "value", "side", "count"):
        if hasattr(eff, k):
            d[k] = getattr(eff, k)
    return d


def _me_ensure_runtime_containers(gs):
    if not hasattr(gs, "statuses"):
        gs.statuses = {}  # board-wide statuses keyed by (scope, id)
    if not hasattr(gs, "marks"):
        gs.marks = {}  # e.g., {"marked": {target_card_id: source_card_id}}
    # Per-goon status dict is created on demand by _me_status_for(goon)


def _me_status_for(goon):
    if not hasattr(goon, "status"):
        goon.status = {}
    return goon.status


def _me_owner_of(gs, goon):
    # Defensive: tests may pass a lightweight gs (e.g., SimpleNamespace) with only turn_player
    try:
        if hasattr(gs, "p1") and hasattr(gs, "p2"):
            if goon in getattr(gs.p1, "board", []) or goon in getattr(gs.p1, "hand", []) or goon in getattr(gs.p1, "deck", []):
                return gs.p1
            if goon in getattr(gs.p2, "board", []) or goon in getattr(gs.p2, "hand", []) or goon in getattr(gs.p2, "deck", []):
                return gs.p2
    except Exception:
        pass
    # Fallback to current turn player when full state isn't present
    return getattr(gs, "turn_player", None) or getattr(gs, "p1", None) or getattr(gs, "p2", None)


def _me_opponent_of(gs, player):
    if hasattr(gs, "p1") and hasattr(gs, "p2"):
        return gs.p2 if player is gs.p1 else gs.p1
    # If opponents aren't modeled (tests), just return the same player
    return player


def _me_all_goons(gs) -> List[Any]:
    if hasattr(gs, "p1") and hasattr(gs, "p2"):
        return list(getattr(gs.p1, "board", [])) + list(getattr(gs.p2, "board", []))
    # Minimal harness (tests) — use turn_player only
    tp = getattr(gs, "turn_player", None)
    return list(getattr(tp, "board", [])) if tp else []


def _me_filter_by_tokens(gs, source, tokens: List[str], context: Optional[Dict[str, Any]] = None) -> List[Any]:
    # Simple resolver for Wave A. When ambiguous (e.g., "any"), the caller/chooser must pick.
    out: List[Any] = []
    context = context or {}
    owner = _me_owner_of(gs, source)
    opp = _me_opponent_of(gs, owner)
    for tok in tokens:
        if tok == "self":
            out.append(source)
        elif tok == "ally_all":
            out.extend(owner.board)
        elif tok == "opponent_all":
            out.extend(opp.board)
        elif tok == "ally_all_except:self":
            out.extend([g for g in owner.board if g is not source])
        elif tok == "all_opponent_playergoons":
            out.extend(opp.board)
        elif tok == "dead_pool":
            # Represent the shared Dead Pool as a tuple marker; Wave B can expand if needed.
            out.append(("dead_pool",))
        elif tok == "goon_using_ability":
            out.append(source)
        elif tok.startswith("card_name:"):
            name = tok.split(":", 1)[1]
            for g in _me_all_goons(gs):
                if getattr(g, "name", None) == name:
                    out.append(g)
        elif tok == "revived_by_self":
            out.extend(context.get("revived", []))
        elif tok in ("any", "two_goons", "three_goons"):
            # chooser must handle these
            out.append(("CHOOSE", tok))
        else:
            # Unrecognized token: ignore (schema should prevent this)
            continue
    return out


def _me_register_duration(
    targets: Iterable[Any],
    tag: str,
    value: Any,
    duration: str,
    turn_number: Optional[int] = None,
    owner: Optional[Any] = None,
):
    for t in targets:
        if isinstance(t, tuple):  # markers like ('dead_pool',) or ('CHOOSE', …)
            continue
        st = _me_status_for(t)
        arr = st.setdefault(tag, [])
        token = {"value": value, "duration": duration}
        if duration == "next_turn" and turn_number is not None:
            token["turn_tag"] = turn_number
        if owner is not None:
            token["owner"] = owner
        arr.append(token)


def _me_apply_alter_wind(gs, source, owner, targets: List[Any], amount, chooser) -> None:
    from .rules import apply_wind_with_resist

    if amount == "X":
        dist = chooser("distribute_wind", targets) or {}
        for goon, delta in dist.items():
            if isinstance(goon, tuple):
                continue
            _me_apply_alter_wind(gs, source, owner, [goon], delta, chooser)
        return

    for t in targets:
        if isinstance(t, tuple):
            continue
        amt = int(amount)
        hostile = amt > 0 and _me_is_hostile(gs, owner, t)
        if hostile and amt > 0:
            status = getattr(t, "status", {}) or {}
            redirects = status.get("redirect_damage", [])
            redirected = False
            for token in redirects:
                partner = token.get("value")
                if partner and partner is not t and partner in getattr(owner, "board", []):
                    apply_wind_with_resist(gs, partner, amt, hostile=True)
                    redirected = True
                    break
            if redirected:
                continue
        if hostile and amt > 0:
            bonus = 0
            try:
                status = getattr(t, "status", {}) or {}
                for token in status.get("increase_damage_taken", []):
                    bonus += int(token.get("value", 0) or 0)
            except Exception:
                pass
            amt += bonus
        if hostile and amt > 0:
            try:
                setattr(t, "_destroyed_by", source)
            except Exception:
                pass
        apply_wind_with_resist(gs, t, amt, hostile=hostile)
        if getattr(t, "wind", 0) < 4 and hasattr(t, "_destroyed_by") and getattr(t, "_destroyed_by") is source:
            try:
                delattr(t, "_destroyed_by")
            except Exception:
                pass


def _me_apply_destroy(gs, source, targets: List[Any]) -> None:
    from .rules import destroy_if_needed

    for t in targets:
        if isinstance(t, tuple):
            continue
        try:
            setattr(t, "_destroyed_by", source)
        except Exception:
            pass
        destroy_if_needed(gs, t)


def _me_copy_abilities_from_sources(target) -> None:
    sources = list(getattr(target, "_deploy_dead_pool_sources", []) or [])
    if not sources:
        return
    copied: List[Any] = list(getattr(target, "_copied_abilities_from_dead_pool", []))
    base_list = list(getattr(target, "abilities", []) or [])
    for source in sources:
        for ability in getattr(source, "abilities", []) or []:
            clone = copy.deepcopy(ability)
            try:
                setattr(clone, "_copied_from_card", getattr(source, "name", None))
            except Exception:
                pass
            base_list.append(clone)
            copied.append(clone)
    target.abilities = base_list
    target._copied_abilities_from_dead_pool = copied
    try:
        delattr(target, "_deploy_dead_pool_sources")
    except Exception:
        pass


def _me_clear_copied_abilities(card) -> None:
    copied = list(getattr(card, "_copied_abilities_from_dead_pool", []) or [])
    if not copied:
        return
    abilities = [ab for ab in getattr(card, "abilities", []) if ab not in copied]
    card.abilities = abilities
    try:
        delattr(card, "_copied_abilities_from_dead_pool")
    except Exception:
        pass


def _me_apply_draw(gs, player, n: int):
    for _ in range(max(0, int(n))):
        if getattr(player, "deck", None):
            player.hand.append(player.deck.pop(0))


def _me_apply_search_deck(gs, player, chooser):
    # chooser should return an object that exists in player.deck
    pick = chooser("search_deck", player.deck)
    if pick in player.deck:
        player.deck.remove(pick)
        player.hand.append(pick)
    # naive reshuffle; if you have gs.rng, you can swap it in later
    if getattr(gs, "rng", None):
        gs.rng.shuffle(player.deck)
    else:
        player.deck.reverse()
        player.deck.reverse()


def _me_banish_from_dead_pool(gs, amount: int, kind: str) -> List[Any]:
    removed: List[Any] = []
    if amount <= 0:
        return removed
    kind = (kind or "").strip().lower()
    pool = list(getattr(gs, "dead_pool", []) or [])

    def _matches(card) -> bool:
        if kind in ("biological", "meat"):
            if kind == "meat":
                return bool(getattr(card, "biological", False) or getattr(card, "mechanical", False))
            return bool(getattr(card, "biological", False))
        if kind in ("mechanical", "gear"):
            return bool(getattr(card, "mechanical", False))
        return True

    for card in pool:
        if len(removed) >= amount:
            break
        if not _matches(card):
            continue
        getattr(gs, "dead_pool", []).remove(card)
        try:
            if getattr(card, "biological", False):
                gs.dead_pool_bio = max(0, int(getattr(gs, "dead_pool_bio", 0) or 0) - 1)
            if getattr(card, "mechanical", False):
                gs.dead_pool_mech = max(0, int(getattr(gs, "dead_pool_mech", 0) or 0) - 1)
        except Exception:
            pass
        removed.append(card)
    return removed


def _me_parse_csv(value: str) -> List[str]:
    if not value:
        return []
    return [piece.strip() for piece in str(value).split(",") if piece.strip()]


def _me_is_hostile(gs, owner, target) -> bool:
    opp = _me_opponent_of(gs, owner)
    return target in getattr(opp, "board", [])


def _me_resurrect_from_dead_pool(gs, owner, amount: int, kind: Optional[str], context: Dict[str, Any]):
    pool = list(getattr(gs, "dead_pool", []) or [])
    revived: List[Any] = []

    kind = (kind or "").strip().lower()

    def _matches(card):
        if not kind:
            return True
        if kind in ("gear", "mechanical"):
            return bool(getattr(card, "mechanical", False))
        if kind in ("meat", "biological"):
            return bool(getattr(card, "biological", False))
        return True

    for card in list(pool):
        if amount is not None and len(revived) >= int(amount):
            break
        if not _matches(card):
            continue
        if _has_squad_goon_duplicate(owner, card):
            continue
        getattr(gs, "dead_pool", []).remove(card)
        try:
            card.wind = 0
            card.new_this_turn = True
            card.just_deployed = True
        except Exception:
            pass
        try:
            if getattr(card, "biological", False):
                gs.dead_pool_bio = max(0, int(getattr(gs, "dead_pool_bio", 0) or 0) - 1)
            if getattr(card, "mechanical", False):
                gs.dead_pool_mech = max(0, int(getattr(gs, "dead_pool_mech", 0) or 0) - 1)
        except Exception:
            pass
        owner.board.append(card)
        revived.append(card)

    return revived


def _me_copy_and_cast(gs, owner, targets: List[Any], chooser: Callable) -> None:
    from .abilities import use_ability

    target = next((t for t in targets if not isinstance(t, tuple)), None)
    if target is None:
        return
    abilities = list(getattr(target, "abilities", []) or [])
    if not abilities:
        return

    payload = {"card": target, "abilities": abilities}
    idx = chooser("select_ability", payload)
    try:
        ability_idx = int(idx)
    except Exception:
        ability_idx = 0
    if not (0 <= ability_idx < len(abilities)):
        ability_idx = 0

    choice = chooser(
        "choose_targets_for_copy",
        {
            "source": target,
            "ability": abilities[ability_idx],
            "pool": _me_all_goons(gs),
            "need": ["any"],
        },
    )
    if isinstance(choice, list):
        new_targets = choice
    elif choice is None:
        new_targets = []
    else:
        new_targets = [choice]

    original_turn_player = gs.turn_player
    owning_player = _me_owner_of(gs, target)
    try:
        if owning_player is not None:
            gs.turn_player = owning_player
        use_ability(gs, target, ability_idx, new_targets)
    finally:
        gs.turn_player = original_turn_player


def _me_apply_conditional_protection(gs, owner, condition: str) -> None:
    cond = str(condition or "")
    if cond.lower() == "shield_array_nodes>=2":
        board = getattr(owner, "board", [])
        count = sum(1 for c in board if str(getattr(c, "name", "")).lower() == "shield array node")
        setattr(owner, "_shield_array_protection", count >= 2)
        refresh_board_state(gs)


def run_machine_effects(gs, source, ability: Dict[str, Any], chooser: Callable, preset_targets: Optional[List[Any]] = None):
    """
    Apply all Wave A effects for a single ability. `chooser(kind, options)` is a
    callback the UI supplies to resolve 'any'/'two_goons'/'three_goons' and 'X'.
    Returns a list of prompts created during execution (UI may have already handled).
    """
    _me_ensure_runtime_containers(gs)

    # >>>> IMPORTANT: dataclass-safe access <<<<
    ab_map = _me_as_mapping_ability(ability)
    effects = ab_map.get("effects") or []
    # ------------------------------------------

    if not effects:
        return []

    owner = _me_owner_of(gs, source)
    prompts: List[Tuple[str, Any]] = []
    context: Dict[str, Any] = {"revived": []}

    try:
        setattr(gs, "_current_effect_source", source)
    except Exception:
        pass

    preset = list(preset_targets or [])
    preset_idx = 0

    for eff in effects:
        # >>>> IMPORTANT: dataclass-safe access <<<<
        eff = _me_as_mapping_effect(eff)
        # ------------------------------------------
        et = eff.get("effect_type")
        tokens = eff.get("target") or []
        duration = eff.get("duration", "instant")
        amount = eff.get("amount", None)

        # Resolve targets
        resolved = _me_filter_by_tokens(gs, source, tokens, context)

        # Let chooser resolve any ambiguous tokens
        needs_choice = [t for t in resolved if isinstance(t, tuple) and t[0] == "CHOOSE"]
        if needs_choice:
            choice: List[Any] = []
            while preset_idx < len(preset) and len(choice) < len(needs_choice):
                choice.append(preset[preset_idx])
                preset_idx += 1
            if len(choice) < len(needs_choice):
                extra = chooser(
                    "choose_targets",
                    {
                        "source": source,
                        "ability": ability,
                        "need": [t[1] for t in needs_choice],
                        "pool": _me_all_goons(gs),
                    },
                )
                extra_list = extra if isinstance(extra, list) else [extra]
                for item in extra_list:
                    if len(choice) >= len(needs_choice):
                        break
                    choice.append(item)
            new_resolved = []
            it = iter(choice)
            for t in resolved:
                if isinstance(t, tuple) and t[0] == "CHOOSE":
                    try:
                        new_resolved.append(next(it))
                    except StopIteration:
                        continue
                else:
                    new_resolved.append(t)
            resolved = new_resolved

        # Enforce targeting restrictions (e.g., SL protection)
        filtered: List[Any] = []
        for target in resolved:
            if isinstance(target, tuple) or target is None:
                filtered.append(target)
                continue
            hostile = bool(owner and _me_owner_of(gs, target) is not owner)
            if not can_target_card(gs, source, target, hostile=hostile):
                continue
            filtered.append(target)
        resolved = filtered

        # Execute effect
        if et == "alter_wind":
            _me_apply_alter_wind(gs, source, owner, resolved, amount, chooser)
        elif et == "prevent_unwind":
            _me_register_duration(resolved, "prevent_unwind", True, duration, gs.turn_number, owner)
        elif et == "disable_abilities":
            _me_register_duration(resolved, "disable_abilities", True, duration, gs.turn_number, owner)
        elif et == "disable_contribution":
            _me_register_duration(resolved, "disable_contribution", True, duration, gs.turn_number, owner)
        elif et == "destroy":
            _me_apply_destroy(gs, source, resolved)
        elif et == "retire_on_destroy":
            _me_register_duration(resolved, "retire_on_destroy", True, duration, gs.turn_number, owner)
        elif et == "return_to_hand_on_destroy":
            _me_register_duration(resolved, "return_to_hand_on_destroy", True, duration, gs.turn_number, owner)
        elif et == "grant_resist":
            _me_register_duration(resolved, "resist", True, duration, gs.turn_number, owner)
        elif et == "cannot_be_targeted":
            _me_register_duration(resolved, "cannot_be_targeted", True, duration, gs.turn_number, owner)
        elif et == "must_be_destroyed_first":
            _me_register_duration(resolved, "must_be_destroyed_first", True, duration, gs.turn_number, owner)
        elif et == "search_deck":
            _me_apply_search_deck(gs, owner, chooser)
        elif et == "draw_cards":
            _me_apply_draw(gs, owner, int(amount or 1))
        elif et == "banish_from_dead_pool":
            kind = str(eff.get("value") or "").strip().lower()
            need = max(0, int(amount or 0))
            removed = _me_banish_from_dead_pool(gs, need, kind)
            if len(removed) < need:
                # restore on failure
                for card in removed:
                    getattr(gs, "dead_pool", []).append(card)
                    try:
                        if getattr(card, "biological", False):
                            gs.dead_pool_bio = int(getattr(gs, "dead_pool_bio", 0) or 0) + 1
                        if getattr(card, "mechanical", False):
                            gs.dead_pool_mech = int(getattr(gs, "dead_pool_mech", 0) or 0) + 1
                    except Exception:
                        pass
                # If chooser exists, it can surface failure; skip effect
                continue
            context.setdefault("banished_dead_pool", []).extend(removed)
        elif et == "destroy_attacker_on_destroy":
            for t in resolved:
                if isinstance(t, tuple):
                    continue
                setattr(t, "_retaliate_on_destroy", True)
        elif et == "enable_double_use_against_target":
            _me_register_duration(resolved, "double_use_against", True, duration, gs.turn_number, owner)
        elif et == "mark_target":
            _me_register_duration(resolved, "marked", source, duration, gs.turn_number, owner)
            for t in resolved:
                if isinstance(t, tuple):
                    continue
                try:
                    setattr(t, "marked", True)
                except Exception:
                    pass
        elif et == "increase_damage_taken":
            _me_register_duration(resolved, "increase_damage_taken", int(amount or 0), duration, gs.turn_number, owner)
        elif et == "reduce_cost_for_cards_against_marked":
            _me_register_duration(
                resolved,
                "mark_cost_reduction",
                _me_parse_csv(eff.get("value", "")),
                duration,
                gs.turn_number,
                owner,
            )
        elif et == "copy_and_cast_target_ability":
            _me_copy_and_cast(gs, owner, resolved, chooser)
        elif et == "conditional_cannot_be_targeted_by_negative_effects":
            _me_apply_conditional_protection(gs, owner, eff.get("value"))
        elif et == "enable_contribution":
            _me_register_duration(resolved, "enable_contribution", source, duration, gs.turn_number, owner)
        elif et == "destroy_self_if_target_destroyed":
            for t in resolved:
                if isinstance(t, tuple):
                    continue
                status = _me_status_for(t)
                arr = status.setdefault("destroy_dependents", [])
                token = {"value": source, "duration": duration}
                if duration == "next_turn":
                    token["turn_tag"] = gs.turn_number
                arr.append(token)
        elif et == "grant_followup_on_destroy":
            _me_register_duration(
                [source],
                "grant_followup_on_destroy",
                {"amount": int(amount or 0), "target": tokens},
                duration,
                gs.turn_number,
                owner,
            )
        elif et == "must_attack":
            _me_register_duration(resolved, "must_attack", True, duration, gs.turn_number, owner)
        elif et == "no_unwind":
            _me_register_duration(resolved, "no_unwind_flag", True, duration, gs.turn_number, owner)
        elif et == "redirect_damage":
            _me_register_duration(resolved, "redirect_damage", source, duration, gs.turn_number, owner)
        elif et == "copy_abilities_on_deploy_from_deadpool":
            for t in resolved:
                if isinstance(t, tuple):
                    continue
                _me_copy_abilities_from_sources(t)
        elif et == "pay_to_unwind":
            _me_register_duration(resolved, "pay_to_unwind", {"source": source}, "persistent", gs.turn_number, owner)
        elif et == "grant_cannot_be_targeted_by_enemy_abilities":
            _me_register_duration(resolved, "cannot_be_targeted_enemy", True, duration, gs.turn_number, owner)
        elif et == "alter_wind_when_targeted_by_ability":
            amt = int(amount or 0)
            payload = {"amount": amt, "hostile_only": True}
            _me_register_duration(resolved, "hostile_target_reaction", payload, duration, gs.turn_number, owner)
        elif et == "alter_deploy_wind":
            if amount:
                try:
                    bonus = int(amount)
                except Exception:
                    bonus = 0
                if bonus:
                    _me_register_duration(resolved, "deploy_contribution_bonus", bonus, duration, gs.turn_number, owner)
        elif et == "resurrect_from_dead_pool":
            revived = _me_resurrect_from_dead_pool(gs, owner, int(amount or 1), eff.get("value"), context)
            context.setdefault("revived", []).extend(revived)
        else:
            # Unknown (Wave B or beyond): ignore here.
            pass

    if hasattr(gs, "_current_effect_source"):
        try:
            delattr(gs, "_current_effect_source")
        except Exception:
            pass

    return prompts
