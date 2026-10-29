"""Adapt public terminal-holdem observations to Lucas's Fullhouse interface.

Only chip units, seat numbering and engine history are adapted. The vendored
v16_squeeze_C_metacap policy is unchanged; each seat owns its own statistics.
"""

import importlib.util
from pathlib import Path

from .agents import AgentError, Decision
from .engine import Action

BOT_PATH = Path(__file__).with_name('vendor') / 'lucas_v16' / 'bot.py'
STREETS = {'preflop': 0, 'flop': 1, 'turn': 2, 'river': 3}


def lucas_state(obs):
    # Compact *dealt-in* seats so upstream position charts still work after busts.
    players = [p for p in obs['players'] if p['dealt_in']]
    seats = {p['seat']: i for i, p in enumerate(players)}
    hero = obs['players'][obs['seat']]
    scale = 100 / obs['big_blind']
    chips = lambda amount: round(amount * scale)
    log = [{'seat': seats[e['seat']], 'action': e['action'], 'amount': chips(e['amount'])}
           for e in obs['blind_posts']]
    pot = sum(e['amount'] for e in obs['blind_posts'])
    for e in obs['action_history']:
        action = 'all_in' if e['action'] == 'raise' and e['all_in'] else e['action']
        log.append({'seat': seats[e['seat']], 'action': action,
                    'amount': chips(e['raise_to'] if e['action'] == 'raise' else e['paid']),
                    'street_idx': STREETS[e['street']], 'owed': chips(e['to_call_before']),
                    'pot_before': chips(pot)})
        pot += e['paid']
    legal = obs['legal']
    return {'hand_id': obs['hand'], 'seat_to_act': seats[obs['seat']],
            'street': obs['street'], 'your_cards': list(obs['hole']),
            'community_cards': list(obs['board']), 'pot': chips(obs['pot']),
            'your_stack': chips(hero['stack']), 'your_bet_this_street': chips(hero['bet']),
            'current_bet': chips(obs['current_bet']),
            'amount_owed': chips(legal.get('to_call', 0)),
            'can_check': 'check' in legal['actions'],
            'min_raise_to': chips(legal['min_raise_to']) if 'raise' in legal['actions'] else 0,
            'max_raise_to': chips(legal.get('max_raise_to', hero['stack'] + hero['bet'])),
            'players': [{'seat': seats[p['seat']], 'bot_id': f"seat-{p['seat']}",
                         'stack': chips(p['stack']), 'bet_this_street': chips(p['bet']),
                         'is_folded': p['folded'],
                         'state': 'folded' if p['folded'] else 'all_in' if p['stack'] == 0 else 'active'}
                        for p in players], 'action_log': log}


def reconstruct_log(log):
    """Use the engine's exact streets/prices, including short calls and side pots.

    Upstream guesses street boundaries by replaying a flat Fullhouse log. This
    engine already records them, so guessing would lose short-all-in semantics.
    """
    return [{k: e[k] for k in ('seat', 'street_idx', 'action', 'amount', 'owed', 'pot_before')}
            for e in log if e['action'] not in ('small_blind', 'big_blind')]


def legal_action(raw, obs):
    legal = obs['legal']
    kind = raw.get('action')
    kind = 'allin' if kind == 'all_in' else kind
    if kind == 'raise' and 'raise' in legal['actions']:
        amount = round(float(raw.get('amount', 0)) * obs['big_blind'] / 100)
        return Action('raise', min(legal['max_raise_to'], max(legal['min_raise_to'], amount)))
    if kind in legal['actions'] and kind != 'raise':
        return Action(kind)
    if kind in ('raise', 'allin', 'check', 'call'):
        # A short all-in may not reopen raising under standard NLHE rules.
        return Action('check' if 'check' in legal['actions'] else 'call')
    raise AgentError(f'Lucas v16 返回无法适配的动作：{raw!r}')


class LucasAgent:
    def __init__(self):
        self.sessions = {}

    def session(self, seat):
        if seat not in self.sessions:
            spec = importlib.util.spec_from_file_location(f'_lucas_v16_seat_{seat}', BOT_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not module._HAVE_EVAL7:
                raise ImportError('缺少 eval7；请使用项目 .venv，或安装 requirements-lucas.txt')
            module._reconstruct_hand = reconstruct_log
            self.sessions[seat] = module
        return self.sessions[seat]

    def decide(self, obs, personality='balanced', cancel=None):
        if cancel and cancel.is_set():
            raise InterruptedError('已取消')
        try:
            module = self.session(obs['seat'])
            raw = module.decide(lucas_state(obs))
            action = legal_action(raw, obs)
        except (ImportError, OSError, ValueError, TypeError, KeyError) as error:
            raise AgentError(f'Lucas v16 决策失败：{error}') from error
        if cancel and cancel.is_set():
            raise InterruptedError('已取消')
        return Decision(action, 'lucas')

    def observe(self, obs):
        # Folded seats still see the remaining public actions before the next hand.
        if obs['seat'] in self.sessions:
            self.sessions[obs['seat']].update_stats(lucas_state(obs))
