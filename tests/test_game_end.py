import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from holdem.__main__ import Application, parser
from holdem.agents import Decision
from holdem.engine import Action
from holdem.ui import CursesUI, HeadlessUI, PlainUI


class RecordingUI(HeadlessUI):
    def __init__(self):
        self.hands = []
        self.saw_unsettled_zero = False

    def human_action(self, app, hand):
        return Action('check' if 'check' in hand.legal()['actions'] else 'call')

    def render(self, app, hand, status=''):
        if not hand.done and not app.players[0].stack:
            self.saw_unsettled_zero = True

    def after_hand(self, app, hand, finished):
        self.hands.append((hand.number, finished, [p.stack for p in hand.players], hand.board.copy()))


class GameEndTests(unittest.TestCase):
    def play(self, *mode, seed=2, short_seat=0):
        app = Application(parser().parse_args([
            '--players', '3', '--stack', '100', '--seed', str(seed), '--hands', '2',
            '--delay', '0', *mode]))
        app.players[short_seat].stack = 10
        app.agent.missing_binary = False
        ui = RecordingUI()

        def passive(obs, *args):
            return Decision(Action('check' if 'check' in obs['legal']['actions'] else 'call'), 'codex')

        with patch.object(app.agent.codex, 'decide', side_effect=passive) as decide:
            report = app.run(ui)
        return app, ui, report, decide.call_args_list

    def test_busted_player_ends_game_before_any_next_hand_decision(self):
        for mode in [(), ('--autoplay',), ('--watch',), ('--headless',)]:
            with self.subTest(mode=mode):
                app, ui, report, calls = self.play(*mode)
                self.assertEqual(report['hands_played'], 1)
                self.assertEqual(app.players[0].stack, 0)
                self.assertEqual(sum(p.stack > 0 for p in app.players), 2)
                self.assertEqual([hand[:2] for hand in ui.hands], [(1, True)])
                self.assertEqual({call.args[0]['hand'] for call in calls}, {1})
                self.assertEqual(report['unsettled_pot'], 0)
                self.assertEqual(sum(p.stack for p in app.players), 210)
                self.assertEqual(len(app.hand.review_board), 5)
                self.assertIn('筹码已归零', report['end_reason'])

    def test_all_in_player_can_win_chips_back_and_continue(self):
        app, ui, report, calls = self.play(seed=0)
        self.assertTrue(ui.saw_unsettled_zero)
        self.assertEqual(ui.hands[0][1], False)
        self.assertEqual(ui.hands[0][2][0], 30)
        self.assertEqual(len(ui.hands[0][3]), 5)
        self.assertEqual(report['hands_played'], 2)
        self.assertIn(2, {call.args[0]['hand'] for call in calls})
        self.assertEqual(sum(p.stack for p in app.players), 210)

    def test_other_opponent_busting_does_not_end_players_game(self):
        app, ui, report, _ = self.play(seed=0, short_seat=2)
        self.assertEqual(ui.hands[0][2][2], 0)
        self.assertGreater(ui.hands[0][2][0], 0)
        self.assertFalse(ui.hands[0][1])
        self.assertEqual(report['hands_played'], 2)

    def test_plain_end_screen_explains_bust_without_next_hand_prompt(self):
        app, _, _, _ = self.play()
        with patch('sys.stdout', new_callable=io.StringIO) as output, \
                patch('builtins.input', side_effect=AssertionError('Must not prompt for another hand')):
            PlainUI().after_hand(app, app.hand, True)
        self.assertIn('筹码已归零', output.getvalue())
        self.assertNotIn('下一手', output.getvalue())

    def test_curses_end_screen_only_offers_exit(self):
        app, _, _, _ = self.play()
        ui = CursesUI.__new__(CursesUI)
        ui.screen = Mock()
        ui.screen.getmaxyx.return_value = (32, 100)
        ui.curses = SimpleNamespace(KEY_ENTER=343)
        ui.boss = ui.paused = False
        ui.raise_text = None
        ui.notice = ''
        ui._write = Mock()
        ui._key = Mock(side_effect=['n', '\n'])
        ui.after_hand(app, app.hand, True)
        content = '\n'.join(call.args[1] for call in ui._write.call_args_list)
        self.assertIn('筹码已归零', content)
        self.assertIn('Q / Enter 退出', content)
        self.assertNotIn('下一手', content)
        self.assertEqual(ui._key.call_count, 2)
