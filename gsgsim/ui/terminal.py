from __future__ import annotations

import re
import webbrowser
from typing import List

from ..models import Card
from ..models import GameState
from ..models import Rank

WHITE = "\033[37m"
CYAN = "\033[36m"
GREY = "\033[90m"
RED = "\033[31m"
YELLOW = "\033[33m"
RST = "\033[0m"

try:
    from .. import engine_rule_shim
except Exception:
    engine_rule_shim = None


def rank_icon(c):
    r = getattr(c, "rank", None)
    return " ⭐" if r == Rank.SL else " 🔶" if r == Rank.SG else " 💪" if r == Rank.TITAN else ""


def prop_icons(c):
    props = getattr(c, "properties", {}) or {}
    out = []
    if props.get("resist"):
        out.append(" ✋")
    if props.get("no_unwind"):
        out.append(" 🚫")
    return "".join(out)


def cost_str(c):
    w = int(getattr(c, "deploy_wind", 0) or 0)
    g = int(getattr(c, "deploy_gear", 0) or 0)
    m = int(getattr(c, "deploy_meat", 0) or 0)
    return f"{WHITE}{w}{RST}{CYAN}⟲{RST} {WHITE}{g}{RST}{GREY}⛭{RST} {WHITE}{m}{RST}{RED}⚈{RST}"


def ability_lines(c: Card) -> List[str]:
    out = []
    for j, a in enumerate(getattr(c, "abilities", []) or []):
        nm = getattr(a, "name", "ABILITY")
        w = int(getattr(a, "wind_cost", 0) or 0)
        g = int(getattr(a, "gear_cost", 0) or 0)
        m = int(getattr(a, "meat_cost", 0) or 0)
        cost = f"{WHITE}{w}{RST}{CYAN}⟲{RST} {WHITE}{g}{RST}{GREY}⛭{RST} {WHITE}{m}{RST}{RED}⚈{RST}" if any((w, g, m)) else ""
        desc = getattr(a, "text", None) or ""
        line = f"{j}: {nm}"
        if cost:
            line += f"  {cost}"
        if desc:
            line += f"  — {desc}"
        out.append(line)
    return out or ["-"]


def name_with_icons(c):
    new_flag = getattr(c, "new_this_turn", False) or getattr(c, "new_in_hand", False)
    return f"{c.name}{rank_icon(c)}{prop_icons(c)}{YELLOW+' ✨'+RST if new_flag else ''}"


