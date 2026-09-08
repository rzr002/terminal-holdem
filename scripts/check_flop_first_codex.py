"""Real Codex preflop smoke test: all nine synthetic seats should see the flop.

Uses account quota. Stops as soon as three community cards have been dealt.
"""

import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from holdem.agents import CodexAgent
from holdem.engine import Hand, Player
from holdem.strategy import PROFILES


def main():
    hand = Hand([Player(p.name, 2000) for p in PROFILES], small_blind=10,
                big_blind=20, rng=random.Random(42))
    agent = CodexAgent(timeout=60, rng=random.Random(17))
    output = Path(__file__).resolve().parents[1] / 'validation/codex-flop-first-smoke.json'
    report = {'model': agent.model, 'seed': 42, 'decisions': [],
              'scope': 'Synthetic nine-seat Codex preflop check; not a win-rate benchmark.'}
    started = time.monotonic()
    try:
        while not hand.done and hand.street == 'preflop':
            if len(report['decisions']) >= 24:
                raise RuntimeError('Preflop action bound exceeded')
            role = PROFILES[hand.actor]
            obs = hand.observation(hand.actor)
            decision = agent.decide(obs, role.key)
            hand.act(decision.action)
            row = {'role': role.key, 'hole': obs['hole'], 'action': decision.action.kind,
                   'amount': decision.action.amount, 'source': decision.source}
            report['decisions'].append(row)
            print(json.dumps(row), flush=True)
            output.write_text(json.dumps(report, indent=2) + '\n')
        report.update(board=hand.board, live_players=len(hand.live()),
                      total_chips=sum(p.stack for p in hand.players) + hand.pot,
                      elapsed_seconds=round(time.monotonic() - started, 2))
        report['passed'] = (hand.street == 'flop' and len(hand.board) == 3
                            and len(hand.live()) == 9 and report['total_chips'] == 18000
                            and all(r['source'] == 'codex' and r['action'] in ('check', 'call', 'raise')
                                    for r in report['decisions']))
    except Exception as error:
        report.update(passed=False, error=f'{type(error).__name__}: {error}')
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'decisions'}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
