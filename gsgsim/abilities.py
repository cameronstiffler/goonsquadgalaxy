from __future__ import annotations

from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Tuple

from .effects import run_effects
from .rules import can_target_card

# Minimal, UI-agnostic registry so we can wire abilities one by one.

AbilityFn = Callable[[object, object], bool]  # (GameState, card) -> success

REGISTRY: Dict[Tuple[str, int], AbilityFn] = {}


def _owner_of(gs, card):
    try:
        if card in getattr(gs.p1, "board", []):
            return gs.p1
        if card in getattr(gs.p2, "board", []):
            return gs.p2
    except Exception:
        pass
    return None


def registers(name: str, idx: int):
    def deco(fn: AbilityFn):
        REGISTRY[(name.lower(), idx)] = fn
        return fn

    return deco


def use_ability(gs, card, idx: int, targets: list | None = None) -> bool:
    # Block active abilities on deploy turn
    if getattr(card, "new_this_turn", False):
        return False

    # Block if disable_abilities status is present
    from .rules import has_status

    if has_status(card, "disable_abilities"):
        # Optionally print a message if UI expects it
        return False

    abilities = getattr(card, "abilities", [])
    try:
        ability = abilities[idx]
    except Exception:
        return False

    # Passive abilities cannot be actively used
    if getattr(ability, "passive", False):
        return False

    if targets is None:
        targets = []

    source_owner = _owner_of(gs, card)
    filtered_targets: List[Any] = []
    for target in targets:
        if target is None:
            continue
        if isinstance(target, tuple):
            filtered_targets.append(target)
            continue
        target_owner = _owner_of(gs, target)
        hostile = bool(source_owner and target_owner and target_owner is not source_owner)
        if not can_target_card(gs, card, target, hostile=hostile):
            return False
        filtered_targets.append(target)
    targets = filtered_targets

    _trigger_target_reactions(gs, card, targets)

    if getattr(card, "ability_used_this_turn", False) and not getattr(ability, "passive", False):
        return False

    # Determine if we have a registry handler up front
    key = (getattr(card, "name", "").lower(), idx)
    fn = REGISTRY.get(key)

    # If neither effects nor handler exist, fail before charging cost
    effects = getattr(ability, "effects", None) or []
    has_exec = bool(effects) or bool(fn)
    if not has_exec:
        return False

    # Compute wind cost and pay before running handler/effects
    cost = getattr(ability, "cost", {}) or {}
    wind_raw = cost.get("wind", 0)
    try:
        wind_cost = int(wind_raw or 0)
    except Exception:
        if isinstance(wind_raw, str) and wind_raw.strip().upper() == "X":
            wind_cost = 0
        else:
            wind_cost = 0

    def _all_marked_same_target() -> bool:
        if not targets:
            return False
        actual = [t for t in targets if t is not None and not isinstance(t, tuple)]
        if not actual:
            return False
        first = actual[0]
        if any(t is not first for t in actual):
            return False
        from .rules import has_status

        return has_status(first, "marked")

    def _has_cost_waiver() -> bool:
        if not targets:
            return False
        if not _all_marked_same_target():
            return False
        allowed_names = set()
        first = targets[0]
        status = getattr(first, "status", {}) if first is not None else {}
        for token in status.get("mark_cost_reduction", []):
            try:
                for name in token.get("value", []):
                    allowed_names.add(name.lower())
            except Exception:
                if isinstance(token, list):
                    allowed_names.update(n.lower() for n in token)
                elif isinstance(token, str):
                    allowed_names.add(token.lower())
        return getattr(card, "name", "").lower() in allowed_names

    if _has_cost_waiver():
        wind_cost = 0

    def _pay_wind_cost(gs, source_card, total: int) -> bool:
        if total <= 0:
            return True

        from .engine import add_wind_and_check
        from .rules import cannot_spend_wind

        board = list(getattr(gs.turn_player, "board", [])) if getattr(gs, "turn_player", None) else []

        contributors: List[Any] = []

        status_map = getattr(source_card, "status", {}) or {}
        for token in status_map.get("enable_contribution", []):
            contributor = token.get("value")
            if contributor and contributor not in contributors and contributor in board:
                contributors.append(contributor)

        if source_card not in contributors:
            contributors.append(source_card)

        payments: List[Tuple[Any, int]] = []
        remaining = int(total)

        for contributor in contributors:
            if remaining <= 0:
                break
            if has_status(contributor, "disable_contribution"):
                continue
            if cannot_spend_wind(gs, contributor):
                continue
            current = int(getattr(contributor, "wind", 0) or 0)
            capacity = max(0, 4 - current)
            if capacity <= 0:
                continue
            pay = min(capacity, remaining)
            if pay <= 0:
                continue
            add_wind_and_check(gs, contributor, pay, hostile=False)
            payments.append((contributor, pay))
            remaining -= pay

        if remaining > 0:
            for contributor, pay in payments:
                add_wind_and_check(gs, contributor, -pay, hostile=False)
            return False
        return True

    if wind_cost > 0:
        if not _pay_wind_cost(gs, card, wind_cost):
            return False

    # If ability.effects is present, call interpreter
    repeat = False
    if targets:
        try:
            from .rules import has_status

            repeat = any(has_status(t, "double_use_against") for t in targets if t is not None)
        except Exception:
            repeat = False

    if effects:

        def _is_machine_spec(spec):
            if hasattr(spec, "effect_type"):
                return True
            if isinstance(spec, dict) and "effect_type" in spec:
                return True
            return False

        success = False
        if any(_is_machine_spec(eff) for eff in effects):
            from .engine import run_machine_effects
            from .ui.rich_ui import _ui_chooser_for_interpreter

            run_machine_effects(
                gs,
                card,
                ability,
                chooser=_ui_chooser_for_interpreter(gs),
                preset_targets=targets,
            )
            if repeat:
                run_machine_effects(
                    gs,
                    card,
                    ability,
                    chooser=_ui_chooser_for_interpreter(gs),
                    preset_targets=targets,
                )
            success = True
        else:
            success = bool(run_effects(gs, card, targets, effects))
    else:
        success = bool(fn(gs, card, targets))
        if success and repeat:
            success = bool(fn(gs, card, targets)) or success

    if success:
        used = set(getattr(card, "_abilities_used_this_turn", set()))
        used.add(idx)
        card._abilities_used_this_turn = used
        setattr(card, "ability_used_this_turn", True)
    return success


