# --- Status helpers for Wave-A effects ---
from __future__ import annotations


def has_status(goon, tag):
    """Return True if goon.status[tag] exists and is nonempty."""
    arr = getattr(goon, "status", {}).get(tag, [])
    return bool(arr)


def consume_status(goon, tag, predicate=lambda t: True):
    """Remove the first status token matching predicate from goon.status[tag]."""
    arr = getattr(goon, "status", {}).get(tag, [])
    for i, t in enumerate(arr):
        if predicate(t):
            arr.pop(i)
            break
    if not arr:
        getattr(goon, "status", {}).pop(tag, None)


# Single source of truth for rule helpers.
# NO imports from engine/UI/payments here.


def apply_wind(gs, card, delta: int) -> int:
    """Adjust card.wind by delta (can be + or -). Returns the applied delta."""
    try:
        w0 = int(getattr(card, "wind", 0) or 0)
    except Exception:
        w0 = 0
    d = int(delta or 0)
    w1 = w0 + d
    if w1 < 0:
        w1 = 0
        d = -w0
    setattr(card, "wind", w1)
    if gs is not None:
        destroy_if_needed(gs, card)
    return d


def apply_wind_with_resist(gs, card, delta: int, *, hostile: bool) -> int:
    """Hostile +1 is reduced by 1 if the card has resist; otherwise apply as-is."""
    d = int(delta or 0)
    if hostile and d > 0:
        has_resist = bool(getattr(card, "resist", False) or "resist" in getattr(card, "traits", set()) or "resist" in getattr(card, "icons", []))
        if has_resist and d == 1:
            d = 0
    return apply_wind(gs, card, d)


def destroy_if_needed(gs, card) -> bool:
    """Retire card at wind >= 4; push to dead_pool; return True if destroyed."""
    try:
        if int(getattr(card, "wind", 0) or 0) < 4:
            return False
    except Exception:
        return False

    owner = None
    for side in (getattr(gs, "p1", None), getattr(gs, "p2", None)):
        if side and card in getattr(side, "board", []):
            owner = side
            break
    if owner:
        # Remove all instances of card from board
        while card in owner.board:
            owner.board.remove(card)
        try:
            owner.retired.append(card)
        except Exception:
            pass

    if not hasattr(gs, "dead_pool") or gs.dead_pool is None:
        gs.dead_pool = []
    gs.dead_pool.append(card)
    try:
        if getattr(card, "biological", False):
            gs.dead_pool_bio = int(getattr(gs, "dead_pool_bio", 0) or 0) + 1
        if getattr(card, "mechanical", False):
            gs.dead_pool_mech = int(getattr(gs, "dead_pool_mech", 0) or 0) + 1
    except Exception:
        pass

    dependents = []
    try:
        status_map = getattr(card, "status", {}) or {}
        for token in status_map.get("destroy_dependents", []):
            dep = token.get("value")
            if dep and any(dep in getattr(player, "board", []) for player in (getattr(gs, "p1", None), getattr(gs, "p2", None))):
                dependents.append(dep)
    except Exception:
        pass

    attacker = getattr(card, "_destroyed_by", None)
    if hasattr(card, "_destroyed_by"):
        try:
            delattr(card, "_destroyed_by")
        except Exception:
            pass
    retaliate = bool(getattr(card, "_retaliate_on_destroy", False))
    if retaliate and attacker and attacker is not card:
        try:
            destroy_if_needed(gs, attacker)
        except Exception:
            pass

    try:
        from .engine import refresh_board_state  # avoid circular at module load

        refresh_board_state(gs)
    except Exception:
        pass

    for dep in dependents:
        try:
            destroy_if_needed(gs, dep)
        except Exception:
            pass

    try:
        for player in (getattr(gs, "p1", None), getattr(gs, "p2", None)):
            if not player:
                continue
            for other in list(getattr(player, "board", [])):
                status_map = getattr(other, "status", {}) or {}
                for tag, arr in list(status_map.items()):
                    filtered = [token for token in arr if token.get("value") is not card]
                    status_map[tag] = filtered
    except Exception:
        pass

    # If the destroyed card is an SL, mark loser on GameState
    try:
        r = getattr(card, "rank", "")
        r_name = getattr(getattr(card, "rank", None), "name", "")
        is_sl = str(r).strip().upper() in ("SL", "SQUAD LEADER") or str(r_name).strip().upper() == "SL"
        if is_sl and getattr(gs, "loser", None) is None:
            loser = "P1" if owner is getattr(gs, "p1", None) else "P2"
            gs.loser = loser
    except Exception:
        pass
    return True


def cannot_spend_wind(gs, card) -> bool:
    """Lock rule: newly deployed or explicit cannot_spend_wind status."""
    return bool(getattr(card, "just_deployed", False) or getattr(card, "new_this_turn", False) or getattr(card, "cannot_spend_wind", False))


def _rank_str(x) -> str:
    r = getattr(x, "rank", "")
    if isinstance(r, str):
        return r.upper()
    return str(getattr(r, "name", "")).upper()


def can_target_card(gs, source, target, *, hostile: bool) -> bool:
    """Hostile target gating: SL protected by BG/SG; Titans cannot be hostile-targeted."""
    # Wave‑A: cannot_be_targeted / must_be_destroyed_first
    if hostile:
        # cannot_be_targeted blocks hostile targeting entirely
        if has_status(target, "cannot_be_targeted"):
            return False
        try:
            owner = None
            if target in getattr(gs.p1, "board", []):
                owner = gs.p1
            elif target in getattr(gs.p2, "board", []):
                owner = gs.p2
            if owner is not None and getattr(owner, "_shield_array_protection", False):
                return False
        except Exception:
            pass
        # If any ally on the defending side must_be_destroyed_first, only those may be targeted
        side = getattr(gs, "p1", None) if target in getattr(getattr(gs, "p1", None), "board", []) else getattr(gs, "p2", None)
        if side:
            must_first = [c for c in getattr(side, "board", []) if has_status(c, "must_be_destroyed_first")]
            if must_first and target not in must_first:
                return False
        if has_status(target, "cannot_be_targeted_negative"):
            return False
    tr = _rank_str(target)
    if tr in ("T", "TITAN"):
        return False
    is_sl = tr in ("SL", "SQUAD LEADER")
    if is_sl:
        side = getattr(gs, "p1", None) if target in getattr(getattr(gs, "p1", None), "board", []) else getattr(gs, "p2", None)
        if side:
            for c in getattr(side, "board", []):
                if c is target:
                    continue
                rr = _rank_str(c)
                if rr in ("BG", "BASIC GOON", "SG", "SQUAD GOON"):
                    return False
    return True
