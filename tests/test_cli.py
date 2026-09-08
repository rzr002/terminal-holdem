import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import tempfile
from unittest.mock import patch

from holdem.__main__ import Application, parser
from holdem.agents import Decision
from holdem.ui import HeadlessUI

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_default_opponents_are_codex(self):
        self.assertEqual(parser().parse_args([]).agent, 'codex')

    def test_list_models_exits_without_starting_game_or_requesting_codex(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'models_cache.json').write_text(json.dumps({'models': [
                {'slug': 'test-fast', 'visibility': 'list', 'description': 'Fast model'}]}))
            result = subprocess.run([sys.executable, '-m', 'holdem', '--list-models'],
                                    env=dict(os.environ, CODEX_HOME=folder), cwd=ROOT,
                                    capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('test-fast', result.stdout)
        self.assertNotIn('HOLD', result.stdout)

    def test_default_table_has_player_and_eight_codex_opponents(self):
        args = parser().parse_args([])
        self.assertEqual(args.players, 9)
        app = Application(args)
        self.assertEqual(len(app.players), 9)
        self.assertEqual(len({p.name for p in app.players}), 9)
        self.assertIsNone(app.agent.local)

    def test_retry_uses_same_cards_and_turn_without_local_actions(self):
        class RetryUI(HeadlessUI):
            def agent_error(self, app, hand, message):
                return True

        app = Application(parser().parse_args(['--headless', '--players', '2', '--hands', '1']))
        app.agent.missing_binary = False
        with patch.object(app.agent.codex, 'decide', side_effect=[TimeoutError('temporary connection error'),
                                                               Decision(Action('fold'), 'codex')]) as decide:
            result = app.run(RetryUI())
        self.assertEqual(decide.call_count, 2)
        self.assertEqual(decide.call_args_list[0].args[0], decide.call_args_list[1].args[0])
        self.assertEqual(result['decisions'], {'codex': 1})
        self.assertEqual(result['error'], '')
        self.assertEqual(result['hands_played'], 1)

    def test_next_hand_receives_public_stats_and_specific_role(self):
        app = Application(parser().parse_args(['--headless', '--hands', '2']))
        app.agent.missing_binary = False
        with patch.object(app.agent.codex, 'decide', return_value=Decision(Action('fold'), 'codex')) as decide:
            result = app.run(HeadlessUI())
        self.assertEqual(result['hands_played'], 2)
        roles = {call.args[1] for call in decide.call_args_list}
        self.assertEqual(roles, {'you', 'nova', 'blaze', 'echo', 'moss', 'atlas', 'jade', 'raven', 'orbit'})
        second_hand = [call.args[0] for call in decide.call_args_list if call.args[0]['hand'] == 2]
        self.assertTrue(second_hand)
        for obs in second_hand:
            self.assertEqual(len(obs['opponent_stats']), 9)
            self.assertTrue(all(row['hands'] == 1 for row in obs['opponent_stats']))
            self.assertTrue(all('hole' not in row for row in obs['opponent_stats']))

    def test_codex_failure_stops_with_nonzero_exit_and_no_fake_actions(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / 'codex'
            binary.write_text(f'#!{sys.executable}\nraise SystemExit(7)\n')
            binary.chmod(0o755)
            env = dict(os.environ, PATH=folder + os.pathsep + os.environ['PATH'])
            result = subprocess.run([sys.executable, '-m', 'holdem', '--headless', '--hands', '1'],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['hands_played'], 0)
            self.assertEqual(data['decisions'], {})
            self.assertTrue(data['error'])
            self.assertNotIn('LOCAL', data['agent'])
    def run_game(self, *args, stdin=''):
        return subprocess.run([sys.executable, '-m', 'holdem', *args], input=stdin, text=True,
                              capture_output=True, cwd=ROOT, timeout=25)

    def test_headless_complete_game_and_chip_conservation(self):
        result = self.run_game('--agent', 'local', '--watch', '--headless', '--hands', '12', '--seed', '42')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertGreater(data['hands_played'], 0)
        self.assertLessEqual(data['hands_played'], 12)
        self.assertEqual(len(data['players']), 9)
        self.assertEqual(sum(p['stack'] for p in data['players']), 18000)
        self.assertGreater(data['decisions']['local'], 0)

    def test_plain_interactive_quit_and_eof(self):
        for stdin in ['q\n', '']:
            result = self.run_game('--agent', 'local', '--plain', stdin=stdin)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('HOLD', result.stdout)
            self.assertNotIn('Traceback', result.stderr)

    def test_plain_model_switch_without_automatic_menu_for_piped_input(self):
        result = self.run_game('--agent', 'codex', '--plain', '--players', '2',
                               stdin='m\ntest-fast-model\nq\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index('HOLD'), result.stdout.index('选择 Codex 模型'))
        self.assertIn('已切换到 test-fast-model', result.stdout)
        self.assertIn('CODEX / test-fast-model', result.stdout)

    def test_reject_invalid_cli_values_without_traceback(self):
        for args in [('--players', '1'), ('--players', '10'), ('--stack', '-1'),
                     ('--big-blind', '0'), ('--delay', '-1'), ('--timeout', '0'),
                     ('--delay', 'nan'), ('--timeout', 'inf'), ('--hands', '-1'),
                     ('--model', 'bad model')]:
            result = self.run_game(*args)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn('Traceback', result.stderr)

    def test_headless_seed_reproducibility(self):
        args = ('--agent', 'local', '--headless', '--hands', '3', '--seed', '8')
        self.assertEqual(self.run_game(*args).stdout, self.run_game(*args).stdout)


from holdem.ui import clip, parse_action, frame_lines, cards_text
from holdem.engine import Hand, Player, Action


class UITests(unittest.TestCase):
    def test_input_commands_and_raise_total(self):
        self.assertEqual(parse_action('r 120', {'actions':['raise']}), Action('raise', 120))
        self.assertEqual(parse_action('c', {'actions':['check']}), Action('check'))
        self.assertEqual(parse_action('c', {'actions':['call']}), Action('call'))
        self.assertEqual(parse_action('a', {'actions':['allin']}), Action('allin'))
        for text in ['r foo', 'r', 'help', 'r 12.5']:
            with self.assertRaises(ValueError):
                parse_action(text, {'actions':['raise']})

    def test_clips_wide_chinese_characters_by_terminal_cells(self):
        self.assertEqual(clip('牌桌 ABC', 5), '牌桌 ')
        self.assertEqual(clip('牌桌 ABC', 1), '')

    def test_ui_conceals_live_opponents_and_boss_hides_all_game_state(self):
        hand = Hand([Player('YOU', 500), Player('NOVA', 500)])
        hand.players[0].hole = ['2s', '3s']
        hand.players[1].hole = ['As', 'Ah']
        normal = '\n'.join(frame_lines(hand, 100, 'LOCAL', False, False, 'YOUR TURN'))
        self.assertNotIn('A♠', normal)
        self.assertNotIn('A♥', normal)
        self.assertIn('2♠', normal)
        boss = '\n'.join(frame_lines(hand, 100, 'LOCAL', False, True, 'YOUR TURN'))
        for private in ['HOLD', 'YOU', 'NOVA', 'LOCAL', '2♠', '筹码', '牌', 'YOUR TURN']:
            self.assertNotIn(private, boss)
        self.assertIn('service', boss)

    def test_small_terminal_shows_all_seats_and_result(self):
        hand = Hand([Player('SEAT' + str(i), 500) for i in range(9)])
        active = frame_lines(hand, 72, 'CODEX', False, False, '', height=24)
        self.assertLessEqual(len(active), 18)
        for player in hand.players:
            self.assertIn(player.name, '\n'.join(active))
        while not hand.done:
            hand.act(Action('fold'))
        lines = frame_lines(hand, 80, 'LOCAL', False, False, '', height=24)
        self.assertLessEqual(len(lines), 18)
        for player in hand.players:
            self.assertIn(player.name, '\n'.join(lines))
        self.assertIn(hand.results[0], '\n'.join(lines))

    def test_finished_nine_seat_table_reveals_folded_holes_and_full_review_board(self):
        hand = Hand([Player('SEAT' + str(i), 500) for i in range(9)])
        while not hand.done:
            hand.act(Action('fold'))
        for height in [None, 24]:
            with self.subTest(height=height):
                lines = frame_lines(hand, 72, 'CODEX', False, False, '', height=height)
                text = '\n'.join(clip(line, 71) for line in lines)
                for player in hand.players:
                    self.assertIn(cards_text(player.hole), text)
                for card in hand.review_board:
                    self.assertIn(cards_text([card])[1:-1], text)
                self.assertNotIn('[··]', text)
                self.assertNotIn('│  ·  │', text)
                self.assertIn('补牌未参与结算', text)
                self.assertIn('*', text)
                self.assertIn('本手已结算', text)
                self.assertNotIn('当前下注', text)
        hidden = '\n'.join(frame_lines(hand, 72, 'CODEX', False, True, '', height=24))
        self.assertNotIn('SEAT', hidden)
        self.assertNotIn('公共牌', hidden)

    def test_showdown_reveals_folded_opponents_and_marks_empty_seats(self):
        hand = Hand([Player(name, stack) for name, stack in
                     [('YOU', 500), ('NOVA', 500), ('ECHO', 500), ('GONE', 0)]])
        hand.act(Action('call'))
        hand.act(Action('fold'))  # NOVA still reveals at the end.
        while not hand.done:
            hand.act(Action('check' if 'check' in hand.legal()['actions'] else 'call'))
        text = '\n'.join(frame_lines(hand, 100, 'CODEX', False, False, ''))
        self.assertIn(cards_text(hand.players[1].hole) + '  弃牌', text)
        self.assertIn('—  已出局', text)
        self.assertNotIn('[··]', text)
        self.assertNotIn('补牌', text)
