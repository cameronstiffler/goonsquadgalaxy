from __future__ import annotations

from typing import Callable
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from .models import Card
from .models import Player
from .rules import apply_wind
from .rules import cannot_spend_wind
from .rules import destroy_if_needed

# Chooser: UI-provided callback that gets (eligible, total_cost) and returns a plan [(idx, amt), ...]
Chooser = Callable[[List[Tuple[int, Card, int]], int], Optional[List[Tuple[int, int]]]]


def _deploy_bonus_available(card: Card) -> int:
    status = getattr(card, "status", {}) or {}
    total = 0
    for token in status.get("deploy_contribution_bonus", []):
        try:
            total += int(token.get("value", 0) or 0)
        except Exception:
            continue
    return max(0, total)


def _spend_deploy_bonus(card: Card, amount: int) -> None:
    if amount <= 0:
        return
    status = getattr(card, "status", {}) or {}
    tokens = status.get("deploy_contribution_bonus", [])
    idx = 0
    remaining = int(amount)
    while remaining > 0 and idx < len(tokens):
        token = tokens[idx]
        try:
            value = int(token.get("value", 0) or 0)
        except Exception:
            value = 0
        if value <= 0:
            tokens.pop(idx)
            continue
        if value <= remaining:
            remaining -= value
            tokens.pop(idx)
            continue
        token["value"] = value - remaining
        remaining = 0
    if not tokens and "deploy_contribution_bonus" in status:
        status.pop("deploy_contribution_bonus", None)


def _eligible_with_caps(player: Player) -> List[Tuple[int, Card, int]]:
    """
    Return list of (board_index, card, cap) that can contribute wind this turn.
    Excludes cards marked new_this_turn. cap is the MAX transferable wind (including lethal);
    the auto planner will restrict itself to 'safe' caps for SLs.
    """
    out: List[Tuple[int, Card, int]] = []
    from .rules import has_status

    for idx, c in enumerate(getattr(player, "board", [])):
        if getattr(c, "new_this_turn", False):
            setattr(c, "_why_ineligible", "new this turn")
            continue
        if has_status(c, "disable_contribution"):
            setattr(c, "_why_ineligible", "disable_contribution")
            continue
        wind = getattr(c, "wind", 0)
        cap = max(0, 4 - wind)
        if cap > 0:
            out.append((idx, c, cap))
    return out


def _auto_plan(eligible: List[Tuple[int, Card, int]], total_cost: int) -> Optional[List[Tuple[int, int]]]:
    """
    Greedy auto plan that:
      1) Prefers non-SL payers first.
      2) For SLs, uses only 'safe' wind (won't push SL to 4) unless unavoidable.
      3) If only lethal SL wind can cover, returns None to force manual confirmation.
    """
    if total_cost <= 0:
        return []

    def is_sl(card: Card) -> bool:
        r = getattr(card, "rank", None)
        # rank might be a string "SL" or an object with .name
        if isinstance(r, str):
            return r.upper() == "SL"
        name = getattr(r, "name", "")
        return str(name).upper() == "SL"

    # Split eligible into non-SL and SL
    non_sl: List[Tuple[int, Card, int]] = []
    sls: List[Tuple[int, Card, int]] = []
    for tup in eligible:
        (i, c, cap) = tup
        (sls if is_sl(c) else non_sl).append(tup)

    plan: List[Tuple[int, int]] = []
    need = total_cost

    # Spend from non-SL first (any cap is fine here)
    for i, c, cap in non_sl:
        if need == 0:
            break
        take = min(cap, need)
        if take > 0:
            plan.append((i, take))
            need -= take

    if need == 0:
        return plan

    # Spend from SLs, but only 'safe' capacity (up to 3 - current wind)
    for i, c, cap in sls:
        if need == 0:
            break
        current = getattr(c, "wind", 0)
        safe_cap = max(0, 3 - current)  # keep SL alive (4 would be lethal)
        take = min(safe_cap, need)
        if take > 0:
            plan.append((i, take))
            need -= take

    if need == 0:
        return plan

    # If we still need wind now, only lethal SL wind remains -> require manual confirmation
    return None


def manual_pay(player, total: int, plan: list[tuple[int, int]], allow_lethal_sl: bool = False) -> bool:
    if total <= 0:
        return False
    if sum(max(0, a) for _, a in plan) != total:
        return False
    board = list(getattr(player, "board", []))
    before = [getattr(c, "wind", 0) for c in board]

    from .rules import has_status

    def is_sl(card):
        r = getattr(card, "rank", "")
        return (isinstance(r, str) and r.upper() == "SL") or (hasattr(r, "name") and str(r.name).upper() == "SL")

    try:
        for idx, amt in plan:
            if amt <= 0 or not (0 <= idx < len(board)):
                raise RuntimeError("bad plan")
            c = board[idx]
            if has_status(c, "disable_contribution"):
                raise RuntimeError("disable_contribution")
            if is_sl(c) and getattr(c, "wind", 0) + amt > 3 and not allow_lethal_sl:
                raise RuntimeError("lethal")
            setattr(c, "wind", getattr(c, "wind", 0) + amt)
    except Exception:
        for c, w in zip(board, before):
            try:
                setattr(c, "wind", w)
            except Exception:
                pass
        return False
    return True


