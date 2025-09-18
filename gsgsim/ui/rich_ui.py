from __future__ import annotations

from rich.console import Console
from rich.table import Table

from ..models import Card
from ..models import GameState

try:
    from .. import engine_rule_shim
except Exception:
    engine_rule_shim = None


# --- Wave-A interpreter UI chooser integration ---
def _ui_chooser_for_interpreter(gs):
    def chooser(kind, options):
        if kind == "choose_targets":
            return select_targets_via_existing_ui(gs, options)
        if kind == "distribute_wind":
            return distribute_wind_via_ui(gs, options)
        if kind == "search_deck":
            return pick_from_deck_via_ui(gs, options)
        return []

    return chooser


def select_targets_via_existing_ui(gs, options):
    # Minimal: just return the first N from pool for now
    pool = options.get("pool", [])
    need = options.get("need", ["any"])
    n = 1
    if need and need[0].startswith("two"):
        n = 2
    if need and need[0].startswith("three"):
        n = 3
    return pool[:n]


def distribute_wind_via_ui(gs, options):
    # Minimal: distribute 1 to each until total is met
    pool = options.get("pool", [])
    total = options.get("total", 1)
    out = {}
    for goon in pool:
        if total <= 0:
            break
        out[goon] = 1
        total -= 1
    return out


def pick_from_deck_via_ui(gs, options):
    # Minimal: pick the first card
    deck = options if isinstance(options, list) else options.get("deck", [])
    return deck[0] if deck else None


# ---------- icon/name helpers ----------


def _safe_str(x) -> str:
    try:
        return str(x)
    except Exception:
        return "?"


def _is_true(obj, *keys) -> bool:
    for k in keys:
        try:
            v = getattr(obj, k, None)
            if isinstance(v, bool):
                if v:
                    return True
            elif isinstance(v, (int, str)):
                if str(v).strip().lower() in ("1", "true", "yes", "y", "on"):
                    return True
            elif v:
                return True
        except Exception:
            pass
    return False


def _icons_has(card: Card, token: str) -> bool:
    try:
        icons = getattr(card, "icons", None) or []
        return token.lower() in {str(x).strip().lower() for x in icons}
    except Exception:
        return False


def _rank_icon_for_name(card: Card) -> str:
    r = getattr(card, "rank", None)
    tag = None
    if isinstance(r, str):
        tag = r.strip().upper()
    elif hasattr(r, "name"):
        tag = _safe_str(getattr(r, "name", "")).strip().upper()
    if tag == "SL":
        return "⭐"
    if tag == "SG":
        return "🔶"
    if tag == "T":
        return "Ω"
    return ""


def _bio_mech_icon(card: Card) -> str:
    out = []
    if _is_true(card, "biological", "is_bio", "bio") or _icons_has(card, "biological"):
        out.append("🥩")
    if _is_true(card, "mechanical", "is_mech", "mech") or _icons_has(card, "mechanical"):
        out.append("⚙️")
    return "".join(out)


def _resist_icon(card: Card) -> str:
    return "✋" if _is_true(card, "resist", "has_resist") or _icons_has(card, "resist") else ""


def _no_unwind_icon(card: Card) -> str:
    return "🚫" if _is_true(card, "no_unwind") or _icons_has(card, "no_unwind") else ""


def _resolve_faction(player, card) -> str:
    for obj, attr in ((player, "faction"), (player, "name"), (card, "faction")):
        try:
            v = getattr(obj, attr, None)
            if v:
                vu = _safe_str(v).upper()
                if "NARC" in vu:
                    return "NARC"
                if "PCU" in vu:
                    return "PCU"
        except Exception:
            pass
    if _icons_has(card, "narc"):
        return "NARC"
    if _icons_has(card, "pcu"):
        return "PCU"
    return ""


def _faction_icon(f: str) -> str:
    fu = (f or "").upper()
    if fu == "NARC":
        return "🚨"
    if fu == "PCU":
        return "🌀"
    return ""


