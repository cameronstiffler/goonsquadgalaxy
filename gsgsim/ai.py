from __future__ import annotations

from typing import Any
from typing import List
from typing import Sequence


def _to_int(val: Any) -> int:
    if isinstance(val, str):
        text = val.strip().upper()
        if text == "X":
            return 0
    try:
        return int(val or 0)
    except Exception:
        return 0


def _get_end_of_turn():
    # Prefer a monkeypatched ai.end_of_turn (used by tests). If missing, return a no-op that just sets a flag.
    try:
        from . import ai as selfmod

        fn = getattr(selfmod, "end_of_turn", None)
        if callable(fn):
            return fn
    except Exception:
        pass

    def _noop(gs):
        setattr(gs, "ended", True)

    return _noop


def _opponent_of(gs, player):
    try:
        if player is getattr(gs, "p1", None):
            return getattr(gs, "p2", None)
        if player is getattr(gs, "p2", None):
            return getattr(gs, "p1", None)
    except Exception:
        pass
    return None


def _rank_value(card) -> int:
    rank = getattr(card, "rank", None)
    text = str(getattr(rank, "name", rank) or "").upper()
    if text == "SL":
        return 3
    if text in ("SG", "T", "TITAN"):
        return 2
    return 1


def _card_cost(card) -> int:
    return _to_int(getattr(card, "deploy_wind", 0))


def _sort_enemy(board: Sequence[Any]) -> List[Any]:
    def _key(card):
        wind = 0
        wind = _to_int(getattr(card, "wind", 0))
        return (wind, _rank_value(card), _card_cost(card))

    return sorted((c for c in board if c is not None), key=_key, reverse=True)


def _sort_friendly(board: Sequence[Any]) -> List[Any]:
    def _key(card):
        return _to_int(getattr(card, "wind", 0))

    return sorted((c for c in board if c is not None), key=_key, reverse=True)


def _ensure_count(cards: List[Any], count: int) -> List[Any]:
    if not cards or count <= 0:
        return []
    out = list(cards[:count])
    while len(out) < count:
        out.append(out[-1])
    return out


def _is_aggressive_effect(effect) -> bool:
    try:
        kind = str(effect.get("effect_type", "")).strip().lower()
    except AttributeError:
        return False
    if kind == "destroy":
        return True
    if kind in {"disable_abilities", "disable_contribution"}:
        return True
    if kind == "alter_wind":
        amt = effect.get("amount")
        return _to_int(amt) > 0
    return False


def _preset_targets(gs, me, ability, opponent) -> List[Any]:
    presets: List[Any] = []
    effects = getattr(ability, "effects", []) or []
    friendly_board = list(getattr(me, "board", []))
    enemy_board = list(getattr(opponent, "board", [])) if opponent else []

    for eff in effects:
        if not isinstance(eff, dict):
            continue
        tokens = eff.get("target") or []
        aggressive = _is_aggressive_effect(eff)
        for raw in tokens:
            tok = str(raw or "").strip().lower()
            if tok not in {"any", "two_goons", "three_goons"}:
                continue
            need = 1
            if tok == "two_goons":
                need = 2
            elif tok == "three_goons":
                need = 3

            if aggressive and enemy_board:
                picks = _ensure_count(_sort_enemy(enemy_board), need)
            else:
                picks = _ensure_count(_sort_friendly(friendly_board), need)
            presets.extend(picks)
    return presets


def _ability_aggression_score(ability) -> int:
    score = 0
    for eff in getattr(ability, "effects", []) or []:
        if not isinstance(eff, dict):
            continue
        kind = str(eff.get("effect_type", "")).strip().lower()
        if kind == "destroy":
            score += 50
        elif kind == "alter_wind":
            amt = eff.get("amount")
            delta = _to_int(amt)
            if delta > 0:
                score += 10 * delta
        elif kind in {"disable_abilities", "disable_contribution"}:
            score += 8
    return score


def _ability_wind_cost(ability) -> int:
    cost = getattr(ability, "cost", {}) or {}
    return _to_int(cost.get("wind", 0))


def _try_aggressive_ability(gs, me) -> bool:
    from .abilities import use_ability

    opponent = _opponent_of(gs, me)
    board = list(getattr(me, "board", []))
    actions: List[tuple[int, Any, Any, List[Any], int]] = []
    for bidx, card in enumerate(board):
        abilities = getattr(card, "abilities", []) or []
        for aidx, ability in enumerate(abilities):
            if getattr(ability, "passive", False):
                continue
            score = _ability_aggression_score(ability)
            if score <= 0:
                continue
            presets = _preset_targets(gs, me, ability, opponent)
            actions.append((score, card, aidx, presets, bidx))

    actions.sort(key=lambda item: item[0], reverse=True)

    for score, card, aidx, presets, _ in actions:
        if getattr(card, "new_this_turn", False):
            continue
        wind_cost = _ability_wind_cost(getattr(card, "abilities", [])[aidx])
        current_wind = _to_int(getattr(card, "wind", 0))
        if wind_cost > 0 and current_wind + wind_cost >= 4:
            # avoid self-retiring
            continue
        if use_ability(gs, card, aidx, presets):
            return True
    return False


def _deploy_best_available(gs, me) -> None:
    from .engine import deploy_from_hand

    try:
        from .engine import _check_deploy_requirements  # type: ignore
        from .engine import _has_squad_goon_duplicate  # type: ignore
    except Exception:
        _check_deploy_requirements = None  # type: ignore
        _has_squad_goon_duplicate = None  # type: ignore

    hand = list(getattr(me, "hand", []))
    if not hand:
        return

    playable: List[tuple[int, Any]] = []
    blocked: List[tuple[int, Any]] = []
    for original_idx, card in enumerate(hand):
        ok = True
        if callable(_check_deploy_requirements):
            try:
                ok, _ = _check_deploy_requirements(gs, me, card)  # type: ignore[arg-type]
            except Exception:
                ok = True

        if ok and callable(_has_squad_goon_duplicate):
            try:
                if _has_squad_goon_duplicate(me, card):  # type: ignore[arg-type]
                    ok = False
            except Exception:
                ok = False

        target = playable if ok else blocked
        target.append((original_idx, card))

    def _sort_key(pair):
        return (_card_cost(pair[1]), pair[0])

    playable.sort(key=_sort_key)
    blocked.sort(key=_sort_key)

    if not playable:
        return

    for original_idx, _card in playable:
        try:
            if deploy_from_hand(gs, me, original_idx):
                break
        except Exception:
            continue


def ai_take_turn(gs) -> None:
    from .abilities import REGISTRY
    from .abilities import use_ability

    me = getattr(gs, "turn_player", None)
    if me is None:
        setattr(gs, "ended", True)
        return

    _deploy_best_available(gs, me)

    if _try_aggressive_ability(gs, me):
        return _get_end_of_turn()(gs)

    # Fallback to prior behaviour: first zero-cost executable ability
    for card in getattr(me, "board", []):
        abilities = getattr(card, "abilities", []) or []
        if getattr(card, "new_this_turn", False):
            continue
        for aidx, ability in enumerate(abilities):
            cost = _to_int(getattr(ability, "cost", {}).get("wind", 0))
            if cost == 0:
                key = (getattr(card, "name", "").lower(), aidx)
                effects = getattr(ability, "effects", None) or []
                if effects or key in REGISTRY:
                    if use_ability(gs, card, aidx, None):
                        return _get_end_of_turn()(gs)

    return _get_end_of_turn()(gs)