class TerminalUI:
    def __init__(self) -> None:
        self._last_turn_summary: str | None = None

    def render(self, gs: GameState):
        print("\033[2J\033[H", end="")
        if self._last_turn_summary:
            print("Previous turn:")
            print(self._last_turn_summary)
        for label, p in [(f"Board {gs.p1.name}", gs.p1), (f"Board {gs.p2.name}", gs.p2)]:
            print(label)
            for i, c in enumerate(p.board):
                lines = ability_lines(c)
                print(f"[{i}] {name_with_icons(c)} | wind={getattr(c, 'wind', 0)} | {lines[0]}")
                for extra in lines[1:]:
                    print(f"    ↳ {extra}")
        print(f"\n{gs.turn_player.name} hand:")
        for i, c in enumerate(gs.turn_player.hand):
            print(f"[{i}] {name_with_icons(c):<20} {cost_str(c)}")

    def _show_card_url(self, card, label: str) -> None:
        if card is None:
            print(f"{label} not found.")
            return
        url = getattr(card, "image_url_full", None)
        if url:
            print(f"{label}: {url}")
            try:
                webbrowser.open(url)
            except Exception as exc:
                print(f"Could not open browser: {exc}")
        else:
            print(f"{label} has no image URL.")

    def _turn_summary(self, gs: GameState, ended_player) -> None:
        turn_number = max(0, getattr(gs, "turn_number", 1) - 1)
        ended_name = getattr(ended_player, "name", "?")
        p1_board = len(getattr(gs.p1, "board", []))
        p2_board = len(getattr(gs.p2, "board", []))
        p1_hand = len(getattr(gs.p1, "hand", []))
        p2_hand = len(getattr(gs.p2, "hand", []))
        p1_retired = len(getattr(gs.p1, "retired", []))
        p2_retired = len(getattr(gs.p2, "retired", []))
        dead_pool = len(getattr(gs, "dead_pool", []) or [])
        summary = f"Turn {turn_number} ends ({ended_name}). " f"P1 board:{p1_board} hand:{p1_hand} retired:{p1_retired}; " f"P2 board:{p2_board} hand:{p2_hand} retired:{p2_retired}; Dead:{dead_pool}"
        print(summary)
        self._last_turn_summary = summary

    def _game_summary(self, gs: GameState) -> None:
        loser = getattr(gs, "loser", None)
        if loser == "P1":
            winner = "P2"
        elif loser == "P2":
            winner = "P1"
        else:
            winner = "Unknown"
        total_turns = max(0, getattr(gs, "turn_number", 1) - 1)
        p1_retired = len(getattr(gs.p1, "retired", []))
        p2_retired = len(getattr(gs.p2, "retired", []))
        dead_pool = len(getattr(gs, "dead_pool", []) or [])
        print(f"Game over! Winner: {winner} after {total_turns} turns. " f"P1 retired {p1_retired}; P2 retired {p2_retired}; Dead Pool {dead_pool}.")
        self._last_turn_summary = None

    def run_loop(self, gs: GameState):
        from ..engine import deploy_from_hand
        from ..engine import end_of_turn

        while True:
            if engine_rule_shim and engine_rule_shim.check_sl_loss(gs):
                self._game_summary(gs)
                break
            self.render(gs)
            try:
                line = input("> ").strip()
            except Exception:
                break
            if line in ("quit", "q"):
                break
            if line in ("end", "e"):
                ended_player = gs.turn_player
                end_of_turn(gs)
                self._turn_summary(gs, ended_player)
                continue
            m = re.fullmatch(r"vh\[(\d+)\]", line)
            if m:
                idx = int(m.group(1))
                hand = getattr(gs.turn_player, "hand", [])
                card = hand[idx] if 0 <= idx < len(hand) else None
                self._show_card_url(card, f"Hand card {idx}")
                continue
            m = re.fullmatch(r"vb\[(\d+)\]", line)
            if m:
                idx = int(m.group(1))
                board = getattr(gs.turn_player, "board", [])
                card = board[idx] if 0 <= idx < len(board) else None
                self._show_card_url(card, f"Board card {idx}")
                continue
            m = re.fullmatch(r"vo\[(\d+)\]", line)
            if m:
                idx = int(m.group(1))
                opponent = gs.p2 if gs.turn_player is gs.p1 else gs.p1
                board = getattr(opponent, "board", [])
                card = board[idx] if 0 <= idx < len(board) else None
                self._show_card_url(card, f"Opponent card {idx}")
                continue
            m = re.fullmatch(r"d(\d+)", line)
            if m:
                idx = int(m.group(1))
                player = gs.turn_player
                card = player.hand[idx]
                wind_cost = _to_int(getattr(card, "deploy_wind", 0))
                pre_state = _collect_board_state(player)
                pre_retired = {id(c) for c in getattr(player, "retired", [])}
                if _auto_deploy_would_retire_sl(gs, player, wind_cost):
                    print("Auto deploy cancelled: paying wind would retire your Squad Leader. Specify contributors manually.")
                    ok = False
                else:
                    ok = deploy_from_hand(gs, player, idx)
                if ok:
                    card = player.board[-1] if player.board else None
                    name = getattr(card, "name", f"#{idx}") if card else f"#{idx}"
                    print(f"Deployed {name} using automatic wind assignment.")
                    for msg in _describe_payment_changes(player, pre_state, pre_retired):
                        print(f"  {msg}")
                else:
                    print("Deploy failed: cannot pay / illegal")
                continue
            m = re.fullmatch(r"dd(\d+)", line)
            if m:
                idx = int(m.group(1))
                player = gs.turn_player
                card = player.hand[idx]
                wind_cost = _to_int(getattr(card, "deploy_wind", 0))
                pre_state = _collect_board_state(player)
                pre_retired = {id(c) for c in getattr(player, "retired", [])}
                if _auto_deploy_would_retire_sl(gs, player, wind_cost):
                    print("Auto deploy cancelled: paying wind would retire your Squad Leader. Specify contributors manually.")
                    ok = False
                else:
                    ok = deploy_from_hand(gs, player, idx)
                if ok:
                    card = player.board[-1] if player.board else None
                    name = getattr(card, "name", f"#{idx}") if card else f"#{idx}"
                    print(f"Double-deployed {name} using automatic wind assignment.")
                    for msg in _describe_payment_changes(player, pre_state, pre_retired):
                        print(f"  {msg}")
                else:
                    print("Deploy failed: cannot pay / illegal")
                continue


def _to_int(val):
    try:
        if isinstance(val, str):
            return int(val.strip())
        return int(val or 0)
    except Exception:
        return 0


def _is_squad_leader(card) -> bool:
    rank = getattr(card, "rank", None)
    if isinstance(rank, Rank):
        return rank.name.upper() == "SL"
    if isinstance(rank, str):
        return rank.strip().upper() == "SL"
    return False


def _auto_deploy_would_retire_sl(gs, player, wind_cost: int) -> bool:
    if wind_cost <= 0:
        return False
    from ..rules import cannot_spend_wind
    from ..rules import has_status

    other_capacity = 0
    sl_capacity = 0
    sl_wind = None

    for card in getattr(player, "board", []):
        if has_status(card, "disable_contribution"):
            continue
        if cannot_spend_wind(gs, card):
            continue
        wind = _to_int(getattr(card, "wind", 0))
        capacity = max(0, 4 - wind)
        if capacity <= 0:
            continue
        if _is_squad_leader(card):
            sl_capacity = capacity
            sl_wind = wind
        else:
            other_capacity += capacity

    if sl_wind is None:
        return False

    remaining = wind_cost - other_capacity
    if remaining <= 0:
        return False
    if remaining > sl_capacity:
        return True
    if sl_wind + remaining >= 4:
        return True
    return False


def _collect_board_state(player):
    state = {}
    for card in getattr(player, "board", []):
        state[id(card)] = {
            "card": card,
            "name": getattr(card, "name", "Goon"),
            "wind": _to_int(getattr(card, "wind", 0)),
        }
    return state


def _describe_payment_changes(player, pre_state, pre_retired_ids):
    msgs = []
    post_board_ids = {id(c) for c in getattr(player, "board", [])}
    post_retired_ids = {id(c) for c in getattr(player, "retired", [])}
    for cid, info in pre_state.items():
        card = info["card"]
        if cid not in post_board_ids and cid not in post_retired_ids:
            continue
        new_wind = _to_int(getattr(card, "wind", 0))
        delta = new_wind - info["wind"]
        if delta <= 0:
            continue
        if cid in post_retired_ids and cid not in pre_retired_ids:
            msgs.append(f"{info['name']} paid {delta} wind and retired (reached {new_wind}).")
        else:
            msgs.append(f"{info['name']} paid {delta} wind (now at {new_wind}).")
    return msgs