def _locked_icon(card: Card) -> str:
    if _is_true(card, "just_deployed"):
        return "🔒"
    try:
        if getattr(card, "turns_in_play", None) == 0:
            return "🔒"
    except Exception:
        pass
    return ""


def _name_with_icons(card: Card, faction_str: str) -> str:
    name = _safe_str(getattr(card, "name", "?"))
    pieces = [
        _rank_icon_for_name(card),
        _resist_icon(card),
        _no_unwind_icon(card),
        _bio_mech_icon(card),
        _faction_icon(faction_str),
        _locked_icon(card),
    ]
    return f"{name}{''.join(p for p in pieces if p)}"


def cost_str(card: Card) -> str:
    try:
        w = int(getattr(card, "deploy_wind", 0) or 0)
        g = int(getattr(card, "deploy_gear", 0) or 0)
        m = int(getattr(card, "deploy_meat", 0) or 0)
    except Exception:
        w = g = m = 0
    return f"{w}⟲ {g}⛭ {m}⚈"


def _cost_block(obj) -> str:
    """Return cost in 'N⟲ N⛭ N⚈' for either an ability or a card."""
    try:
        cost = getattr(obj, "cost", None)
        if isinstance(cost, dict):
            w = int(cost.get("wind", 0) or 0)
            g = int(cost.get("gear", 0) or 0)
            m = int(cost.get("meat", 0) or 0)
            return f"{w}⟲ {g}⛭ {m}⚈"
    except Exception:
        pass
    w = int(getattr(obj, "deploy_wind", 0) or 0)
    g = int(getattr(obj, "deploy_gear", 0) or 0)
    m = int(getattr(obj, "deploy_meat", 0) or 0)
    return f"{w}⟲ {g}⛭ {m}⚈"


def _abilities_block(card: "Card") -> str:
    """Format abilities as lines: [index] [name][cost N⟲ N⛭ N⚈] [text]"""
    out = []
    for i, a in enumerate(getattr(card, "abilities", []) or []):
        name = _safe_str(getattr(a, "name", "ABILITY"))
        text = _safe_str(getattr(a, "text", "") or "")
        cost_txt = _cost_block(a)
        line = f"{i} {name}[{cost_txt}]"
        if text:
            line += f" {text}"
        out.append(line)
    return "\n".join(out) if out else "-"


# ---------- Rich UI ----------


def _parse_d_cmd(tok: str):
    t = (tok or "").strip()
    if t.startswith("dd") and t[2:].isdigit():
        return ("dd", int(t[2:]))
    if t.startswith("d") and len(t) > 1 and t[1:].isdigit():
        return ("d", int(t[1:]))
    return (None, None)


