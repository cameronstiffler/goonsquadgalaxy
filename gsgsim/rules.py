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
        # If any ally on the defending side must_be_destroyed_first, only those may be targeted
        side = getattr(gs, "p1", None) if target in getattr(getattr(gs, "p1", None), "board", []) else getattr(gs, "p2", None)
        if side:
            must_first = [c for c in getattr(side, "board", []) if has_status(c, "must_be_destroyed_first")]
            if must_first and target not in must_first:
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
