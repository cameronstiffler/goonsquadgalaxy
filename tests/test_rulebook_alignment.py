import types

from gsgsim.payments import distribute_wind
from gsgsim.rules import apply_wind_with_resist
from gsgsim.rules import destroy_if_needed


def mk_card(name="Card", rank="BG", wind=0, biological=False, mechanical=False):
    return types.SimpleNamespace(
        name=name,
        rank=rank,
        wind=wind,
        biological=biological,
        mechanical=mechanical,
        status={},
    )


def mk_player(board):
    return types.SimpleNamespace(board=list(board), retired=[])


def mk_gs(p1_board=None, p2_board=None):
    return types.SimpleNamespace(
        p1=mk_player(p1_board or []),
        p2=mk_player(p2_board or []),
        dead_pool=[],
        dead_pool_bio=0,
        dead_pool_mech=0,
    )


def test_titan_removed_from_game_not_dead_pooled():
    titan = mk_card(rank="T", wind=4)
    gs = mk_gs([titan])

    assert destroy_if_needed(gs, titan) is True
    assert titan not in gs.dead_pool
    assert gs.dead_pool == []
    assert gs.p1.retired == [titan]
    assert titan not in gs.p1.board


def test_titan_cannot_pay_wind_costs():
    titan = mk_card(rank="T", wind=0)
    player = mk_player([titan])

    assert distribute_wind(player, 1) is False
    assert titan.wind == 0


def test_resist_reduces_hostile_wind_by_one():
    goon = mk_card(rank="BG", wind=0)
    goon.resist = True
    gs = mk_gs([goon])

    applied = apply_wind_with_resist(gs, goon, 2, hostile=True)
    assert applied == 1
    assert goon.wind == 1


def test_ai_auto_pay_never_self_kos():
    sl = mk_card(rank="SL", wind=3)
    bg = mk_card(rank="BG", wind=3)
    player = mk_player([sl, bg])
    gs = mk_gs([sl, bg])
    gs.turn_player = gs.p1
    gs.turn_player.controller = "ai"

    # Auto with gs should refuse lethal payments
    assert distribute_wind(player, 1, gs=gs, auto=True) is False
    assert sl.wind == 3
    assert bg.wind == 3

    # Non-lethal payment is allowed
    bg.wind = 1
    assert distribute_wind(player, 1, gs=gs, auto=True) is True
    assert bg.wind == 2
