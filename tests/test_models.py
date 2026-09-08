import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from holdem.__main__ import Application, parser
from holdem.agents import Decision
from holdem.engine import Action
from holdem.models import available_models, resolve_model_choice
from holdem.ui import HeadlessUI, PlainUI


class ModelTests(unittest.TestCase):
    def test_cache_uses_visible_cli_models_in_priority_order(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_HOME=folder):
            Path(folder, 'models_cache.json').write_text(json.dumps({'models': [
                {'slug': 'test-large', 'visibility': 'list', 'priority': 5},
                {'slug': 'test-fast', 'visibility': 'list', 'priority': 1,
                 'supported_in_api': False, 'description': 'Fast model'},
                {'slug': 'internal', 'visibility': 'hide'},
                {'slug': 'bad\nname', 'visibility': 'list'},
                {'slug': 'test-fast', 'visibility': 'list'}, None,
            ]}))
            options = available_models('custom-model')
            self.assertEqual([o.model for o in options], ['custom-model', 'test-fast', 'test-large'])
            self.assertEqual(options[1].description, 'Fast model')

    def test_missing_or_malformed_cache_allows_current_and_manual_model(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_HOME=folder):
            for content in [None, '{', '[]', '{"models":null}', '{"models":{}}']:
                if content is not None:
                    Path(folder, 'models_cache.json').write_text(content)
                self.assertEqual(available_models(), [])
                self.assertEqual([o.model for o in available_models('existing')], ['existing'])
                self.assertEqual(resolve_model_choice('provider/new-fast', [], None), 'provider/new-fast')

    def test_menu_choices_validate_numbers_names_and_keep_current(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_HOME=folder):
            options = available_models('test-current')
        self.assertEqual(resolve_model_choice('', options, 'test-current'), 'test-current')
        self.assertEqual(resolve_model_choice('1', options, None), 'test-current')
        for value in ['2', '0', '-1', 'bad name', 'bad\x1bname']:
            with self.assertRaises(ValueError):
                resolve_model_choice(value, options, None)

    def test_plain_picker_accepts_number_and_manual_name(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, CODEX_HOME=folder):
            for answer, expected in [('1', 'test-current'), ('my-fast-model', 'my-fast-model')]:
                with patch('builtins.input', return_value=answer), patch('builtins.print'):
                    self.assertEqual(PlainUI().pick_model('test-current'), expected)

    def test_switch_during_decision_takes_effect_on_next_request(self):
        app = Application(parser().parse_args([
            '--headless', '--hands', '1', '--players', '2', '--model', 'test-original']))
        app.agent.missing_binary = False
        release = threading.Event()
        seen = []
        test = self

        def decide(obs, *args):
            if not seen:
                self.assertTrue(release.wait(3))
            seen.append(app.agent.codex.model)
            return Decision(Action('check' if 'check' in obs['legal']['actions'] else 'call'), 'codex')

        class SwitchingUI(HeadlessUI):
            def wait_decision(self, app, hand, future):
                if not release.is_set():
                    try:
                        app.request_model('test-fast')
                        test.assertEqual(app.agent.codex.model, 'test-original')
                        test.assertEqual(app.pending_model, 'test-fast')
                    finally:
                        release.set()
                return future.result()

        with patch.object(app.agent.codex, 'decide', side_effect=decide):
            report = app.run(SwitchingUI())
        self.assertEqual(seen[0], 'test-original')
        self.assertTrue(all(model == 'test-fast' for model in seen[1:]))
        self.assertGreater(len(seen), 1)
        self.assertEqual(report['hands_played'], 1)
        self.assertEqual(sum(p['stack'] for p in report['players']), 4000)
        self.assertEqual(report['agent'], 'CODEX / test-fast')

    def test_switch_after_error_retries_same_turn_with_new_model(self):
        app = Application(parser().parse_args([
            '--headless', '--hands', '1', '--players', '2', '--model', 'test-original']))
        app.agent.missing_binary = False
        seen = []

        def decide(obs, *args):
            seen.append((app.agent.codex.model, obs))
            if len(seen) == 1:
                raise TimeoutError('slow model')
            return Decision(Action('fold'), 'codex')

        test = self

        class RetryUI(HeadlessUI):
            def agent_error(self, app, hand, message):
                app.request_model('test-fast')
                test.assertTrue(app.error)  # Keep the retry controls until the retry succeeds.
                return True

        with patch.object(app.agent.codex, 'decide', side_effect=decide):
            report = app.run(RetryUI())
        self.assertEqual([model for model, _ in seen], ['test-original', 'test-fast'])
        self.assertEqual(seen[0][1], seen[1][1])
        self.assertEqual(report['error'], '')
