from itertools import combinations
import random
import unittest
from holdem.engine import Action, DECK, Hand, Player, evaluate


class RulesTests(unittest.TestCase):
    def hand(self, stacks=(1000, 1000, 1000), button=0):
        return Hand([Player(f'P{i}', s) for i, s in enumerate(stacks)],
                    button=button, small_blind=5, big_blind=10, rng=random.Random(9))

    def test_hand_categories_and_kickers(self):
        hands = ['As Jd 9h 5c 3s', 'As Ad 9h 5c 3s', 'As Ad 9h 9c 3s',
                 'As Ad Ah 5c 3s', 'As 2d 3h 4c 5s', 'As Js 9s 5s 3s',
                 'As Ad Ah 5c 5s', 'As Ad Ah Ac 3s', 'As Ks Qs Js Ts']
        scores = [evaluate(h.split()) for h in hands]
        self.assertEqual([s[0] for s in scores], list(range(9)))
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(evaluate('6s 2d 3h 4c 5s'.split()), scores[4])
        self.assertGreater(evaluate('As Ad Kh 5c 3s'.split()), scores[1])
        self.assertEqual(evaluate('As Ad Ah Ks Kd Kh 2s'.split()), (6, 14, 13))
        self.assertEqual(evaluate('As Ks Qs Js Ts 2s 3s'.split()), (8, 14))

    def test_seven_card_evaluation_matches_best_five_card_subset(self):
        rng = random.Random(81)
        for _ in range(200):
            cards = rng.sample(DECK, 7)
            self.assertEqual(evaluate(cards), max(evaluate(combo) for combo in combinations(cards, 5)))

    def test_blinds_turn_order_and_big_blind_option(self):
        h = self.hand()
        self.assertEqual(h.actor, 0)
        self.assertEqual(h.pot, 15)
        h.act(Action('call'))
        h.act(Action('call'))
        self.assertEqual(h.actor, 2)
        self.assertIn('check', h.legal()['actions'])
        h.act(Action('check'))
        self.assertEqual(h.street, 'flop')
        self.assertEqual(h.actor, 1)
        self.assertEqual(len(h.board), 3)

    def test_nine_seats_get_unique_cards_and_act_in_order(self):
        h = self.hand((1000,) * 9)
        cards = [card for player in h.players for card in player.hole]
        self.assertEqual(len(cards), 18)
        self.assertEqual(len(set(cards)), 18)
        self.assertEqual(len(h.deck), 34)
        order = []
        while h.street == 'preflop':
            order.append(h.actor)
            h.act(Action('check' if 'check' in h.legal()['actions'] else 'call'))
        self.assertEqual(order, [3, 4, 5, 6, 7, 8, 0, 1, 2])
        self.assertEqual(h.actor, 1)

    def test_nine_way_allin_splits_all_sidepots_and_returns_uncalled_chips(self):
        stacks = tuple(range(10, 100, 10))
        h = self.hand(stacks)
        holes = [card for card in DECK if not card.endswith('s')][:18]
        for i, player in enumerate(h.players):
            player.hole = holes[i * 2:i * 2 + 2]
        h.deck = list(reversed(['8h', 'As', 'Ks', 'Qs', '9h', 'Js', 'Th', 'Ts']))
        while not h.done:
            h.act(Action('allin' if 'allin' in h.legal()['actions'] else 'call'))
        self.assertEqual([p.stack for p in h.players], list(stacks))
        self.assertEqual(h.pot, 0)
        self.assertTrue(any('边池 7' in line for line in h.results))
        self.assertTrue(any('收回未被跟注的 10' in line for line in h.results))

    def test_heads_up_button_small_blind_acts_first_then_last(self):
        h = self.hand((1000, 1000))
        self.assertEqual(h.actor, 0)
        self.assertEqual([p.bet for p in h.players], [5, 10])
        h.act(Action('call'))
        h.act(Action('check'))
        self.assertEqual(h.actor, 1)

    def test_transition_to_heads_up_does_not_repeat_big_blind(self):
        h = self.hand((1000, 1000, 1000))
        self.assertEqual(h.bb, 2)
        h.players[0].stack = 0  # dealer eliminated: next BB must be seat 1
        self.assertEqual(h.next_button(), 2)
        h.players[0].stack = 1000
        h.players[1].stack = 0  # small blind eliminated: next BB must be seat 0
        self.assertEqual(h.next_button(), 2)

    def test_illegal_action_does_not_change_state(self):
        h = self.hand()
        before = h.observation(0)
        for action in [Action('check'), Action('raise', 11), Action('raise', 1001),
                       Action('raise', True), Action('dance')]:
            with self.assertRaises(ValueError):
                h.act(action)
            self.assertEqual(h.observation(0), before)

    def test_short_allin_does_not_reopen_raise(self):
        h = self.hand((1000, 15, 1000))
        h.act(Action('call'))
        h.act(Action('allin'))
        self.assertEqual(h.current_bet, 15)
        h.act(Action('call'))
        self.assertEqual(h.actor, 0)
        self.assertNotIn('raise', h.legal()['actions'])
        self.assertNotIn('allin', h.legal()['actions'])
        self.assertEqual(h.legal()['to_call'], 5)
        h.act(Action('call'))
        self.assertEqual(h.street, 'flop')

    def test_cumulative_short_allins_reopen_raise(self):
        h = self.hand((15, 20, 1000, 1000))
        h.act(Action('call'))  # seat 3 acts at 10
        h.act(Action('allin'))  # seat 0: 15, short raise of 5
        h.act(Action('allin'))  # seat 1: 20, short raise of 5
        h.act(Action('call'))  # seat 2 calls 20
        self.assertEqual(h.actor, 3)
        self.assertIn('raise', h.legal()['actions'])

    def test_short_big_blind_keeps_nominal_bring_in(self):
        h = self.hand((1000, 1000, 3))
        self.assertEqual(h.legal()['to_call'], 10)
        self.assertEqual(h.legal()['min_raise_to'], 20)

    def test_heads_up_short_blind_does_not_require_call_into_empty_sidepot(self):
        h = self.hand((100, 3))
        self.assertTrue(h.done)
        self.assertEqual(sum(p.stack for p in h.players), 103)
        self.assertEqual(len(h.board), 5)

    def test_last_player_only_faces_actual_short_allin_not_nominal_big_blind(self):
        h = self.hand((100, 2, 3))
        self.assertEqual(h.legal()['to_call'], 3)
        self.assertNotIn('raise', h.legal()['actions'])
        h.act(Action('call'))
        self.assertTrue(h.done)

    def test_allin_sidepots_and_uncalled_return(self):
        h = self.hand((100, 200, 300))
        h.players[0].hole = ['As', 'Ad']
        h.players[1].hole = ['Ks', 'Kd']
        h.players[2].hole = ['Qs', 'Qd']
        # pop() deals burn, flop, burn, turn, burn, river.
        h.deck = list(reversed(['6h', '2s', '3d', '7h', '8c', '9c', 'Th', 'Jc']))
        h.act(Action('allin'))
        h.act(Action('allin'))
        self.assertNotIn('raise', h.legal()['actions'])
        h.act(Action('call'))
        self.assertTrue(h.done)
        self.assertEqual([p.stack for p in h.players], [300, 200, 100])
        self.assertEqual(h.pot, 0)

    def test_fold_awards_pot_and_conserves_chips(self):
        h = self.hand()
        h.act(Action('fold'))
        h.act(Action('fold'))
        self.assertTrue(h.done)
        self.assertEqual([p.stack for p in h.players], [1000, 995, 1005])
        self.assertFalse(h.showdown)

    def test_early_finish_completes_review_board_without_changing_deal_or_payout(self):
        for street, offsets in [('preflop', [2, 3, 4, 6, 8]),
                                ('flop', [2, 4]), ('turn', [2]), ('river', [])]:
            with self.subTest(street=street):
                h = self.hand()
                while h.street != street:
                    h.act(Action('check' if 'check' in h.legal()['actions'] else 'call'))
                self.assertEqual(h.review_board, [])
                board, deck = h.board.copy(), h.deck.copy()
                expected = board + [deck[-offset] for offset in offsets]
                while len(h.live()) > 2:
                    h.act(Action('fold'))
                winner = next(i for i in h.live() if i != h.actor)
                expected_stacks = [p.stack for p in h.players]
                expected_stacks[winner] += h.pot
                h.act(Action('fold'))
                self.assertTrue(h.done)
                self.assertFalse(h.showdown)
                self.assertEqual(h.review_board, expected)
                self.assertEqual(h.board, board)
                self.assertEqual(h.deck, deck)
                self.assertEqual(h.street, street)
                self.assertEqual([p.stack for p in h.players], expected_stacks)
                self.assertEqual(h.pot, 0)
                observation = h.observation(0)
                self.assertEqual(observation['board'], board)
                self.assertNotIn('review_board', observation)
                self.assertTrue(all('hole' not in p for p in observation['players']))
                for card in expected[len(board):]:
                    self.assertNotIn(card, ' '.join(observation['history']))

    def test_showdown_review_uses_exactly_the_settled_board(self):
        h = self.hand()
        while not h.done:
            h.act(Action('check' if 'check' in h.legal()['actions'] else 'call'))
        self.assertTrue(h.showdown)
        self.assertEqual(h.review_board, h.board)
        self.assertEqual(len(h.review_board), 5)
        self.assertIsNot(h.review_board, h.board)

    def test_tie_odd_chip_left_of_button_and_folded_contribution(self):
        h = self.hand((100, 100, 100))
        h.players[0].hole = ['2c', '3c']
        h.players[1].hole = ['2d', '3d']
        h.deck = list(reversed(['4c', 'As', 'Ks', 'Qs', '4d', 'Js', '4h', 'Ts']))
        h.act(Action('raise', 25))
        h.act(Action('call'))
        h.act(Action('call'))
        h.act(Action('check'))  # SB
        h.act(Action('fold'))  # BB's 25 stay in the pot
        while not h.done:
            h.act(Action('check'))
        self.assertEqual([p.stack for p in h.players], [112, 113, 75])

    def test_observation_never_contains_other_hole_cards_or_deck(self):
        h = self.hand()
        obs = h.observation(0)
        self.assertEqual(obs['hole'], h.players[0].hole)
        self.assertNotIn('deck', obs)
        self.assertTrue(all('hole' not in p for p in obs['players']))

    def test_seeded_random_play_always_terminates_and_conserves_chips(self):
        rng = random.Random(42)
        for n in range(2, 10):
            for _ in range(30):
                stacks = [rng.randint(1, 500) for _ in range(n)]
                h = self.hand(stacks, rng.randrange(n))
                actions = 0
                while not h.done:
                    legal = h.legal()
                    name = rng.choice(legal['actions'])
                    amount = rng.randint(legal['min_raise_to'], legal['max_raise_to']) if name == 'raise' else 0
                    h.act(Action(name, amount))
                    self.assertEqual(sum(p.stack for p in h.players) + h.pot, sum(stacks))
                    self.assertTrue(all(p.stack >= 0 for p in h.players))
                    actions += 1
                    self.assertLess(actions, 300)
                self.assertEqual(sum(p.stack for p in h.players), sum(stacks))


if __name__ == '__main__':
    unittest.main()
