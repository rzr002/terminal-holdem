from pathlib import Path
import random
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from holdem.engine import Action, Hand, Player
from holdem.agents import CodexAgent, LocalAgent, ResilientAgent, parse_decision


class AgentTests(unittest.TestCase):
    def view(self):
        return Hand([Player('YOU', 500), Player('NOVA', 500)], rng=random.Random(7)).observation(0)

    def executable(self, folder, body):
        path = Path(folder) / 'fake-codex'
        path.write_text(f'#!{sys.executable}\n' + body)
        path.chmod(0o755)
        return str(path)

    def test_structured_action_validation(self):
        obs = self.view()
        self.assertEqual(parse_decision({'action': 'raise', 'amount': 20, 'reason': 'value'}, obs), Action('raise', 20))
        for result in [{'action': 'check', 'amount': 0}, {'action': 'raise', 'amount': 11},
                       {'action': 'raise', 'amount': True}, {'action': 'raise', 'amount': 900},
                       {'action': 'dance'}, [], None]:
            with self.assertRaises(ValueError):
                parse_decision(result, obs)

    def test_real_subprocess_contract_reads_final_json(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, '''import sys, json
from pathlib import Path
args = sys.argv
assert args[args.index('--sandbox') + 1] == 'read-only'
assert '--ephemeral' in args and '--ignore-user-config' in args
prompt = sys.stdin.read()
assert 'hole' in prompt and 'legal' in prompt
schema = json.loads(Path(args[args.index('--output-schema')+1]).read_text())
assert schema['additionalProperties'] is False
Path(args[args.index('-o') + 1]).write_text(json.dumps({'action':'call','amount':0,'reason':'pot odds'}))
''')
            decision = CodexAgent(binary=binary, timeout=3).decide(self.view(), 'balanced')
            self.assertEqual(decision.action, Action('call'))
            self.assertEqual(decision.source, 'codex')

    def test_codex_failure_and_illegal_output_never_use_local_strategy(self):
        for body in ["raise SystemExit(7)", '''import sys
from pathlib import Path
Path(sys.argv[sys.argv.index('-o')+1]).write_text('{"action":"raise","amount":99999}')
''']:
            with tempfile.TemporaryDirectory() as folder:
                binary = self.executable(folder, body)
                agent = ResilientAgent('codex', binary=binary, timeout=2, rng=random.Random(1))
                with patch.object(LocalAgent, 'decide', side_effect=AssertionError('Local strategy must not run')):
                    with self.assertRaises(RuntimeError):
                        agent.decide(self.view(), 'balanced')
                self.assertTrue(agent.failure)
                self.assertNotIn('LOCAL', agent.label)

    def test_codex_receives_role_skill_analysis_and_only_its_own_cards(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, '''import sys, json
from pathlib import Path
prompt = sys.stdin.read()
assert 'poker-core' in prompt and 'poker-nova' in prompt
analysis = json.loads(prompt.split('STRATEGY_ANALYSIS:\\n')[1].split('\\nOBSERVATION:')[0])
assert 'range_equity' in analysis and 'pot_odds' in analysis
obs = json.loads(prompt.split('OBSERVATION:\\n')[1])
assert all('hole' not in p for p in obs['players'])
assert 'deck' not in obs and 'review_board' not in obs
Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{"action":"call","amount":0}')
''')
            decision = CodexAgent(binary=binary, timeout=3).decide(self.view(), 'nova')
            self.assertEqual(decision.source, 'codex')

    def test_system_proxy_is_passed_to_codex_without_changing_parent_environment(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, '''import os, sys
from pathlib import Path
assert os.environ['HTTPS_PROXY'] == 'http://127.0.0.1:8899'
Path(sys.argv[sys.argv.index('-o')+1]).write_text('{"action":"call","amount":0}')
''')
            before = dict(os.environ)
            with patch('urllib.request.getproxies', return_value={'https': 'http://127.0.0.1:8899'}):
                decision = CodexAgent(binary=binary).decide(self.view())
            self.assertEqual(decision.source, 'codex')
            self.assertEqual(dict(os.environ), before)

    def test_missing_codex_does_not_substitute_local_agent(self):
        with patch('shutil.which', return_value=None), patch.object(LocalAgent, 'decide', side_effect=AssertionError):
            agent = ResilientAgent('codex')
            with self.assertRaises(RuntimeError):
                agent.decide(self.view())

    def test_timeout_and_cancellation(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, 'import time\ntime.sleep(10)\n')
            with self.assertRaises(TimeoutError):
                CodexAgent(binary=binary, timeout=0.15).decide(self.view(), 'balanced')
            event = threading.Event()
            event.set()
            with self.assertRaises(InterruptedError):
                CodexAgent(binary=binary).decide(self.view(), 'balanced', cancel=event)

    def test_cancelling_running_request_terminates_the_process(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, 'import time\ntime.sleep(10)\n')
            event = threading.Event()
            timer = threading.Timer(0.15, event.set)
            timer.start()
            try:
                with self.assertRaises(InterruptedError):
                    CodexAgent(binary=binary, timeout=2).decide(self.view(), cancel=event)
            finally:
                timer.join()

    def test_large_skill_prompt_cannot_block_timeout_when_child_does_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = self.executable(folder, 'import time\ntime.sleep(2)\n')
            obs = self.view()
            obs['history'] = ['公开行动记录 ' * 1000] * 30
            with self.assertRaises(TimeoutError):
                CodexAgent(binary=binary, timeout=0.1).decide(obs, 'nova')

    def test_local_agent_only_chooses_legal_actions_across_full_hands(self):
        rng = random.Random(23)
        agent = LocalAgent(rng=rng, samples=8)
        for n in range(2, 10):
            hand = Hand([Player(str(i), rng.randint(10, 300)) for i in range(n)], rng=rng)
            initial = sum(p.stack for p in hand.players) + hand.pot
            while not hand.done:
                result = agent.decide(hand.observation(hand.actor), 'aggressive')
                hand.act(result.action)
            self.assertEqual(sum(p.stack for p in hand.players), initial)
