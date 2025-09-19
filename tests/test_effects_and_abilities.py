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


def test_effect_mark_targets_without_registry(monkeypatch):
    from gsgsim.abilities import use_ability

    t1 = mk_card("Target1")
    t2 = mk_card("Target2")
    src = mk_card("Marker", abilities=[Ability("MARK", cost={"wind": 0}, effects=[{"op": "mark"}])])
    gs = types.SimpleNamespace(turn_player=mk_player([src]))
    ok = use_ability(gs, src, 0, [t1, t2])
    assert ok
    assert getattr(t1, "marked", False) is True
    assert getattr(t2, "marked", False) is True
    assert getattr(src, "marked", False) is False  # we passed explicit targets


def test_effect_mark_self_when_no_targets(monkeypatch):
    from gsgsim.abilities import use_ability

    src = mk_card("SelfMarker", abilities=[Ability("MARK", cost={"wind": 0}, effects=[{"op": "mark"}])])
    gs = types.SimpleNamespace(turn_player=mk_player([src]))
    ok = use_ability(gs, src, 0, None)
    assert ok
    assert getattr(src, "marked", False) is True


def test_cost_not_charged_if_no_exec(monkeypatch):
    # If no effects and no handler, do not charge cost
    from gsgsim.abilities import use_ability

    sl = mk_card("Lokar", rank="SL", wind=3)
    src = mk_card("Dummy", abilities=[Ability("NOOP", cost={"wind": 2}, effects=[])])
    gs = types.SimpleNamespace(turn_player=mk_player([sl]))
    ok = use_ability(gs, src, 0, None)
    assert ok is False
    assert sl.wind == 3  # no payment taken


def test_cost_charged_then_success_via_registry(monkeypatch):
    from gsgsim.abilities import registers
    from gsgsim.abilities import use_ability

    called = {"ok": False}

    @registers("ProbeX", 0)
    def _ok(gs, card, targets):
        called["ok"] = True
        return True

    src = mk_card("ProbeX", abilities=[Ability("PING", cost={"wind": 1}, effects=[])])
    bg = mk_card("BG", wind=0)
    gs = types.SimpleNamespace(turn_player=mk_player([bg]))
    # Cost paid by the source goon; registry handler still fires
    ok = use_ability(gs, src, 0, None)
    assert ok is True and called["ok"] is True
    assert getattr(src, "wind", 0) == 1
