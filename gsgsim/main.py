# === MACHINE EFFECTS (Wave A) ==================================================
# Non-invasive runtime for machine-readable card effects. This block is designed
# to be standalone and safe: nothing else in the engine has to import from here.
# You can call `run_machine_effects(gs, source_goon, ability, chooser)` from the
# ability execution point to apply effects if `ability["effects"]` is present.
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
# Targets supported: self, any, ally_all, opponent_all, ally_all_except:self,
# all_opponent_playergoons, two_goons, three_goons, card_name:*, dead_pool
# Durations: instant, until_end_of_turn, next_turn, next_unwind, persistent
#
# NOTE: This block never prints; UI owns messaging. It mutates GameState and
# returns a list of "prompts" when user choices are needed (e.g., pick targets).
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple

from .engine import init_game
from .engine import select_ui


def _ensure_runtime_containers(gs):
    if not hasattr(gs, "statuses"):
        gs.statuses = {}  # board-wide statuses keyed by (scope, id)
    if not hasattr(gs, "marks"):
        gs.marks = {}  # e.g., {"marked": {target_card_id: source_card_id}}
    # Per-goon status dict is created on demand by _status_for(goon)


def _status_for(goon):
    if not hasattr(goon, "status"):
        goon.status = {}
    return goon.status


def _owner_of(gs, goon):
    if goon in gs.p1.board or goon in gs.p1.hand or goon in gs.p1.deck:
        return gs.p1
    return gs.p2


def _opponent_of(gs, player):
    return gs.p2 if player is gs.p1 else gs.p1


def _all_goons(gs) -> List[Any]:
    return list(gs.p1.board) + list(gs.p2.board)


def _filter_by_tokens(gs, source, tokens: List[str]) -> List[Any]:
    # Simple resolver for Wave A. When ambiguous (e.g., "any"), the caller/chooser must pick.
    out: List[Any] = []
    owner = _owner_of(gs, source)
    opp = _opponent_of(gs, owner)
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
            for g in _all_goons(gs):
                if getattr(g, "name", None) == name:
                    out.append(g)
        elif tok in ("any", "two_goons", "three_goons"):
            # chooser must handle these
            out.append(("CHOOSE", tok))
        else:
            # Unrecognized token: ignore (schema should prevent this)
            continue
    return out


def _register_duration(targets: Iterable[Any], tag: str, value: Any, duration: str):
    for t in targets:
        if isinstance(t, tuple):  # markers like ('dead_pool',) or ('CHOOSE', …)
            continue
        st = _status_for(t)
        arr = st.setdefault(tag, [])
        arr.append({"value": value, "duration": duration})


def _apply_alter_wind(gs, targets: List[Any], amount, chooser) -> None:
    # amount can be int or "X" (chooser must supply actual distribution for X)
    if amount == "X":
        # Expect chooser to return a dict {goon: int}
        dist = chooser("distribute_wind", targets)
        for goon, delta in dist.items():
            gs.rules.apply_wind(goon, delta)  # relies on your existing rules hook
    else:
        for t in targets:
            if isinstance(t, tuple):
                continue
            gs.rules.apply_wind(t, int(amount))


def _apply_destroy(gs, targets: List[Any]) -> None:
    for t in targets:
        if isinstance(t, tuple):
            continue
        gs.rules.destroy_goon(t)


def _apply_draw(gs, player, n: int):
    for _ in range(max(0, n)):
        if player.deck:
            player.hand.append(player.deck.pop())


def _apply_search_deck(gs, player, chooser):
    pick = chooser("search_deck", player.deck)
    if pick in player.deck:
        player.deck.remove(pick)
        player.hand.append(pick)
    # reshuffle
    rng = getattr(gs, "rng", None)
    (rng.shuffle(player.deck) if rng else player.deck.reverse())


def run_machine_effects(gs, source, ability: Dict[str, Any], chooser: Callable):
    """
    Apply all Wave A effects for a single ability. `chooser(kind, options)` is a
    callback the UI supplies to resolve 'any'/'two_goons'/'three_goons' and 'X'.
    Returns a list of prompts created during execution (UI may have already handled).
    """
    _ensure_runtime_containers(gs)
    effects = ability.get("effects") or []
    if not effects:
        return []
    owner = _owner_of(gs, source)
    prompts: List[Tuple[str, Any]] = []
    for eff in effects:
        et = eff.get("effect_type")
        tokens = eff.get("target") or []
        duration = eff.get("duration", "instant")
        amount = eff.get("amount", None)
        # Resolve targets
        resolved = _filter_by_tokens(gs, source, tokens)
        # Let chooser resolve any ambiguous tokens
        needs_choice = [t for t in resolved if isinstance(t, tuple) and t[0] == "CHOOSE"]
        if needs_choice:
            choice = chooser("choose_targets", {"source": source, "ability": ability, "need": [t[1] for t in needs_choice], "pool": _all_goons(gs)})
            # Replace markers with chosen goons
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
            _apply_alter_wind(gs, resolved, amount, chooser)
        elif et == "prevent_unwind":
            _register_duration(resolved, "prevent_unwind", True, duration)
        elif et == "disable_abilities":
            _register_duration(resolved, "disable_abilities", True, duration)
        elif et == "disable_contribution":
            _register_duration(resolved, "disable_contribution", True, duration)
        elif et == "destroy":
            _apply_destroy(gs, resolved)
        elif et == "retire_on_destroy":
            _register_duration(resolved, "retire_on_destroy", True, duration)
        elif et == "return_to_hand_on_destroy":
            _register_duration(resolved, "return_to_hand_on_destroy", True, duration)
        elif et == "grant_resist":
            _register_duration(resolved, "resist", True, duration)
        elif et == "cannot_be_targeted":
            _register_duration(resolved, "cannot_be_targeted", True, duration)
        elif et == "must_be_destroyed_first":
            _register_duration(resolved, "must_be_destroyed_first", True, duration)
        elif et == "search_deck":
            _apply_search_deck(gs, owner, chooser)
        elif et == "draw_cards":
            _apply_draw(gs, owner, int(amount or 1))
        else:
            # Unknown (Wave B or beyond): ignore here.
            pass
    return prompts


def main():
    """Launch the simulator with decks/UI configured from environment flags."""

    def _env_truthy(name: str) -> bool:
        val = os.environ.get(name)
        if val is None:
            return False
        return val.strip().lower() in {"1", "true", "on", "yes"}

    gs = init_game()

    ui_name = os.environ.get("GSG_UI") or "rich"
    ui = select_ui(ui_name)

    ai_flag = (os.environ.get("GSG_AI") or "").strip().lower()
    ai_p1 = ai_flag in {"p1", "both"}
    ai_p2 = ai_flag in {"p2", "both"}
    auto = _env_truthy("GSG_AUTO")

    ui.run_loop(gs, ai_p1=ai_p1, ai_p2=ai_p2, auto=auto)
    return gs
