from gsgsim.engine import start_of_turn
from gsgsim.loader import build_cards
from gsgsim.loader import find_squad_leader
from gsgsim.loader import load_deck_json
from gsgsim.models import GameState
from gsgsim.models import Player
from gsgsim.rules import apply_wind
from gsgsim.rules import apply_wind_with_resist


def test_ko_at_four_wind():
    gs = _mk_game()
    target = gs.turn_player.board[0]
    apply_wind(gs, target, +4)
    assert target in gs.turn_player.retired


def test_resist_reduces_hostile_by_one_min_zero():
    gs = _mk_game()
    target = gs.turn_player.board[0]
    setattr(target, "has_resist", True)
    before = getattr(target, "wind", 0)
    apply_wind_with_resist(gs, target, +1, hostile=True)
    after = getattr(target, "wind", 0)
    assert (after - before) == 0


def test_no_unwind_blocks_auto_unwind():
    from gsgsim.engine import end_of_turn

    gs = _mk_game()
    card = gs.turn_player.board[0]
    setattr(card, "wind", 2)
    setattr(card, "no_unwind", True)
    end_of_turn(gs)
    start_of_turn(gs)
    assert getattr(card, "wind", 0) == 2


def test_just_deployed_lock_blocks_spend_until_next_turn():
    from gsgsim import rules
    from gsgsim.engine import deploy_from_hand
    from gsgsim.engine import end_of_turn

    gs = _mk_game()
    # simulate a deploy: put a dummy card in hand and call deploy_from_hand if needed,
    # or set the flag directly for the test:
    card = gs.turn_player.board[0]
    setattr(card, "just_deployed", True)
    assert hasattr(rules, "cannot_spend_wind") and rules.cannot_spend_wind(gs, card)
    end_of_turn(gs)
    start_of_turn(gs)
    assert not rules.cannot_spend_wind(gs, card)


def _mk_game():
    narc = build_cards(load_deck_json("narc_deck_strict.json"), faction="NARC")
    pcu = build_cards(load_deck_json("pcu_deck_strict.json"), faction="PCU")
    p1_sl, p2_sl = find_squad_leader(narc), find_squad_leader(pcu)
    p1 = Player("NARC", board=[p1_sl], hand=[], deck=[], retired=[])
    p2 = Player("PCU", board=[p2_sl], hand=[], deck=[], retired=[])
    gs = GameState(p1=p1, p2=p2, turn_player=p1, phase="start", turn_number=1, rng=None)
    start_of_turn(gs)
    return gs
