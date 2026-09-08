"""Public-information poker analysis; heuristic ranges, never solver/GTO claims."""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .engine import DECK, RANKS, evaluate

SKILLS_ROOT = Path(__file__).resolve().parents[1] / 'skills'


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    style: str
    legacy: str
    openness: float = 0
    aggression: float = 0
    bluff: float = 0.03


PROFILES = (
    Profile('you', 'YOU', '均衡', 'balanced'),
    Profile('nova', 'NOVA', '紧凶', 'tight', -0.03, -0.02, 0.02),
    Profile('blaze', 'BLAZE', '施压', 'aggressive', 0.02, 0.06, 0.06),
    Profile('echo', 'ECHO', '混合', 'tricky', 0.01, 0.02, 0.05),
    Profile('moss', 'MOSS', '深筹', 'loose', 0.02, 0, 0.02),
    Profile('atlas', 'ATLAS', '均衡', 'balanced', 0, 0, 0.03),
    Profile('jade', 'JADE', '读人', 'tight', -0.01, 0.01, 0.03),
    Profile('raven', 'RAVEN', '阻断', 'aggressive', 0.01, 0.04, 0.05),
    Profile('orbit', 'ORBIT', '短筹', 'tricky', 0, 0.02, 0.03),
)


def profile_for(key):
    key = {'balanced': 'you', 'tight': 'nova', 'aggressive': 'blaze',
           'tricky': 'echo', 'loose': 'moss'}.get(key, key)
    return next((p for p in PROFILES if p.key == key), PROFILES[0])


@lru_cache(maxsize=16)
def skill_text(key):
    profile = profile_for(key)
    return '\n\n'.join((SKILLS_ROOT / name / 'SKILL.md').read_text(encoding='utf-8')
                        for name in ('poker-core', f'poker-{profile.key}'))


def positions(obs):
    seats = [p['seat'] for p in obs['players']
             if p.get('dealt_in', p['stack'] + p['committed'] > 0)]
    ordered = sorted(seats, key=lambda s: (s - obs['button']) % len(obs['players']))
    early = {2: [], 3: [], 4: ['CO'], 5: ['HJ', 'CO'], 6: ['UTG', 'HJ', 'CO'],
             7: ['UTG', 'LJ', 'HJ', 'CO'], 8: ['UTG', 'MP', 'LJ', 'HJ', 'CO'],
             9: ['UTG', 'UTG+1', 'MP', 'LJ', 'HJ', 'CO']}
    labels = ['BTN', 'BB'] if len(ordered) == 2 else ['BTN', 'SB', 'BB'] + early[len(ordered)]
    return dict(zip(ordered, labels))


def preflop_strength(hole):
    """A starting-hand ordering, NOT a win probability or solved range."""
    hi, lo = sorted((RANKS.index(c[0]) + 2 for c in hole), reverse=True)
    if hi == lo:
        return min(1, 0.50 + (hi - 2) * 0.041)
    score = (hi + lo - 4) / 24 * 0.60 + (hi >= 13) * 0.10 + (hi == 14) * 0.10
    score += (hole[0][1] == hole[1][1]) * 0.08
    score += 0.07 if hi - lo == 1 else (0.02 if hi - lo == 2 else 0)
    return min(1, score)


def has_draw(hole, board):
    if not 3 <= len(board) < 5:
        return False
    cards = hole + board
    suits = Counter(c[1] for c in cards)
    if any(count == 4 and any(c[1] == suit for c in hole) for suit, count in suits.items()):
        return True
    ranks = {RANKS.index(c[0]) + 2 for c in cards}
    own = {RANKS.index(c[0]) + 2 for c in hole}
    board_ranks = {RANKS.index(c[0]) + 2 for c in board}
    for values in (ranks, own, board_ranks):
        if 14 in values:
            values.add(1)
    return any(len(ranks & set(range(high - 4, high + 1))) == 4 and
               bool((own - board_ranks) & set(range(high - 4, high + 1))) for high in range(5, 15))


def range_weight(hole, obs, seat):
    """Approximate action-conditioned combo weights. Every combo keeps nonzero mass."""
    events = [e for e in obs.get('action_history', []) if e['seat'] == seat]
    preflop = [e for e in events if e['street'] == 'preflop']
    raises = sum(e['action'] == 'raise' for e in preflop)
    calls = any(e['action'] == 'call' for e in preflop)
    weight = 1.0
    if raises or calls:
        threshold = min(0.86, 0.68 + 0.06 * (raises - 1)) if raises else 0.45
        if positions(obs).get(seat) in ('BTN', 'CO', 'SB'):
            threshold -= 0.06
        strength = preflop_strength(hole)
        weight = 1.0 if strength >= threshold else max(0.035, 0.35 - (threshold - strength))
    for event in events:
        if event['street'] == 'preflop' or event['action'] not in ('raise', 'call'):
            continue
        count = {'flop': 3, 'turn': 4, 'river': 5}[event['street']]
        board = obs['board'][:count]
        if len(board) < 3:
            continue
        category = evaluate(hole + board)[0]
        made_or_draw = category >= 2 or has_draw(hole, board)
        likelihood = 0.95 if made_or_draw else (0.60 if category == 1 else 0.15)
        if event['action'] == 'raise' and category == 0 and not made_or_draw:
            likelihood = 0.07
        weight *= likelihood
    return max(0.02, weight)


def _eligible_pots(obs, cost):
    contributions = {p['seat']: p['committed'] + (cost if p['seat'] == obs['seat'] else 0)
                     for p in obs['players']}
    live = {p['seat'] for p in obs['players'] if not p['folded']}
    pots, previous = [], 0
    for level in sorted(set(contributions.values()) - {0}):
        contributors = {s for s, amount in contributions.items() if amount >= level}
        if obs['seat'] in contributors:
            pots.append(((level - previous) * len(contributors), contributors & live))
        previous = level
    return pots


