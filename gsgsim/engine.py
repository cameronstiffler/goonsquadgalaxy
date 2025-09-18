from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Tuple

from .abilities import use_ability
from .models import GameState
from .models import Player
from .payments import distribute_wind
from .rules import cannot_spend_wind
from .rules import destroy_if_needed

Chooser = Callable[[List[Tuple[int, object, int]], int], Optional[List[Tuple[int, int]]]]


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


def deploy_from_hand(gs: GameState, player: Player, hand_idx: int, chooser: Optional[Chooser] = None) -> bool:
    if hand_idx < 0 or hand_idx >= len(player.hand):
        print("invalid hand index")
        return False

    card = player.hand[hand_idx]
    wind_cost = getattr(card, "deploy_wind", 0)
    gear_cost = getattr(card, "deploy_gear", 0)
    meat_cost = getattr(card, "deploy_meat", 0)
    if gear_cost or meat_cost:
        # Not implemented in this build
        print("could not pay wind")
        return False

    # Pay wind
    if not distribute_wind(player, wind_cost, gs=gs, chooser=chooser):
        return False
    # Move card to board (wrap to allow runtime mutable attrs on frozen dataclasses)
    runtime = _wrap_runtime(card)
    player.board.append(runtime)
    player.hand.pop(hand_idx)
    runtime.wind = 0
    runtime.new_this_turn = True
    runtime.just_deployed = True
    _sweep_board_for_kills(gs)
    return True


def start_of_turn(gs: GameState) -> None:
    _clear_turn_locks(gs)
    # Draw 1 for current player; lose if deck empty
    p = gs.turn_player
    if hasattr(p, "deck") and isinstance(p.deck, list):
        if not p.deck:
            print(f"{p.name} loses: deck empty at draw step!")
            return
        card = p.deck.pop(0)
        p.hand.append(card)
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
    # Auto-unwind only the current turn player's board, skipping no_unwind
    from .rules import apply_wind
    from .rules import consume_status
    from .rules import has_status

    for c in gs.turn_player.board:
        if getattr(c, "wind", 0) > 0 and not getattr(c, "no_unwind", False):
            # Enforce prevent_unwind status
            if has_status(c, "prevent_unwind"):
                consume_status(c, "prevent_unwind", lambda t: t.get("duration") in ("next_unwind", "until_end_of_turn"))
                continue
            apply_wind(gs, c, -1)

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


# === canonical wind mutation and checks (rules-backed) ===
def add_wind_and_check(gs, card, delta: int) -> int:
    """Mutate wind by delta. KO and retire at >= 4. Return applied delta."""
    old = int(getattr(card, "wind", 0) or 0)
    new = max(0, old + int(delta))
    card.wind = new

    # KO at 4+ wind
    if new >= 4:
        owner = None
        if hasattr(gs, "p1") and card in getattr(gs.p1, "board", []):
            owner = gs.p1
        elif hasattr(gs, "p2") and card in getattr(gs.p2, "board", []):
            owner = gs.p2
        if owner:
            try:
                owner.board.remove(card)
            except ValueError:
                pass
            owner.retired.append(card)
    return new - old


def manual_pay(gs, total: int, targets: list[tuple[str, int]]) -> bool:
    """
    Spend 'total' wind across target board indices (p1|p2,idx).
    Applies KO-at-4 immediately via add_wind_and_check.
    """
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
    # greedy round-robin, 1 wind at a time
    while paid < total and pool:
        progressed, next_pool = False, []
        for c in pool:
            # skip if already retired
            if c not in getattr(gs.p1, "board", []) and c not in getattr(gs.p2, "board", []):
                continue
            add_wind_and_check(gs, c, +1)
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
    if goon in gs.p1.board or goon in gs.p1.hand or goon in gs.p1.deck:
        return gs.p1
    return gs.p2


def _me_opponent_of(gs, player):
    return gs.p2 if player is gs.p1 else gs.p1


def _me_all_goons(gs) -> List[Any]:
    return list(gs.p1.board) + list(gs.p2.board)


