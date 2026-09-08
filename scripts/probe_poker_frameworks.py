"""Bounded research probe; does not install a game opponent or train full Hold'em.

Run in an optional environment with rlcard==1.2.0 and open_spiel==2.0.2.
Random games verify basic execution only. Kuhn learning does not imply NLHE skill.
"""

import argparse
from copy import deepcopy
from importlib.metadata import version
import json
from pathlib import Path
import platform
import random
import time


def rlcard_probe(hands):
    import numpy as np
    import rlcard

    results = []
    for players in (2, 6, 9):
        env = rlcard.make('no-limit-holdem', config={
            'game_num_players': players, 'chips_for_each': 200, 'seed': 31,
        })
        rng = random.Random(31)
        started = time.perf_counter()
        actions = 0
        for _ in range(hands):
            state, _ = env.reset()
            for _ in range(2000):
                if env.is_over():
                    break
                state, _ = env.step(rng.choice(list(state['legal_actions'])))
                actions += 1
            else:
                raise AssertionError('RLCard did not terminate within 2000 actions')
            assert np.isclose(sum(env.get_payoffs()), 0)
        results.append({'players': players, 'hands': hands, 'actions': actions,
                        'seconds': round(time.perf_counter() - started, 4),
                        'observation_shape': env.state_shape[0],
                        'actions_available': {a.name: a.value for a in env.actions},
                        'terminal_and_zero_sum': True})

    # Two strategically different information sets collapse to the same default
    # vector because private and community cards share a single 52-card mask.
    env.reset()
    while not env.game.public_cards:
        env.step(1)  # CHECK_CALL in the pinned 1.2.0 release.
    raw = env.get_state(env.get_player_id())['raw_obs']
    swapped = deepcopy(raw)
    swapped['hand'][0], swapped['public_cards'][0] = (
        swapped['public_cards'][0], swapped['hand'][0])
    same = np.array_equal(env._extract_state(raw)['obs'],
                          env._extract_state(swapped)['obs'])
    return {'version': version('rlcard'), 'random_games': results,
            'private_board_swap_has_identical_default_vector': bool(same),
            'scope': 'Default vector only; raw_obs and action_record have more information.'}


def holdem_parameters(players, abstraction):
    return {'betting': 'nolimit', 'numPlayers': players, 'numRounds': 4,
            'stack': ' '.join(['2000'] * players),
            'blind': '20 10' if players == 2 else ' '.join(['10', '20'] + ['0'] * (players - 2)),
            'firstPlayer': '2 1 1 1' if players == 2 else '3 1 1 1',
            'numSuits': 4, 'numRanks': 13, 'numHoleCards': 2,
            'numBoardCards': '0 3 1 1', 'bettingAbstraction': abstraction}


def openspiel_probe(hands):
    import pyspiel

    results = []
    for players in (2, 6, 9):
        for abstraction in ('fchpa', 'fullgame'):
            params = holdem_parameters(players, abstraction)
            game = pyspiel.load_game('universal_poker', params)
            rng = random.Random(31)
            started = time.perf_counter()
            decisions = 0
            for _ in range(hands):
                state = game.new_initial_state()
                for _ in range(2000):
                    if state.is_terminal():
                        break
                    if state.is_chance_node():
                        outcomes, probabilities = zip(*state.chance_outcomes())
                        action = rng.choices(outcomes, weights=probabilities)[0]
                    else:
                        action = rng.choice(state.legal_actions())
                        decisions += 1
                    state.apply_action(action)
                else:
                    raise AssertionError('OpenSpiel did not terminate within 2000 steps')
                assert abs(sum(state.returns())) < 1e-8
            results.append({'players': players, 'hands': hands, 'decisions': decisions,
                            'seconds': round(time.perf_counter() - started, 4),
                            'parameters': params,
                            'num_distinct_actions': game.num_distinct_actions(),
                            'information_state_tensor_shape': game.information_state_tensor_shape(),
                            'terminal_and_zero_sum': True})
    return {'version': version('open_spiel'), 'random_games': results}


def learning_probe(iterations):
    import numpy as np
    import pyspiel
    from open_spiel.python.algorithms import exploitability, external_sampling_mccfr

    np.random.seed(31)
    game = pyspiel.load_game('kuhn_poker', {'players': 2})
    solver = external_sampling_mccfr.ExternalSamplingSolver(game)
    checkpoints = [{'iterations': 0, 'exploitability':
                    exploitability.exploitability(game, solver.average_policy())}]
    started = time.perf_counter()
    for iteration in range(1, iterations + 1):
        solver.iteration()
        if iteration in {100, 1000, iterations}:
            checkpoints.append({'iterations': iteration, 'exploitability':
                                exploitability.exploitability(game, solver.average_policy())})
    return {'game': 'two-player Kuhn poker', 'algorithm': 'external-sampling MCCFR',
            'seed': 31, 'seconds': round(time.perf_counter() - started, 4),
            'checkpoints': checkpoints,
            'improved': bool(checkpoints[-1]['exploitability'] < checkpoints[0]['exploitability']),
            'scope': 'Tiny-game learning demonstration only; no trained full-Holdem opponent exported.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hands', type=int, default=100)
    parser.add_argument('--iterations', type=int, default=5000)
    parser.add_argument('--output', type=Path, default=Path('validation/poker-framework-probe.json'))
    args = parser.parse_args()
    if not 1 <= args.hands <= 1000 or not 1 <= args.iterations <= 10000:
        parser.error('Research bounds: hands 1–1000; iterations 1–10000')
    report = {'python': platform.python_version(), 'machine': platform.machine(),
              'scope': 'Research probe, not an integration or strength benchmark.'}
    for name, run in [('rlcard', lambda: rlcard_probe(args.hands)),
                      ('openspiel', lambda: openspiel_probe(args.hands)),
                      ('learning', lambda: learning_probe(args.iterations))]:
        try:
            report[name] = run()
        except Exception as error:
            report[name] = {'error': f'{type(error).__name__}: {error}'}
        print(json.dumps({name: report[name]}), flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    return 1 if any('error' in report[name] for name in ('rlcard', 'openspiel', 'learning')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
