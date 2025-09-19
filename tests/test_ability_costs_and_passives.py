import types


def mk_player(board):
    return types.SimpleNamespace(board=list(board), hand=[])


def mk_card(name, rank="BG", wind=0, new_this_turn=False, abilities=None):
    return types.SimpleNamespace(name=name, rank=rank, wind=wind, new_this_turn=new_this_turn, abilities=abilities or [])


class Ability:
    def __init__(self, name, cost=None, effects=None, passive=False):
        self.name = name
        self.cost = cost or {}
        self.effects = effects or []
        self.passive = passive


def test_passive_cannot_be_used(monkeypatch):
    from gsgsim.abilities import use_ability

    gs = types.SimpleNamespace(turn_player=mk_player([]))
    c = mk_card("Probe", abilities=[Ability("RESOURCEFUL", cost={"wind": 1}, passive=True)])
    assert use_ability(gs, c, 0, None) is False


def test_active_cost_enforced(monkeypatch):
    # Ability costs 2 wind: cost should be applied to the source goon only
    from gsgsim.abilities import registers
    from gsgsim.abilities import use_ability

    called = {"ok": False}

    @registers("Probe", 0)
    def _fake(gs, card, targets):
        called["ok"] = True
        return True

    gs = types.SimpleNamespace(turn_player=mk_player([]))
    sl = mk_card("Lokar", rank="SL", wind=3, new_this_turn=False)
    gs.turn_player.board.append(sl)
    c = mk_card("Probe", abilities=[Ability("PING", cost={"wind": 2}, passive=False)])

    # Source goon pays its own cost; Squad Leader remains untouched
    assert use_ability(gs, c, 0, None) is True
    assert called["ok"] is True
    assert getattr(c, "wind", 0) == 2
    assert getattr(sl, "wind", 0) == 3


def test_active_cost_succeeds_with_non_sl_sources(monkeypatch):
    from gsgsim.abilities import registers
    from gsgsim.abilities import use_ability

    @registers("Probe2", 0)
    def _ok(gs, card, targets):
        return True

    # Board: two BGs with room to pay 2
    b1 = mk_card("BG1", wind=0)
    b2 = mk_card("BG2", wind=0)
    gs = types.SimpleNamespace(turn_player=mk_player([b1, b2]))
    c = mk_card("Probe2", abilities=[Ability("PING", cost={"wind": 2}, passive=False)])
    assert use_ability(gs, c, 0, None) is True
    assert getattr(c, "wind", 0) == 2