def analyze(obs, rng, samples=192, cancel=None):
    if samples < 1:
        raise ValueError('抽样次数必须大于零')
    if cancel and cancel.is_set():
        raise InterruptedError('已取消')
    seat = obs['seat']
    hero = next(p for p in obs['players'] if p['seat'] == seat)
    opponents = [p['seat'] for p in obs['players'] if p['seat'] != seat and not p['folded']]
    known = obs['hole'] + obs['board']
    unseen = [c for c in DECK if c not in known]
    cost = obs['legal'].get('to_call', 0)
    pots = _eligible_pots(obs, cost)
    contestable = sum(amount for amount, _ in pots)
    equity = payout = 0.0
    # Cache weights for sampled combinations within this decision only.
    weights = {}
    for _ in range(samples):
        if cancel and cancel.is_set():
            raise InterruptedError('已取消')
        remaining = unseen.copy()
        holes = {}
        order = opponents.copy()
        rng.shuffle(order)
        for other in order:
            # Bounded rejection sampling keeps the UI responsive. The final proposal
            # is accepted at the cap, so these are approximate heuristic ranges.
            for attempt in range(12):
                pair = rng.sample(remaining, 2)
                key = (other, *sorted(pair))
                if key not in weights:
                    weights[key] = range_weight(pair, obs, other)
                if rng.random() < weights[key] or attempt == 11:
                    break
            holes[other] = pair
            for card in pair:
                remaining.remove(card)
        board = obs['board'] + rng.sample(remaining, 5 - len(obs['board']))
        scores = {seat: evaluate(obs['hole'] + board)}
        scores.update({other: evaluate(pair + board) for other, pair in holes.items()})
        best = max(scores.values())
        if scores[seat] == best:
            equity += 1 / sum(score == best for score in scores.values())
        for amount, eligible in pots:
            best = max(scores[s] for s in eligible)
            if scores[seat] == best:
                payout += amount / sum(scores[s] == best for s in eligible)
    positions_by_seat = positions(obs)
    after_button = sorted([seat] + opponents, key=lambda s: (s - obs['button'] - 1) % len(obs['players']))
    remaining_stack = max(0, hero['stack'] - cost)
    effective = max((min(remaining_stack, p['stack']) for p in obs['players']
                     if p['seat'] in opponents), default=0)
    legal = obs['legal']
    sizes = {}
    if 'raise' in legal['actions']:
        for label, fraction in [('small', 0.33), ('half', 0.5), ('large', 0.75), ('pot', 1)]:
            target = obs['current_bet'] + max(obs['big_blind'], round((obs['pot'] + cost) * fraction))
            sizes[label] = min(legal['max_raise_to'], max(legal['min_raise_to'], target))
    events = obs.get('action_history', [])
    return {'position': positions_by_seat.get(seat, 'unknown'),
            'in_position_postflop': after_button[-1] == seat,
            'live_opponents': len(opponents), 'preflop_strength': round(preflop_strength(obs['hole']), 4),
            'range_equity': round(equity / samples, 5), 'equity_samples': samples,
            'equity_model': 'heuristic action-weighted ranges; bounded Monte Carlo; not GTO',
            'eligible_pot_after_call': contestable, 'pot_odds': round(cost / max(1, contestable), 5),
            'call_ev_chips': round(payout / samples - cost, 2),
            'effective_stack_after_call': effective, 'spr': round(effective / max(1, contestable), 2),
            'has_draw': has_draw(obs['hole'], obs['board']),
            'preflop_raises': sum(e['action'] == 'raise' and e['street'] == 'preflop' for e in events),
            'raises_this_street': sum(e['action'] == 'raise' and e['street'] == obs['street'] for e in events),
            'bet_sizes_raise_to': sizes,
            'caveat': 'Call EV assumes no more betting; ranges and future folds are not solved. '
                      'Use stronger ranges and equity-realization discounts when facing pressure.'}


class PublicStats:
    """In-memory counters from public actions; never store hole cards or runouts."""

    def __init__(self):
        self.rows = {}

    def record(self, obs):
        for player in obs['players']:
            if not player.get('dealt_in', True):
                continue
            seat = player['seat']
            row = self.rows.setdefault(seat, Counter())
            events = [e for e in obs.get('action_history', []) if e['seat'] == seat]
            row['hands'] += 1
            row['vpip'] += any(e['street'] == 'preflop' and e['paid'] > 0 for e in events)
            row['pfr'] += any(e['street'] == 'preflop' and e['action'] == 'raise' for e in events)
            faced = [e for e in events if e['to_call_before'] > 0]
            row['faced_bets'] += len(faced)
            row['folds'] += sum(e['action'] == 'fold' for e in faced)
            row['bets_raises'] += sum(e['street'] != 'preflop' and e['action'] == 'raise' for e in events)
            row['calls'] += sum(e['street'] != 'preflop' and e['action'] == 'call' for e in events)

    def snapshot(self):
        return [{'seat': seat, 'hands': row['hands'],
                 'vpip_pct': round(100 * row['vpip'] / row['hands'], 1),
                 'pfr_pct': round(100 * row['pfr'] / row['hands'], 1),
                 'faced_bets': row['faced_bets'],
                 'fold_to_bet_pct': round(100 * row['folds'] / row['faced_bets'], 1) if row['faced_bets'] else None,
                 'postflop_aggression_factor': round(row['bets_raises'] / row['calls'], 2) if row['calls'] else None}
                for seat, row in sorted(self.rows.items())]
