"""Real Codex checks for affordable flop entry and defending repeated shoves."""

import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from holdem.agents import CodexAgent
from holdem.engine import Action, Hand, Player
from holdem.strategy import PROFILES


def main():
    cases = []
    for hero, hole in [(1, '9s 8s'), (2, 'Kh Td'), (3, '6c 6d'), (4, 'As 5s')]:
        hand = Hand([Player(p.name, 2000) for p in PROFILES], button=(hero - 4) % 9,
                    small_blind=10, big_blind=20, rng=random.Random(9))
        hand.act(Action('raise', 50))
        assert hand.actor == hero
        obs = hand.observation(hero)
        obs['hole'] = hole.split()
        cases.append(('cheap_flop', PROFILES[hero].key, obs, ['call', 'raise']))
    for hero, hole in [(5, 'As Qs'), (6, 'Jc Jh'), (7, 'Kh Qh'), (8, '9c 9d'), (1, '7c 2d')]:
        shover = (hero + 1) % 9
        stack = 400 if hero == 8 else 2000
        hand = Hand([Player(p.name, stack) for p in PROFILES], button=(hero - 2) % 9,
                    small_blind=10, big_blind=20, rng=random.Random(9))
        assert hand.actor == shover
        hand.act(Action('allin'))
        for _ in range(7):
            hand.act(Action('fold'))
        assert hand.actor == hero
        obs = hand.observation(hero)
        obs['hole'] = hole.split()
        obs['opponent_stats'] = [{'seat': shover, 'hands': 4, 'shove_hands': 3}]
        expected = ['fold'] if hero == 1 else ['call', 'allin']
        cases.append(('repeat_shove_trash' if hero == 1 else 'repeat_shove_defense', PROFILES[hero].key, obs, expected))
    agent = CodexAgent(timeout=60, rng=random.Random(7))
    results = []
    report = {'model': agent.model, 'cases': results,
              'scope': 'Synthetic decisions using real Codex; verifies selected behaviors, not full-game frequencies.'}
    for name, role, obs, expected in cases:
        started = time.monotonic()
        decision = agent.decide(obs, role)
        result = {'case': name, 'role': role, 'hole': obs['hole'], 'action': decision.action.kind,
                  'amount': decision.action.amount, 'source': decision.source,
                  'passed': decision.action.kind in expected,
                  'elapsed_seconds': round(time.monotonic() - started, 2)}
        results.append(result)
        print(json.dumps(result), flush=True)
        Path('validation/codex-table-style-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