class RichUI:
    def __init__(self) -> None:
        self.console = Console()

    def _print_command_banner(self, cheats_enabled: bool = False) -> None:
        banner = (
            "[bold cyan]Goon Squad Galaxy Simulator[/bold cyan]\n"
            "Commands: [bold]help[/bold] | [bold]quit[/bold](q) | [bold]end[/bold](e) | [bold]dN[/bold]|[bold]d N[/bold] | [bold]ddN[/bold]|[bold]dd N[/bold] | [bold]u <src> <abil>[/bold] | [bold]pay <amount> p1|p2:idx[,idx][/bold] | [bold]ai[/bold] [p1|p2]"  # noqa: E501
        )
        if cheats_enabled:
            banner += " | [bold]kill[/bold] p1|p2 <idx> (k)"
        banner += "\nStart flags: --ai p1|p2|both, --auto\nSee: gamerules.md"
        self.console.print(banner)

    def _print_ai_banner(self) -> None:
        self.console.print(
            (
                "[bold]Tip:[/bold] Type [bold]ai[/bold] to have the [bold]current player[/bold] act once.\n"
                "Use [bold]ai p1[/bold] or [bold]ai p2[/bold] to target a side.\n"
                "Deploy with [bold]dN[/bold] or [bold]d N[/bold]; end turn with [bold]e[/bold] or [bold]end[/bold]."
            )
        )

    def _check_game_over(self, gs: GameState) -> bool:
        # First, check for loser marked on GameState
        if getattr(gs, "loser", None) is not None:
            loser = gs.loser
            winner = "P1" if loser == "P2" else "P2"
            self.console.print(f"[bold]Game over! Winner: {winner}[/bold]")
            return True
        if engine_rule_shim and hasattr(engine_rule_shim, "check_sl_loss"):
            loser = engine_rule_shim.check_sl_loss(gs)
            if loser is not None:
                winner = "P1" if loser == "P2" else "P2"
                self.console.print(f"[bold]Game over! Winner: {winner}[/bold]")
                return True
        return False

    def render(self, gs: GameState) -> None:
        c = self.console
        c.print(f"Turn {gs.turn_number} | Player: {'P1' if gs.turn_player is gs.p1 else 'P2'}")

        def board_table(title: str, player) -> Table:
            t = Table(title=title)
            t.add_column("#", justify="right", style="cyan")
            t.add_column("Name")
            t.add_column("Wind", justify="right")
            t.add_column("Abilities")
            for i, card in enumerate(getattr(player, "board", [])):
                abil_txt = _abilities_block(card)
                t.add_row(
                    str(i),
                    _name_with_icons(card, _resolve_faction(player, card)),
                    str(getattr(card, "wind", 0)),
                    abil_txt,
                )
            return t

        c.print(board_table("Board P1", gs.p1))
        c.print(board_table("Board P2", gs.p2))

        # Dead Pool HUD line (always show counters)
        bio = getattr(gs, "dead_pool_bio", 0)
        mech = getattr(gs, "dead_pool_mech", 0)
        c.print(f"Dead Pool 🥩:{bio} ⚙️:{mech}")

        def hand_table(title: str, player) -> Table:
            t = Table(title=f"{title} hand ({len(player.hand)})")
            t.add_column("#", justify="right", style="cyan")
            t.add_column("Name")
            t.add_column("Cost")
            for i, card in enumerate(player.hand):
                t.add_row(
                    str(i),
                    _name_with_icons(card, _resolve_faction(player, card)),
                    _cost_block(card),
                )
            return t

        if gs.turn_player is gs.p1:
            c.print(hand_table("P1", gs.p1))
        else:
            c.print(hand_table("P2", gs.p2))

    def run_loop(self, gs: GameState, ai_p1: bool = False, ai_p2: bool = False, auto: bool = False):
        import os

        from ..ai import ai_take_turn
        from ..engine import deploy_from_hand
        from ..engine import end_of_turn
        from ..engine import use_ability_cli

        cheats_enabled = os.environ.get("GSG_CHEATS") in ("1", "true", "on", "yes")
        self._print_command_banner(cheats_enabled=cheats_enabled)
        self._print_ai_banner()

        while True:
            if self._check_game_over(gs):
                break

            self.render(gs)

            if auto and ((gs.turn_player is gs.p1 and ai_p1) or (gs.turn_player is gs.p2 and ai_p2)):
                prev = gs.turn_player
                ai_take_turn(gs)
                if gs.turn_player is prev:
                    end_of_turn(gs)
                continue

            try:
                line = self.console.input("> ").strip()
            except KeyboardInterrupt:
                break
            except Exception:
                break

            if not line:
                continue

            parts = line.split()

            if line in ("help", "h"):
                self._print_command_banner(cheats_enabled=cheats_enabled)
                self._print_ai_banner()
                if cheats_enabled:
                    self.console.print("[bold]kill p1|p2 <idx>[/bold] (alias: k) — Instantly destroy a goon on the board by index.")
                continue
            if parts[0] in ("kill", "k"):
                if not cheats_enabled:
                    self.console.print("unknown command")
                    continue
                if len(parts) != 3 or parts[1] not in ("p1", "p2") or not parts[2].isdigit():
                    self.console.print("Usage: kill p1|p2 <idx>")
                    continue
                side = gs.p1 if parts[1] == "p1" else gs.p2
                idx = int(parts[2])
                board = getattr(side, "board", [])
                if idx < 0 or idx >= len(board):
                    self.console.print("Invalid index for kill command.")
                    continue
                card = board[idx]
                from ..rules import apply_wind
                from ..rules import destroy_if_needed

                cur = int(getattr(card, "wind", 0) or 0)
                delta = max(0, 4 - cur)
                if delta:
                    apply_wind(gs, card, delta)
                destroyed = destroy_if_needed(gs, card)
                self.console.print(f"killed: {getattr(card, 'name','?')}" if destroyed else "no-op: not on board")
                continue

            if line in ("deadpool", "dp"):
                dead = getattr(gs, "dead_pool", [])
                if not dead:
                    self.console.print("Dead Pool is empty.")
                else:
                    from rich.table import Table

                    t = Table(title="Dead Pool")
                    t.add_column("#", justify="right", style="cyan")
                    t.add_column("Name")
                    t.add_column("Icons")
                    for i, card in enumerate(dead):
                        # Reuse _name_with_icons for icons tail
                        name_with_icons = _name_with_icons(card, getattr(card, "faction", None))
                        # Split name and icons (assume icons are at the end)
                        if " " in name_with_icons:
                            name, icons = name_with_icons.split(" ", 1)
                        else:
                            name, icons = name_with_icons, ""
                        t.add_row(str(i), name, icons)
                    self.console.print(t)
                continue

            if line in ("quit", "q", "exit"):
                break

            if line in ("end", "e"):
                end_of_turn(gs)
                continue

            parts = line.split()

            if parts[0] == "ai":
                if len(parts) == 1:
                    prev = gs.turn_player
                    ai_take_turn(gs)
                    if gs.turn_player is prev:
                        end_of_turn(gs)
                else:
                    side = parts[1].lower()
                    side_player = gs.p1 if side in ("p1", "1") else gs.p2
                    original = gs.turn_player
                    gs.turn_player = side_player
                    prev = gs.turn_player
                    ai_take_turn(gs)
                    if gs.turn_player is prev:
                        end_of_turn(gs)
                    gs.turn_player = original
                continue

            # replacing if (parts[0].startswith("d") and parts[0][1:].isdigit()) or (parts[0] == "d" and len(parts) >= 2 and parts[1].isdigit()):
            # replacing continue

            # deploy shortcuts: dN | d N | ddN
            kind, idx = _parse_d_cmd(parts[0]) if parts else (None, None)
            if kind and idx is not None:
                # allow "d N" form
                if parts[0] == "d" and len(parts) >= 2 and parts[1].isdigit():
                    idx = int(parts[1])

                try:
                    ok = deploy_from_hand(gs, gs.turn_player, idx)
                    print("deploy ok" if ok else "deploy failed: cannot pay / illegal")
                except Exception as e:
                    print(f"deploy error: {e}")
                continue

            if len(parts) >= 3 and parts[0] == "u" and parts[1].isdigit() and parts[2].isdigit():
                src = int(parts[1])
                abil = int(parts[2])
                spec = parts[3] if len(parts) >= 4 else None
                use_ability_cli(gs, src, abil, spec)
                continue

            if parts[0] == "pay" and len(parts) >= 3 and parts[1].isdigit():
                amt = int(parts[1])
                spec = parts[2]
                from ..engine import manual_pay_cli

                manual_pay_cli(gs, amt, spec)
                continue

            self.console.print(
                "commands: help | quit(q) | end(e) | dN|d N | ddN|dd N | " "u <src> <abil> | pay <amount> p1|p2:idx[,idx] | ai [p1|p2]  " "(start flags: --ai p1|p2|both, --auto)  See: gamerules.md"
            )
