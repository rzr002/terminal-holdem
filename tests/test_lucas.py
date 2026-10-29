import copy
import random
import threading
import unittest
from unittest.mock import patch

from holdem.agents import AgentError, CodexAgent, ResilientAgent
from holdem.engine import Action, Hand, Player
from holdem.lucas import LucasAgent, lucas_state, legal_action, reconstruct_log
from holdem.__main__ import Application, parser
from holdem.ui import HeadlessUI, frame_lines


def table(stacks=(2000,) * 6, button=0):
    return Hand([Player(f'P{i}', stack) for i, stack in enumerate(stacks)],
                button, 10, 20, random.Random(17))


class LucasTests(unittest.TestCase):
    def test_defaults_to_six_players_without_creating_codex(self):
        args = parser().parse_args([])
        self.assertEqual((args.agent, args.players), ('lucas', 6))
        with patch.object(CodexAgent, '__init__', side_effect=AssertionError('No API calls')):
            app = Application(args)
        self.assertIsNone(app.agent.codex)
        self.assertIn('LUCAS v16', app.agent.label)
        self.assertEqual(parser().parse_args(['--agent', 'codex']).players, 9)
        self.assertEqual(parser().parse_args(['--model', 'example-model']).agent, 'codex')

    def test_normalizes_blinds_and_does_not_expose_dealer_or_other_holes(self):
        hand = table()
        obs = hand.observation(hand.actor)
        before = copy.deepcopy(obs)
        state = lucas_state(obs)
        self.assertEqual(state['your_stack'], 10000)
        self.assertEqual(state['pot'], 150)
        self.assertEqual(state['min_raise_to'], 200)
        self.assertEqual(state['amount_owed'], 100)
        self.assertEqual(state['your_cards'], obs['hole'])
        self.assertEqual([x['amount'] for x in state['action_log']], [50, 100])
        self.assertTrue(all('hole' not in p and 'cards' not in p for p in state['players']))
        self.assertFalse({'deck', 'rng', 'seed', 'review_board'} & state.keys())
        state['your_cards'].clear()
        self.assertEqual(obs, before)

    def test_exact_history_retains_streets_and_short_all_in_call(self):
        hand = table((2000, 2000, 2000, 17, 2000, 2000))
        while hand.street == 'preflop' and not hand.done:
            hand.act(Action('check' if 'check' in hand.legal()['actions'] else 'call'))
        hand.act(Action('check'))
        state = lucas_state(hand.observation(hand.actor))
        records = reconstruct_log(state['action_log'])
        self.assertEqual([x['street_idx'] for x in records], [0] * 6 + [1])
        self.assertEqual(records[-1]['pot_before'], 117 * 5)
        self.assertEqual(records[-1]['owed'], 0)
        self.assertEqual(state['action_log'][1]['amount'], 100)
        self.assertEqual(state['action_log'][2]['action'], 'call')
        self.assertEqual(state['action_log'][2]['amount'], 85)

    def test_actual_short_blind_is_preserved_after_payout(self):
        hand = table((2000, 2000, 17, 2000, 2000, 2000))
        while not hand.done:
            hand.act(Action('check' if 'check' in hand.legal()['actions'] else 'call'))
        state = lucas_state(hand.observation(0))
        self.assertEqual(state['action_log'][1]['amount'], 85)
        self.assertEqual(reconstruct_log(state['action_log'])[-1]['pot_before'], 117 * 5)

    def test_compacts_busted_seats_but_keeps_stable_opponent_ids(self):
        hand = table((2000, 0, 2000, 2000, 0, 2000), button=2)
        state = lucas_state(hand.observation(hand.actor))
        self.assertEqual([p['seat'] for p in state['players']], [0, 1, 2, 3])
        self.assertEqual([p['bot_id'] for p in state['players']], ['seat-0', 'seat-2', 'seat-3', 'seat-5'])
        self.assertEqual([e['seat'] for e in state['action_log']], [2, 3])

    def test_raise_conversion_rounds_then_respects_actual_legal_bounds(self):
        obs = table().observation(3)
        self.assertEqual(legal_action({'action': 'raise', 'amount': 251}, obs), Action('raise', 50))
        self.assertEqual(legal_action({'action': 'raise', 'amount': 1}, obs), Action('raise', 40))
        self.assertEqual(legal_action({'action': 'all_in'}, obs), Action('allin'))
        closed = copy.deepcopy(obs)
        closed['legal']['actions'] = ['fold', 'call']
        self.assertEqual(legal_action({'action': 'all_in'}, closed), Action('call'))
        self.assertEqual(legal_action({'action': 'raise', 'amount': 300}, closed), Action('call'))

    def test_independent_seats_and_full_preflop_folding_allowed(self):
        agent = LucasAgent()
        hand = table()
        obs = hand.observation(hand.actor)
        module = agent.session(obs['seat'])
        with patch.object(module, 'decide', return_value={'action': 'fold'}) as decide:
            result = agent.decide(obs)
        self.assertEqual(result.action, Action('fold'))
        self.assertEqual(decide.call_args.args[0]['your_cards'], obs['hole'])
        other = agent.session(0)
        self.assertIsNot(module.PLAYER_STATS, other.PLAYER_STATS)
        module.PLAYER_STATS['sentinel'] = {}
        self.assertNotIn('sentinel', other.PLAYER_STATS)

    def test_dependency_failure_pauses_instead_of_changing_opponent(self):
        agent = LucasAgent()
        with patch.object(agent, 'session', side_effect=ImportError('missing eval7')):
            with self.assertRaisesRegex(AgentError, 'eval7'):
                agent.decide(table().observation(3))

    def test_cancel_before_decision(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            LucasAgent().decide(table().observation(3), cancel=cancel)

    def test_postflop_uses_real_equity_without_emergency_fallback(self):
        hand = table()
        while hand.street == 'preflop':
            hand.act(Action('check' if 'check' in hand.legal()['actions'] else 'call'))
        agent = LucasAgent()
        module = agent.session(hand.actor)
        with patch.object(module, '_hero_equity', wraps=module._hero_equity) as equity, \
                patch.object(module, '_emergency', side_effect=AssertionError('Policy error')):
            decision = agent.decide(hand.observation(hand.actor))
        self.assertTrue(equity.called)
        self.assertIn(decision.action.kind, hand.legal()['actions'])
        result = module.equity_vs_random(['As', 'Ah'], ['Ac', 'Ad', '2s', '3s', '4h'], 1)
        self.assertEqual(result, 1.0)

    def test_real_six_seat_hands_are_legal_conserve_chips_and_record_final_actions(self):
        agent = ResilientAgent('lucas')
        for number, stacks in enumerate(((2000,) * 6, (21, 37, 105, 200, 555, 2000),
                                         (2000, 0, 2000, 33, 2000, 0)), 1):
            hand = table(stacks)
            hand.number = number
            actions = 0
            while not hand.done:
                obs = hand.observation(hand.actor)
                decision = agent.decide(obs)
                self.assertEqual(decision.source, 'lucas')
                self.assertIn(decision.action.kind, hand.legal()['actions'])
                hand.act(decision.action)
                actions += 1
                self.assertLess(actions, 200)
            for seat, player in enumerate(hand.players):
                if player.hole:
                    agent.observe(hand.observation(seat))
            self.assertEqual(sum(p.stack for p in hand.players), sum(stacks))
            self.assertEqual(hand.pot, 0)
            self.assertTrue(any(m.PLAYER_STATS for m in agent.local.sessions.values()))

    def test_ui_labels_the_standard_table(self):
        text = '\n'.join(frame_lines(table(), 100, 'LUCAS v16 / 本地', False, False, ''))
        self.assertIn('标准德州', text)
        self.assertNotIn('先看翻牌', text)
        self.assertIn('Lucas v16', text)


if __name__ == '__main__':
    unittest.main()