def _apply_machine_effects(gs, card, targets, effects):
    if not targets:
        return False

    from .engine import run_machine_effects

    def _chooser(kind, options):
        if kind == "choose_targets":
            return targets
        return []

    run_machine_effects(gs, card, {"effects": effects}, chooser=_chooser, preset_targets=targets)
    return True


def _mark_target(gs, card):
    # Placeholder: toggle a "marked" flag so tests/UI can verify
    setattr(card, "marked", True)
    return True


def _trigger_target_reactions(gs, card, targets):
    if not targets:
        return
    owner = _owner_of(gs, card)
    for target in targets:
        if target is None or isinstance(target, tuple):
            continue
        status_map = getattr(target, "status", {}) or {}
        reactions = status_map.get("hostile_target_reaction", [])
        if not reactions:
            continue
        target_owner = _owner_of(gs, target)
        hostile = bool(owner and target_owner and owner is not target_owner)
        for token in reactions:
            value = token.get("value", {}) or {}
            amount = int(value.get("amount", 0) or 0)
            if amount == 0:
                continue
            if value.get("hostile_only") and not hostile:
                continue
            from .engine import trigger_hostile_target_reaction

            trigger_hostile_target_reaction(gs, card, target, amount)


@registers("Hover Shield", 0)
def _cover(gs, card, targets):
    # Placeholder: toggle "covered" for a simple status effect
    setattr(card, "covered", True)
    return True


@registers("Sentry Node", 0)
def _autoburst(gs, card, targets):
    # Placeholder: no-op success (damage resolution not implemented here)
    return True


@registers("Sentry Node", 1)
def _lead_laser(gs, card, targets):
    # Placeholder
    return True


@registers("Sausage Droid", 0)
def _process(gs, card, targets):
    # Placeholder: no-op success
    return True


@registers("Annihilist Overseer", 0)
def _annihilist_blind(gs, card, targets):
    effects = [
        {"effect_type": "disable_abilities", "target": ["any"], "duration": "next_turn"},
        {"effect_type": "disable_contribution", "target": ["any"], "duration": "next_turn"},
    ]
    return _apply_machine_effects(gs, card, targets, effects)


@registers("Annihilist Overseer", 1)
def _annihilist_behead(gs, card, targets):
    effects = [
        {"effect_type": "destroy", "target": ["any"], "duration": "instant"},
    ]
    return _apply_machine_effects(gs, card, targets, effects)


@registers("Lokar Simmons", 0)
def _lokar_resourceful(gs, card, targets):
    # define effect here (e.g., draw a card, mark, buff, etc.)
    return True


@registers("Lokar Simmons", 0)
def _diag_lokar_simmons_0(gs, card, targets):
    # DIAGNOSTIC ONLY: succeed to verify cost/flow; replace with real effect later
    try:
        print(
            "[diag] RESOURCEFUL fired; targets=",
            [getattr(t, "name", None) for t in (targets or [])],
        )
    except Exception:
        pass
    return True


@registers("Grim", 0)
def _diag_grim_0(gs, card, targets):
    # DIAGNOSTIC ONLY: cost 0, immediate success
    try:
        print(
            "[diag] DEFY NATURE fired; targets=",
            [getattr(t, "name", None) for t in (targets or [])],
        )
    except Exception:
        pass
    return True


@registers("Target Marker Probe", 0)
def _tmp_mark(gs, card, targets):

    return bool(run_effects(gs, card, targets, [{"op": "mark"}]))
