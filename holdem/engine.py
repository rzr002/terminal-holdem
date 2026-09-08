"""No-limit Hold'em rules. No I/O and no agent access to the dealer's state."""

from collections import Counter
from dataclasses import dataclass, field
import random

RANKS = '23456789TJQKA'
SUITS = 'shdc'
DECK = [r + s for r in RANKS for s in SUITS]
MAX_PLAYERS = 9
HAND_NAMES = ('高牌', '一对', '两对', '三条', '顺子', '同花', '葫芦', '四条', '同花顺')
STREETS = ('preflop', 'flop', 'turn', 'river')
STREET_NAMES = dict(zip(STREETS, ('翻牌前', '翻牌', '转牌', '河牌')))


def _straight(ranks):
    values = set(ranks)
    if 14 in values:
        values.add(1)
    return next((hi for hi in range(14, 4, -1)
                 if all(r in values for r in range(hi - 4, hi + 1))), 0)


def evaluate(cards):
    """Comparable tuple for the best five of 5–7 cards; ace-low straights count."""
    if not 5 <= len(cards) <= 7 or len(set(cards)) != len(cards) or any(c not in DECK for c in cards):
        raise ValueError('需要 5–7 张不重复的有效牌')
    ranks = sorted((RANKS.index(c[0]) + 2 for c in cards), reverse=True)
    counts = Counter(ranks)
    flush = next((sorted((RANKS.index(c[0]) + 2 for c in cards if c[1] == s), reverse=True)
                  for s in SUITS if sum(c[1] == s for c in cards) >= 5), [])
    if flush and (high := _straight(flush)):
        return (8, high)
    quads = [r for r in counts if counts[r] == 4]
    trips = [r for r in counts if counts[r] >= 3]
    pairs = [r for r in counts if counts[r] >= 2]
    if quads:
        return (7, quads[0], max(r for r in ranks if r != quads[0]))
    if trips and any(r != trips[0] for r in pairs):
        return (6, trips[0], max(r for r in pairs if r != trips[0]))
    if flush:
        return (5, *flush[:5])
    if high := _straight(ranks):
        return (4, high)
    if trips:
        return (3, trips[0], *[r for r in ranks if r != trips[0]][:2])
    if len(pairs) >= 2:
        return (2, *pairs[:2], max(r for r in ranks if r not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[r for r in ranks if r != pairs[0]][:3])
    return (0, *ranks[:5])


@dataclass(frozen=True)
class Action:
    kind: str
    amount: int = 0  # raise TO this total on the current street, not BY this much


@dataclass
class Player:
    name: str
    stack: int
    hole: list = field(default_factory=list)
    bet: int = 0
    committed: int = 0
    folded: bool = False
    last_action: str = ''


class Hand:
    def __init__(self, players, button=0, small_blind=5, big_blind=10, rng=None, number=1):
        if not 2 <= len(players) <= MAX_PLAYERS:
            raise ValueError(f'需要 2–{MAX_PLAYERS} 个座位')
        if not 0 < small_blind <= big_blind:
            raise ValueError('盲注必须为正数，且小盲不大于大盲')
        if any(type(p.stack) is not int or p.stack < 0 for p in players):
            raise ValueError('筹码必须为非负整数')
        if not 0 <= button < len(players):
            raise ValueError('无效的庄家位置')
        self.players = players
        self.small_blind, self.big_blind = small_blind, big_blind
        self.number = number
        self.board, self.history, self.results = [], [], []
        self.review_board = []
        self.street = 'preflop'
        self.done = self.showdown = False
        self.actor = None
        self.current_bet = self.min_raise = big_blind
        self.acted_at = {}
        self.deck = DECK.copy()
        (rng or random.Random()).shuffle(self.deck)
        for p in players:
            p.bet = p.committed = 0
            p.hole = []
            p.folded = p.stack == 0
            p.last_action = ''
        live = self.live()
        if len(live) < 2:
            raise ValueError('至少两位玩家需要有筹码')
        self.button = button if button in live else self.next_seat(button, live)
        self.sb = self.button if len(live) == 2 else self.next_seat(self.button, live)
        self.bb = self.next_seat(self.sb, live)
        for _ in range(2):
            for i in self.ordered_after(self.button, live):
                players[i].hole.append(self.deck.pop())
        for i, amount, label in [(self.sb, small_blind, '小盲'), (self.bb, big_blind, '大盲')]:
            self._pay(i, min(amount, players[i].stack))
            players[i].last_action = label
            self.history.append(f'{players[i].name} {label} {players[i].bet}')
        self.pending = set(self.capable())
        self._progress(self.bb)

    @property
    def pot(self):
        return sum(p.committed for p in self.players)

    def live(self):
        return [i for i, p in enumerate(self.players) if not p.folded]

    def capable(self):
        return [i for i in self.live() if self.players[i].stack > 0]

    def ordered_after(self, seat, seats):
        return sorted(seats, key=lambda i: (i - seat - 1) % len(self.players))

    def next_seat(self, seat, seats):
        return self.ordered_after(seat, seats)[0]

    def next_button(self):
        funded = [i for i, p in enumerate(self.players) if p.stack]
        if len(funded) == 2:
            # When switching to heads-up, advance the big blind first.
            next_bb = self.next_seat(self.bb, funded)
            return self.next_seat(next_bb, funded)
        return self.next_seat(self.button, funded)

    def legal(self):
        if self.done or self.actor is None:
            return {'actions': [], 'to_call': 0, 'min_raise_to': 0, 'max_raise_to': 0}
        i = self.actor
        p = self.players[i]
        owed = max(0, self.current_bet - p.bet)
        maximum = p.bet + p.stack
        minimum = self.current_bet + self.min_raise
        reopened = i not in self.acted_at or self.current_bet - self.acted_at[i] >= self.min_raise
        can_raise = reopened and any(j != i for j in self.capable())
        actions = ['fold', 'call' if owed else 'check']
        if can_raise and maximum >= minimum:
            actions.append('raise')
        if maximum <= self.current_bet or (can_raise and maximum > self.current_bet):
            actions.append('allin')
        return {'actions': actions, 'to_call': min(owed, p.stack),
                'min_raise_to': minimum, 'max_raise_to': maximum}

    def act(self, action):
        legal = self.legal()
        if action.kind not in legal['actions']:
            raise ValueError('当前不能执行这个动作')
        if action.kind == 'raise' and (type(action.amount) is not int or
                not legal['min_raise_to'] <= action.amount <= legal['max_raise_to']):
            raise ValueError(f"加注总额应为 {legal['min_raise_to']}–{legal['max_raise_to']}")
        i = self.actor
        p = self.players[i]
        self.pending.discard(i)
        if action.kind == 'fold':
            p.folded = True
            label = '弃牌'
        elif action.kind == 'check':
            label = '过牌'
        else:
            if action.kind == 'call':
                target = p.bet + legal['to_call']
            elif action.kind == 'allin':
                target = p.bet + p.stack
            else:
                target = action.amount
            added = target - p.bet
            self._pay(i, added)
            if target > self.current_bet:
                increment = target - self.current_bet
                if increment >= self.min_raise:
                    self.min_raise = increment
                self.current_bet = target
                self.pending.update(j for j in self.capable() if j != i and self.players[j].bet < target)
                label = f'加注至 {target}'
            else:
                label = f'跟注 {added}'
            if p.stack == 0:
                label += ' · ALL-IN'
        self.acted_at[i] = self.current_bet
        p.last_action = label
        self.history.append(f'{p.name} {label}')
        self._progress(i)

    def _pay(self, i, amount):
        p = self.players[i]
        p.stack -= amount
        p.bet += amount
        p.committed += amount

    def _progress(self, after):
        while True:
            if len(self.live()) == 1:
                i = self.live()[0]
                self.players[i].stack += self.pot
                self.results.append(f'{self.players[i].name} 赢得 {self.pot}（其余玩家弃牌）')
                self._finish()
                return
            capable = self.capable()
            self.pending.intersection_update(capable)
            if len(capable) == 1:
                # A nominal big blind only sets the bring-in when others can contest it.
                other_bet = max(self.players[j].bet for j in self.live() if j != capable[0])
                self.current_bet = min(self.current_bet, other_bet)
            # No dry side-pot bets: the last player with chips only needs to face a bet.
            if len(capable) == 1 and self.players[capable[0]].bet >= self.current_bet:
                self.pending.clear()
            if self.pending:
                self.actor = self.next_seat(after, self.pending)
                return
            if self.street == 'river':
                self._settle()
                return
            self.street = STREETS[STREETS.index(self.street) + 1]
            self.deck.pop()  # burn
            self.board.extend(self.deck.pop() for _ in range(3 if self.street == 'flop' else 1))
            self.current_bet = 0
            self.min_raise = self.big_blind
            self.acted_at.clear()
            for p in self.players:
                p.bet = 0
                p.last_action = '弃牌' if p.folded and p.hole else ('ALL-IN' if p.stack == 0 and p.hole else '')
            self.history.append(f'{STREET_NAMES[self.street]} / {" ".join(self.board)}')
            self.pending = set(capable) if len(capable) >= 2 else set()
            after = self.button

    def _settle(self):
        self.showdown = True
        scores = {i: evaluate(self.players[i].hole + self.board) for i in self.live()}
        previous = 0
        pot_number = 0
        for level in sorted({p.committed for p in self.players if p.committed}):
            contributors = [i for i, p in enumerate(self.players) if p.committed >= level]
            amount = (level - previous) * len(contributors)
            previous = level
            if len(contributors) == 1:
                i = contributors[0]
                self.players[i].stack += amount
                self.results.append(f'{self.players[i].name} 收回未被跟注的 {amount}')
                continue
            eligible = [i for i in contributors if i in scores]
            best = max(scores[i] for i in eligible)
            winners = self.ordered_after(self.button, [i for i in eligible if scores[i] == best])
            share, odd = divmod(amount, len(winners))
            label = '主池' if pot_number == 0 else f'边池 {pot_number}'
            for k, i in enumerate(winners):
                payout = share + (k < odd)
                self.players[i].stack += payout
                self.results.append(f'{label} · {self.players[i].name} +{payout} · {HAND_NAMES[best[0]]}')
            pot_number += 1
        self._finish()

    def _finish(self):
        # Complete a separate review board after settlement, respecting burn cards.
        # It never changes the actual deal, payouts, or information sent to agents.
        self.review_board = self.board.copy()
        remaining = self.deck.copy()
        while len(self.review_board) < 5:
            remaining.pop()
            count = 3 if not self.review_board else 1
            self.review_board.extend(remaining.pop() for _ in range(count))
        for p in self.players:
            p.committed = p.bet = 0
        self.actor = None
        self.done = True
        self.history.extend(self.results)

    def observation(self, seat):
        """A new JSON-safe player view, with ONLY this seat's private cards."""
        return {'hand': self.number, 'seat': seat, 'button': self.button,
                'street': self.street, 'hole': self.players[seat].hole.copy(),
                'board': self.board.copy(), 'pot': self.pot, 'big_blind': self.big_blind,
                'current_bet': self.current_bet,
                'players': [{'seat': i, 'name': p.name, 'stack': p.stack, 'bet': p.bet,
                             'committed': p.committed, 'folded': p.folded}
                            for i, p in enumerate(self.players)],
                'legal': self.legal() if seat == self.actor else {'actions': []},
                'history': self.history.copy()}
