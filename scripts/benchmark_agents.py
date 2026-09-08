"""Same-deal seat rotations for the offline opponent; never calls a paid model."""

import argparse
import json
from pathlib import Path
import random
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from holdem.agents import LocalAgent, StrategicAgent
from holdem.engine import Hand, Player


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deals', type=int, default=20)
    parser.add_argument('--players', type=int, choices=range(2, 10), default=9)
    parser.add_argument('--samples', type=int, default=64)
    parser.add_argument('--seed', type=int, default=20260908)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.deals < 2 or args.samples < 1:
        parser.error('deals >= 2 and samples >= 1 are required')
    started = time.monotonic()
    blocks = []
    decisions = 0
    for deal in range(args.deals):
        returns = []
        for hero in range(args.players):
            hand = Hand([Player(f'P{i}', 1000) for i in range(args.players)],
                        small_blind=5, big_blind=10, rng=random.Random(args.seed + deal))
            agents = [LocalAgent(random.Random(args.seed + deal * 100 + i), args.samples)
                      for i in range(args.players)]
            agents[hero] = StrategicAgent(random.Random(args.seed + deal * 100 + hero), args.samples)
            while not hand.done:
                seat = hand.actor
                hand.act(agents[seat].decide(hand.observation(seat), 'balanced').action)
                decisions += 1
            assert sum(p.stack for p in hand.players) == args.players * 1000
            returns.append((hand.players[hero].stack - 1000) / 10)
        blocks.append(statistics.mean(returns))
    # Rotations of one deal are correlated; use the deal, not the individual hand,
    # as the unit of uncertainty. Normal intervals are descriptive for small samples.
    mean = statistics.mean(blocks)
    margin = 1.96 * statistics.stdev(blocks) / len(blocks) ** 0.5
    result = {'candidate': 'strategic', 'opponents': 'legacy local / balanced',
              'players': args.players, 'independent_deals': args.deals,
              'seat_rotations_per_deal': args.players, 'hands': args.deals * args.players,
              'samples_per_agent_decision': args.samples, 'seed': args.seed,
              'bb_per_100': round(mean * 100, 2),
              'approx_95pct_interval_bb_per_100': [round((mean - margin) * 100, 2), round((mean + margin) * 100, 2)],
              'decisions': decisions, 'elapsed_seconds': round(time.monotonic() - started, 2),
              'limitations': 'Small synthetic benchmark versus one heuristic baseline; '
                             'not a Codex benchmark, exploitability estimate, or professional-strength claim.'}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