def _me_filter_by_tokens(gs, source, tokens: List[str]) -> List[Any]:
    # Simple resolver for Wave A. When ambiguous (e.g., "any"), the caller/chooser must pick.
    out: List[Any] = []
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
        elif tok.startswith("card_name:"):
            name = tok.split(":", 1)[1]
            for g in _me_all_goons(gs):
                if getattr(g, "name", None) == name:
                    out.append(g)
        elif tok in ("any", "two_goons", "three_goons"):
            # chooser must handle these
            out.append(("CHOOSE", tok))
        else:
            # Unrecognized token: ignore (schema should prevent this)
            continue
    return out


def _me_register_duration(targets: Iterable[Any], tag: str, value: Any, duration: str):
    for t in targets:
        if isinstance(t, tuple):  # markers like ('dead_pool',) or ('CHOOSE', …)
            continue
        st = _me_status_for(t)
        arr = st.setdefault(tag, [])
        arr.append({"value": value, "duration": duration})


def _me_apply_alter_wind(gs, targets: List[Any], amount, chooser) -> None:
    # amount can be int or "X" (chooser must supply actual distribution for X)
    from .engine import add_wind_and_check  # local canonical wind mutation

    if amount == "X":
        # Expect chooser to return a dict {goon: int}
        dist = chooser("distribute_wind", targets)
        for goon, delta in (dist or {}).items():
            add_wind_and_check(gs, goon, int(delta))
    else:
        for t in targets:
            if isinstance(t, tuple):
                continue
            add_wind_and_check(gs, t, int(amount))


def _me_apply_destroy(gs, targets: List[Any]) -> None:
    from .rules import destroy_if_needed

    for t in targets:
        if isinstance(t, tuple):
            continue
        destroy_if_needed(gs, t)


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


def run_machine_effects(gs, source, ability: Dict[str, Any], chooser: Callable):
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

    for eff in effects:
        # >>>> IMPORTANT: dataclass-safe access <<<<
        eff = _me_as_mapping_effect(eff)
        # ------------------------------------------
        et = eff.get("effect_type")
        tokens = eff.get("target") or []
        duration = eff.get("duration", "instant")
        amount = eff.get("amount", None)

        # Resolve targets
        resolved = _me_filter_by_tokens(gs, source, tokens)

        # Let chooser resolve any ambiguous tokens
        needs_choice = [t for t in resolved if isinstance(t, tuple) and t[0] == "CHOOSE"]
        if needs_choice:
            choice = chooser("choose_targets", {"source": source, "ability": ability, "need": [t[1] for t in needs_choice], "pool": _me_all_goons(gs)})
            new_resolved = []
            it = iter(choice if isinstance(choice, list) else [choice])
            for t in resolved:
                if isinstance(t, tuple) and t[0] == "CHOOSE":
                    try:
                        new_resolved.append(next(it))
                    except StopIteration:
                        continue
                else:
                    new_resolved.append(t)
            resolved = new_resolved

        # Execute effect
        if et == "alter_wind":
            _me_apply_alter_wind(gs, resolved, amount, chooser)
        elif et == "prevent_unwind":
            _me_register_duration(resolved, "prevent_unwind", True, duration)
        elif et == "disable_abilities":
            _me_register_duration(resolved, "disable_abilities", True, duration)
        elif et == "disable_contribution":
            _me_register_duration(resolved, "disable_contribution", True, duration)
        elif et == "destroy":
            _me_apply_destroy(gs, resolved)
        elif et == "retire_on_destroy":
            _me_register_duration(resolved, "retire_on_destroy", True, duration)
        elif et == "return_to_hand_on_destroy":
            _me_register_duration(resolved, "return_to_hand_on_destroy", True, duration)
        elif et == "grant_resist":
            _me_register_duration(resolved, "resist", True, duration)
        elif et == "cannot_be_targeted":
            _me_register_duration(resolved, "cannot_be_targeted", True, duration)
        elif et == "must_be_destroyed_first":
            _me_register_duration(resolved, "must_be_destroyed_first", True, duration)
        elif et == "search_deck":
            _me_apply_search_deck(gs, owner, chooser)
        elif et == "draw_cards":
            _me_apply_draw(gs, owner, int(amount or 1))
        else:
            # Unknown (Wave B or beyond): ignore here.
            pass

    return prompts
