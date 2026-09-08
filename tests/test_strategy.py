import copy
import importlib
from pathlib import Path
import random
import threading
import unittest

from holdem.engine import Action, Hand, Player
from holdem import agents


class StrategyTests(unittest.TestCase):
    def strategy(self):
        self.assertIsNotNone(importlib.util.find_spec('holdem.strategy'), 'Strategy analysis is missing')
        return importlib.import_module('holdem.strategy')

    def view(self, players=9):
        hand = Hand([Player(str(i), 1000) for i in range(players)], rng=random.Random(8))
        return hand.observation(hand.actor)

    def test_profiles_have_separate_loadable_skills_for_every_seat(self):
        strategy = self.strategy()
        self.assertEqual([p.name for p in strategy.PROFILES],
                         ['YOU', 'NOVA', 'BLAZE', 'ECHO', 'MOSS', 'ATLAS', 'JADE', 'RAVEN', 'ORBIT'])
        bodies = []
        for profile in strategy.PROFILES:
            path = Path(strategy.SKILLS_ROOT) / f'poker-{profile.key}' / 'SKILL.md'
            self.assertTrue(path.is_file())
            body = path.read_text()
            self.assertIn(body, strategy.skill_text(profile.key))
            bodies.append(body)
        self.assertEqual(len(set(bodies)), 9)

    def test_positions_skip_eliminated_seats(self):
        strategy = self.strategy()
        hand = Hand([Player(str(i), stack) for i, stack in enumerate([100, 0, 100, 100, 0])])
        self.assertEqual(strategy.analyze(hand.observation(0), random.Random(1), samples=12)['position'], 'BTN')
        self.assertEqual(strategy.analyze(hand.observation(2), random.Random(1), samples=12)['position'], 'SB')
        self.assertEqual(strategy.analyze(hand.observation(3), random.Random(1), samples=12)['position'], 'BB')
        self.assertEqual(strategy.analyze(self.view(), random.Random(1), samples=12)['position'], 'UTG')

    def test_analysis_does_not_mutate_or_access_hidden_state(self):
        strategy = self.strategy()
        obs = self.view()
        before = copy.deepcopy(obs)
        first = strategy.analyze(obs, random.Random(3), samples=32)
        self.assertEqual(obs, before)
        second = strategy.analyze(copy.deepcopy(obs), random.Random(3), samples=32)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first['range_equity'], 0)
        self.assertLessEqual(first['range_equity'], 1)
        self.assertEqual(first['equity_samples'], 32)
        self.assertNotIn('deck', first)
        self.assertNotIn('opponent_holes', first)

    def river(self, hole, board, stacks=(90, 90), committed=(10, 10), call=0):
        obs = self.view(len(stacks))
        obs.update(seat=0, street='river', hole=hole.split(), board=board.split(),
                   pot=sum(committed), current_bet=call)
        for p, stack, amount in zip(obs['players'], stacks, committed):
            p.update(stack=stack, committed=amount, bet=0, folded=False)
        obs['legal'] = {'actions': ['fold', 'call', 'allin'] if call else ['fold', 'check', 'raise', 'allin'],
                        'to_call': call, 'min_raise_to': 10, 'max_raise_to': stacks[0]}
        return obs

    def test_ties_and_nuts_use_fractional_equity(self):
        strategy = self.strategy()
        tied = self.river('2c 3d', 'As Ks Qs Js Ts', (90,) * 3, (10,) * 3)
        result = strategy.analyze(tied, random.Random(4), samples=24)
        self.assertAlmostEqual(result['range_equity'], 1 / 3, places=4)
        nuts = self.river('As Ks', 'Qs Js Ts 2d 3c')
        self.assertEqual(strategy.analyze(nuts, random.Random(4), samples=24)['range_equity'], 1)

    def test_short_stack_price_excludes_ineligible_sidepot(self):
        strategy = self.strategy()
        obs = self.river('As Ks', 'Qs Js Ts 2d 3c', (5, 0, 0), (0, 100, 100), call=5)
        result = strategy.analyze(obs, random.Random(4), samples=24)
        self.assertEqual(result['eligible_pot_after_call'], 15)
        self.assertAlmostEqual(result['pot_odds'], 1 / 3, places=4)
        self.assertEqual(result['call_ev_chips'], 10)

    def test_preflop_raise_weights_strong_combos_over_trash(self):
        strategy = self.strategy()
        obs = self.view(3)
        obs['action_history'] = [{'seat': 1, 'street': 'preflop', 'action': 'raise',
                                  'raise_to': 40, 'paid': 35, 'to_call_before': 5}]
        self.assertGreater(strategy.range_weight(['As', 'Ah'], obs, 1),
                           strategy.range_weight(['7c', '2d'], obs, 1) * 3)

    def test_analysis_cancels_before_sampling(self):
        strategy = self.strategy()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            strategy.analyze(self.view(), random.Random(1), cancel=cancel)

    def test_public_stats_count_hands_and_opportunities_without_cards(self):
        strategy = self.strategy()
        hand = Hand([Player('YOU', 100), Player('NOVA', 100), Player('BLAZE', 100)])
        hand.act(Action('raise', 30))
        hand.act(Action('fold'))
        hand.act(Action('fold'))
        stats = strategy.PublicStats()
        stats.record(hand.observation(0))
        rows = stats.snapshot()
        self.assertEqual(rows[0]['hands'], 1)
        self.assertEqual(rows[0]['vpip_pct'], 100)
        self.assertEqual(rows[0]['pfr_pct'], 100)
        self.assertEqual(rows[1]['fold_to_bet_pct'], 100)
        self.assertEqual(rows[2]['vpip_pct'], 0)
        self.assertTrue(all('hole' not in row and 'board' not in row for row in rows))

    def test_program_folds_early_position_trash_and_raises_aces(self):
        self.assertTrue(hasattr(agents, 'StrategicAgent'), 'Program opponent is missing')
        obs = self.view()
        for hole, expected in [('7c 2d', 'fold'), ('As Ah', 'raise')]:
            with self.subTest(hole=hole):
                obs['hole'] = hole.split()
                action = agents.StrategicAgent(random.Random(2), samples=48).decide(obs, 'nova').action
                self.assertEqual(action.kind, expected)

    def test_program_never_folds_free_action_or_unbeatable_hand(self):
        self.assertTrue(hasattr(agents, 'StrategicAgent'), 'Program opponent is missing')
        agent = agents.StrategicAgent(random.Random(2), samples=24)
        free = self.river('7c 2d', 'As Kh Qs Js 9h')
        self.assertNotEqual(agent.decide(free, 'nova').action.kind, 'fold')
        nuts = self.river('As Ks', 'Qs Js Ts 2d 3c', call=50)
        self.assertIn(agent.decide(nuts, 'nova').action.kind, ['call', 'allin'])

    def test_program_provider_is_explicit_and_codex_remains_default(self):
        from holdem.__main__ import Application, parser
        self.assertIn('strategic', parser()._option_string_actions['--agent'].choices)
        self.assertEqual(parser().parse_args([]).agent, 'codex')
        app = Application(parser().parse_args(['--agent', 'strategic']))
        self.assertIsNone(app.agent.codex)
        self.assertIn('STRATEGIC', app.agent.label)

    def test_program_handles_complete_tables_with_short_stacks(self):
        self.assertTrue(hasattr(agents, 'StrategicAgent'))
        rng = random.Random(15)
        agent = agents.StrategicAgent(random.Random(4), samples=12)
        for n in range(2, 10):
            for _ in range(3):
                hand = Hand([Player(str(i), rng.randrange(1, 200)) for i in range(n)], rng=rng)
                total = sum(p.stack for p in hand.players) + hand.pot
                while not hand.done:
                    hand.act(agent.decide(hand.observation(hand.actor), 'orbit').action)
                self.assertEqual(sum(p.stack for p in hand.players), total)


if __name__ == '__main__':
    unittest.main()
