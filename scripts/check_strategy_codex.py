"""Three synthetic strategy checks using the real, logged-in Codex CLI."""

import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from holdem.agents import CodexAgent
from holdem.engine import Hand, Player


def main():
    cases = []
    for name, role, hole, expected in [('early_trash', 'nova', ['7c', '2d'], ['fold']),
                                       ('early_aces', 'blaze', ['As', 'Ah'], ['raise'])]:
        hand = Hand([Player(f'P{i}', 1000) for i in range(9)], rng=random.Random(1))
        obs = hand.observation(hand.actor)
        obs['hole'] = hole
        cases.append((name, role, obs, expected))
    hand = Hand([Player(f'P{i}', 100) for i in range(3)])
    obs = hand.observation(0)
    obs.update(street='river', hole=['As', 'Ks'], board=['Qs', 'Js', 'Ts', '2d', '3c'],
               pot=200, current_bet=100, history=[], action_history=[])
    for player, stack, committed in zip(obs['players'], [5, 0, 0], [0, 100, 100]):
        player.update(stack=stack, committed=committed, bet=committed, folded=False)
    obs['legal'] = {'actions': ['fold', 'call', 'allin'], 'to_call': 5,
                    'min_raise_to': 200, 'max_raise_to': 5}
    cases.append(('short_stack_nuts', 'orbit', obs, ['call', 'allin']))
    agent = CodexAgent(timeout=60, rng=random.Random(7))
    results = []
    for name, role, obs, expected in cases:
        started = time.monotonic()
        decision = agent.decide(obs, role)
        result = {'case': name, 'role': role, 'action': decision.action.kind,
                  'amount': decision.action.amount, 'source': decision.source,
                  'passed': decision.action.kind in expected,
                  'elapsed_seconds': round(time.monotonic() - started, 2)}
        results.append(result)
        print(json.dumps(result), flush=True)
    report = {'model': agent.model, 'cases': results,
              'scope': 'Real Codex, loaded role skills and analysis; three synthetic sanity checks, not a strength benchmark.'}
    Path('validation/codex-strategy-smoke.json').write_text(json.dumps(report, indent=2) + '\n')
    return 0 if all(result['passed'] for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