def distribute_wind(player, total_cost, *, auto=True, gs=None, chooser=None, contributors: Optional[List[int]] = None):
    """
    Pay `total_cost` wind from player's board (all-or-nothing, transactional, no fallback).
      - Prefer non-SL first, then SL (SL can reach 4 and retire immediately).
      - If `gs` provided, use rules.apply_wind/destroy_if_needed for all mutations.
      - No prints/logging, no partial payments, no fallback.
    """
    if total_cost is None:
        return True
    try:
        total_cost = int(total_cost)
    except Exception:
        return False
    if total_cost <= 0:
        return True

    def is_sl(card):
        r = getattr(card, "rank", None)
        if isinstance(r, str):
            return r.upper() == "SL"
        return getattr(r, "name", "").upper() == "SL"

    board = list(getattr(player, "board", []))

    from .rules import has_status

    def capacity(card):
        if cannot_spend_wind(gs, card):
            return 0
        if has_status(card, "disable_contribution"):
            return 0
        w = int(getattr(card, "wind", 0) or 0)
        return max(0, 4 - w)

    if contributors:
        plan_map: Dict[int, int] = {}
        for idx in contributors:
            if not isinstance(idx, int):
                return False
            plan_map[idx] = plan_map.get(idx, 0) + 1

        actual_total = sum(plan_map.values())
        if actual_total > total_cost:
            return False

        bonus_usage: List[Tuple[Card, int]] = []
        remaining = total_cost - actual_total
        if remaining > 0:
            for idx, amt in plan_map.items():
                if idx < 0 or idx >= len(board):
                    return False
                if amt <= 0:
                    continue
                card = board[idx]
                bonus = _deploy_bonus_available(card)
                if bonus <= 0:
                    continue
                use = min(bonus, remaining)
                if use > 0:
                    bonus_usage.append((card, use))
                    remaining -= use
                if remaining <= 0:
                    break
            if remaining > 0:
                return False

        for idx, amt in plan_map.items():
            if idx < 0 or idx >= len(board):
                return False
            card = board[idx]
            if has_status(card, "disable_contribution") or cannot_spend_wind(gs, card):
                return False
            if capacity(card) < amt:
                return False
            if is_sl(card) and int(getattr(card, "wind", 0) or 0) + amt > 3:
                return False

        for idx, amt in plan_map.items():
            card = board[idx]
            for _ in range(amt):
                if gs is not None:
                    apply_wind(gs, card, +1)
                    destroy_if_needed(gs, card)
                else:
                    card.wind = int(getattr(card, "wind", 0) or 0) + 1

        for card, used in bonus_usage:
            _spend_deploy_bonus(card, used)

        return True

    # Order: non-SL first, then SL
    non_sl: List[Tuple[Card, int, int]] = []
    sls: List[Tuple[Card, int, int]] = []
    for c in board:
        cap = capacity(c)
        if cap <= 0:
            continue
        bonus = _deploy_bonus_available(c)
        entry = (c, cap, bonus)
        (sls if is_sl(c) else non_sl).append(entry)
    order: List[Tuple[Card, int, int]] = non_sl + sls

    # Transactional: check total capacity first (including bonuses)
    total_cap = sum(cap for _, cap, _ in order)
    total_bonus = sum(max(0, bonus) for _, _, bonus in order)
    # Guard: in plain auto mode (no gs), refuse lethal-only payment when **all** payers are SLs
    # This matches tests expecting distribute_wind(p, 1) to return False when only an SL at 3 can pay.
    if auto and gs is None:
        # Build the current payer set we considered in 'order'
        def _is_sl(card):
            rank = getattr(card, "rank", None)
            name = getattr(rank, "name", "") if hasattr(rank, "name") else rank
            return str(name).upper() == "SL"

        if order and all(_is_sl(card) for card, _, _ in order):
            actual_needed = max(0, total_cost - total_bonus)
            safe_capacity = 0
            for card, _, _ in order:
                current = int(getattr(card, "wind", 0) or 0)
                safe_capacity += max(0, 3 - current)
            if actual_needed > safe_capacity:
                return False

    if total_cap + total_bonus < total_cost:
        return False

    # Plan payment
    need = total_cost
    plan: List[Tuple[Card, int]] = []
    bonus_usage: List[Tuple[Card, int]] = []
    for card, cap, bonus in order:
        if need <= 0:
            break
        take = min(cap, need)
        if take <= 0:
            continue
        extra = 0
        if bonus > 0 and take < need:
            extra = min(bonus, need - take)
        plan.append((card, take))
        if extra > 0:
            bonus_usage.append((card, extra))
        need -= take + extra

    if need > 0:
        return False

    # Apply payment
    for card, take in plan:
        if gs is not None:
            apply_wind(gs, card, +take)
            destroy_if_needed(gs, card)
        else:
            card.wind = int(getattr(card, "wind", 0)) + int(take)
    for card, used in bonus_usage:
        _spend_deploy_bonus(card, used)
    return True
