from gsgsim.engine import deploy_from_hand
from gsgsim.engine import end_of_turn
from gsgsim.engine import start_of_turn
from gsgsim.loader import build_cards
from gsgsim.loader import find_squad_leader
from gsgsim.loader import load_deck_json
from gsgsim.models import GameState
from gsgsim.models import Player


def test_no_unwind_blocks_auto_unwind():
    gs = _mk_game()
    # put a fresh body on board if possible (not critical which card)
    deploy_from_hand(gs, gs.turn_player, 0)
    card = gs.turn_player.board[-1]
    setattr(card, "wind", 2)
    setattr(card, "no_unwind", True)
    end_of_turn(gs)
    start_of_turn(gs)
    assert getattr(card, "wind", 0) == 2, "no_unwind should prevent auto-unwind tick"


def test_just_deployed_lock_blocks_spend_until_next_turn():
    gs = _mk_game()
    # deploy gives the card the 'just_deployed' lock
    deploy_from_hand(gs, gs.turn_player, 0)
    card = gs.turn_player.board[-1]
    from gsgsim import rules

    assert hasattr(rules, "cannot_spend_wind") and rules.cannot_spend_wind(gs, card), "Newly deployed card must be locked this turn"
    # after ending & starting controller's next turn, lock clears
    end_of_turn(gs)
    start_of_turn(gs)
    assert not rules.cannot_spend_wind(gs, card), "Lock should clear at player's next start_of_turn"


def _mk_game():
    n = build_cards(load_deck_json("narc_deck_strict.json"), "NARC")
    p = build_cards(load_deck_json("pcu_deck_strict.json"), "PCU")
    p1_sl, p2_sl = find_squad_leader(n), find_squad_leader(p)
    p1 = Player("NARC", board=[p1_sl], hand=[c for c in n if c is not p1_sl][:4], deck=[], retired=[])
    p2 = Player("PCU", board=[p2_sl], hand=[c for c in p if c is not p2_sl][:4], deck=[], retired=[])
    gs = GameState(p1=p1, p2=p2, turn_player=p1, phase="start", turn_number=1, rng=None)
    start_of_turn(gs)
    return gs
