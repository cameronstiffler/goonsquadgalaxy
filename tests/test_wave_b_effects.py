from gsgsim.abilities import use_ability
from gsgsim.engine import deploy_from_hand
from gsgsim.engine import end_of_turn
from gsgsim.engine import pay_to_unwind
from gsgsim.engine import run_machine_effects
from gsgsim.engine import start_of_turn
from gsgsim.loader import build_cards
from gsgsim.loader import load_deck_json
from gsgsim.models import Ability
from gsgsim.models import Card
from gsgsim.models import GameState
from gsgsim.models import Player
from gsgsim.models import Rank
from gsgsim.rules import apply_wind
from gsgsim.rules import can_target_card
from gsgsim.rules import destroy_if_needed
from gsgsim.rules import has_status


def _mk_state():
    p1 = Player("P1")
    p2 = Player("P2")
    gs = GameState(p1=p1, p2=p2, turn_player=p1, phase="main", turn_number=1)
    gs.dead_pool = []
    gs.dead_pool_bio = 0
    gs.dead_pool_mech = 0
    return gs


def test_meatjacker_clone_copies_abilities_from_dead_pool():
    gs = _mk_state()
    deck = build_cards(load_deck_json("pcu_deck_strict.json"), "PCU")
    meatjacker = next(c for c in deck if c.name == "Meatjacker")
    base_count = len(meatjacker.abilities)
    gs.turn_player.hand.append(meatjacker)

    donor = Card(
        name="Prototype",
        rank=Rank.BG,
        abilities=[Ability("LASER GRID", {"wind": 0}, [], passive=False)],
    )
    donor.mechanical = True
    gs.dead_pool = [donor]
    gs.dead_pool_mech = 1

    assert deploy_from_hand(gs, gs.turn_player, 0)
    deployed = gs.turn_player.board[-1]
    ability_names = {ab.name for ab in deployed.abilities}
    assert "LASER GRID" in ability_names
    assert len(deployed.abilities) == base_count + 1

    apply_wind(gs, deployed, 4)
    destroy_if_needed(gs, deployed)
    assert len(deployed.abilities) == base_count


def test_pay_to_unwind_transfers_wind_from_allies():
    gs = _mk_state()
    watcher = Card("Watcher", Rank.BG)
    ally = Card("Ally", Rank.BG)
    watcher.wind = 3
    watcher.status = {"pay_to_unwind": [{"value": {"source": watcher}, "duration": "persistent", "owner": gs.turn_player}]}
    gs.turn_player.board.extend([watcher, ally])
    assert pay_to_unwind(gs, watcher, [(ally, 2)])
    assert watcher.wind == 1
    assert ally.wind == 2

    gs.turn_player = gs.p2
    assert not pay_to_unwind(gs, watcher, [(ally, 1)])


def test_sentry_mode_blocks_hostile_targeting_until_next_turn():
    gs = _mk_state()
    narc_cards = build_cards(load_deck_json("narc_deck_strict.json"), "NARC")
    buzzkill = next(c for c in narc_cards if c.name == "Buzzkill")
    protector = Card("Bodyguard", Rank.BG)
    gs.turn_player.board.extend([buzzkill, protector])

    ability = buzzkill.abilities[0]  # SENTRY MODE

    def chooser(kind, payload):
        if kind == "choose_targets":
            return [protector]
        return []

    run_machine_effects(gs, buzzkill, ability, chooser)
    assert has_status(protector, "cannot_be_targeted_enemy")

    opp = Card("Sniper", Rank.BG)
    gs.p2.board.append(opp)

    assert not can_target_card(gs, opp, protector, hostile=True)
    assert can_target_card(gs, buzzkill, protector, hostile=False)

    end_of_turn(gs)
    end_of_turn(gs)
    assert can_target_card(gs, opp, protector, hostile=True)


def test_alter_wind_when_targeted_by_hostile_ability_triggers_once():
    gs = _mk_state()
    narc_cards = build_cards(load_deck_json("narc_deck_strict.json"), "NARC")
    k9 = next(c for c in narc_cards if c.name == "K-9.0")
    ally = Card("Shieldmate", Rank.BG)
    gs.turn_player.board.extend([k9, ally])

    ability = k9.abilities[0]  # SCAN FOR VULNERABILITY

    def chooser(kind, payload):
        if kind == "choose_targets":
            return [ally]
        return []

    run_machine_effects(gs, k9, ability, chooser)
    assert has_status(ally, "hostile_target_reaction")

    pcu_cards = build_cards(load_deck_json("pcu_deck_strict.json"), "PCU")
    dragoon = next(c for c in pcu_cards if c.name == "Dragoon")
    gs.p2.board.append(dragoon)
    gs.turn_player = gs.p2

    assert ally.wind == 0
    use_ability(gs, dragoon, 0, [ally])
    assert ally.wind == 2  # 1 from DIVE ATTACK + 1 from reaction

    gs.turn_player = gs.p1
    support = Card(
        "Support",
        Rank.BG,
        abilities=[
            Ability(
                "BOOST",
                {"wind": 0},
                [
                    {
                        "effect_type": "alter_wind",
                        "amount": 1,
                        "target": ["any"],
                        "duration": "instant",
                    }
                ],
                passive=False,
            )
        ],
    )
    gs.turn_player.board.append(support)
    use_ability(gs, support, 0, [ally])
    assert ally.wind == 3  # only base ability applies
