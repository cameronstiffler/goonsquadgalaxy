#!/usr/bin/env bash
set -euo pipefail

python3 - <<'PY'
from gsgsim.loader import load_deck_json
from gsgsim.loader import build_cards
from gsgsim.loader import find_squad_leader
from gsgsim.models import Player
from gsgsim.models import GameState
from gsgsim.engine import start_of_turn
from gsgsim.engine import end_of_turn
from gsgsim.engine import deploy_from_hand
from gsgsim.rules import apply_wind

# Build a tiny game state
narc = build_cards(load_deck_json("narc_deck_strict.json"), faction="NARC")
pcu  = build_cards(load_deck_json("pcu_deck_strict.json"),  faction="PCU")
p1_sl, p2_sl = find_squad_leader(narc), find_squad_leader(pcu)
p1 = Player("NARC", board=[p1_sl], hand=[c for c in narc if c is not p1_sl][:3], deck=[], retired=[])
p2 = Player("PCU", board=[p2_sl], hand=[c for c in pcu  if c is not p2_sl][:3], deck=[], retired=[])
gs = GameState(p1=p1, p2=p2, turn_player=p1, phase="start", turn_number=1, rng=None)
start_of_turn(gs)

# Deploy from hand index 0 (may be 0-cost in this trimmed hand)
ok = deploy_from_hand(gs, gs.turn_player, 0)
assert ok is not False, "deploy_from_hand failed (hand[0] illegal or cannot pay)"

# End/Start to clear just_deployed lock
end_of_turn(gs); start_of_turn(gs)

# KO rule: push a board card to 4 wind
target = gs.turn_player.board[0]
apply_wind(gs, target, +4)
assert target not in gs.turn_player.board and target in gs.turn_player.retired, "KO at 4 failed"

print("SMOKE OK")
PY
