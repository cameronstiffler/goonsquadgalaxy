from gsgsim.engine import deploy_from_hand
from gsgsim.models import Card
from gsgsim.models import GameState
from gsgsim.models import Player
from gsgsim.models import Rank


def _mk_state() -> GameState:
    p1 = Player("P1")
    p2 = Player("P2")
    return GameState(p1, p2, p1, "main", 1)


def _mutt_card() -> Card:
    card = Card(name="Mutt", rank=Rank.BG)
    card.deploy_wind = 0
    card.deploy_requirements = [{"type": "requires_card_in_play", "card_name": "Krax", "side": "self"}]
    return card


def test_mutt_requires_krax_in_play() -> None:
    gs = _mk_state()
    p1 = gs.turn_player

    # Without Krax, deployment should fail and leave the card in hand.
    mutt = _mutt_card()
    p1.hand.append(mutt)
    ok = deploy_from_hand(gs, p1, 0)
    assert not ok
    assert p1.hand and p1.hand[0].name == "Mutt"
    assert all(c.name != "Mutt" for c in p1.board)

    # Place Krax on the board and retry with a fresh Mutt; it should now succeed.
    p1.board.append(Card(name="Krax", rank=Rank.BG))
    fresh_mutt = _mutt_card()
    p1.hand = [fresh_mutt]
    ok = deploy_from_hand(gs, p1, 0)
    assert ok
    assert any(c.name == "Mutt" for c in p1.board)
