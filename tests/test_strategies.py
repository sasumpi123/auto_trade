from dataclasses import FrozenInstanceError, replace
import unittest
from itertools import product
from unittest.mock import patch

from strategies.signals import (Snapshot, Signal, RSIStrategy, MACDStrategy, BollingerStrategy,
                                ConsensusStrategy, LegacyCompositeStrategy, research_strategies)


def snapshot(**overrides):
    values = dict(close=100, rsi=50, rsi_delta=0, macd=0, macd_signal=0,
                  macd_delta=0, bb_lower=80, bb_middle=100, bb_upper=120)
    return Snapshot(**(values | overrides))


class StrategyTests(unittest.TestCase):
    def test_rsi_thresholds_and_direction(self):
        strategy = RSIStrategy()
        for rsi, trend, expected in [(29, 1, 'BUY'), (71, -1, 'SELL'), (30, 1, 'HOLD'),
                                      (70, -1, 'HOLD'), (20, -1, 'HOLD'), (80, 1, 'HOLD')]:
            with self.subTest(rsi=rsi, trend=trend):
                self.assertEqual(strategy.decide(snapshot(rsi=rsi, rsi_delta=trend)).action, expected)

    def test_macd_level_and_trend_not_an_invented_cross_event(self):
        strategy = MACDStrategy()
        self.assertEqual(strategy.decide(snapshot(macd=-1, macd_signal=-2, macd_delta=1)).action, 'BUY')
        self.assertEqual(strategy.decide(snapshot(macd=1, macd_signal=2, macd_delta=-1)).action, 'SELL')
        self.assertEqual(strategy.decide(snapshot(macd=-1, macd_signal=-2, macd_delta=-1)).action, 'HOLD')
        self.assertEqual(strategy.decide(snapshot(macd=1, macd_signal=0, macd_delta=1)).action, 'HOLD')

    def test_standalone_bands_do_not_require_other_indicators(self):
        strategy = BollingerStrategy()
        self.assertEqual(strategy.decide(snapshot(close=79, rsi_delta=-1, macd_delta=-1)).action, 'BUY')
        self.assertEqual(strategy.decide(snapshot(close=121)).action, 'SELL')
        self.assertEqual(strategy.decide(snapshot(close=80)).action, 'HOLD')
        self.assertEqual(strategy.decide(snapshot(close=120)).action, 'HOLD')

    def test_two_votes_required_and_vote_record_is_complete(self):
        s = snapshot(rsi=20, rsi_delta=1, macd=-1, macd_signal=-2, macd_delta=1)
        signal = ConsensusStrategy(2).decide(s)
        self.assertEqual(signal.action, 'BUY')
        self.assertEqual(signal.votes, (('rsi', 'BUY'), ('macd', 'BUY'), ('bollinger', 'HOLD')))
        self.assertEqual(ConsensusStrategy(3).decide(s).action, 'HOLD')
        self.assertEqual(ConsensusStrategy(2).decide(replace(s, macd_delta=0)).action, 'HOLD')

    def test_unanimity_requires_three_votes(self):
        s = snapshot(close=79, rsi=20, rsi_delta=1, macd=-1, macd_signal=-2, macd_delta=1)
        self.assertEqual(ConsensusStrategy(3).decide(s).action, 'BUY')
        sell = snapshot(close=121, rsi=80, rsi_delta=-1, macd=1, macd_signal=2, macd_delta=-1)
        self.assertEqual(ConsensusStrategy(3).decide(sell).action, 'SELL')

    def test_opposing_vote_blocks_two_of_three_in_both_directions(self):
        buy_conflict = snapshot(close=121, rsi=20, rsi_delta=1,
                                macd=-1, macd_signal=-2, macd_delta=1)
        sell_conflict = snapshot(close=79, rsi=80, rsi_delta=-1,
                                 macd=1, macd_signal=2, macd_delta=-1)
        for observation, votes, legacy_action in (
                (buy_conflict, ('BUY', 'BUY', 'SELL'), 'BUY'),
                (sell_conflict, ('SELL', 'SELL', 'BUY'), 'SELL')):
            with self.subTest(votes=votes):
                for quorum in (2, 3):
                    signal = ConsensusStrategy(quorum).decide(observation)
                    self.assertEqual(signal.action, 'HOLD')
                    self.assertEqual(tuple(action for _, action in signal.votes), votes)
                self.assertEqual(LegacyCompositeStrategy().decide(observation).action, legacy_action)

    def test_consensus_contract_for_all_vote_combinations(self):
        for votes in product(('BUY', 'SELL', 'HOLD'), repeat=3):
            with self.subTest(votes=votes), \
                    patch.object(RSIStrategy, 'decide', return_value=Signal(votes[0])), \
                    patch.object(MACDStrategy, 'decide', return_value=Signal(votes[1])), \
                    patch.object(BollingerStrategy, 'decide', return_value=Signal(votes[2])):
                for quorum in (2, 3):
                    expected = 'HOLD'
                    for direction, opposite in (('BUY', 'SELL'), ('SELL', 'BUY')):
                        if votes.count(direction) >= quorum and opposite not in votes:
                            expected = direction
                    signal = ConsensusStrategy(quorum).decide(snapshot())
                    self.assertEqual(signal.action, expected)
                    self.assertEqual(tuple(action for _, action in signal.votes), votes)

    def test_legacy_bands_still_require_confirmation(self):
        s = snapshot(close=79, rsi_delta=-1, macd_delta=-1)
        self.assertEqual(LegacyCompositeStrategy().decide(s).action, 'HOLD')
        self.assertEqual(LegacyCompositeStrategy().decide(replace(s, rsi_delta=1)).action, 'BUY')

    def test_legacy_priority_target_and_reason_are_preserved(self):
        s = snapshot(close=121, rsi=20, rsi_delta=1)
        signal = LegacyCompositeStrategy().decide(s)
        self.assertEqual(signal.action, 'SELL')
        self.assertEqual(signal.target_price, 121 * 1.05)
        self.assertIn('RSI 과매도 반등', signal.reason)
        self.assertIn('BB 상단 돌파', signal.reason)

    def test_snapshot_and_builtin_strategies_are_immutable(self):
        s = snapshot()
        with self.assertRaises(FrozenInstanceError):
            s.close = 200
        strategy = ConsensusStrategy()
        self.assertEqual(strategy.decide(s), strategy.decide(s))
        with self.assertRaises(FrozenInstanceError):
            strategy.quorum = 3

    def test_invalid_inputs_and_quorum_fail(self):
        for value in (float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                snapshot(rsi=value)
        with self.assertRaises(ValueError):
            snapshot(close=0)
        with self.assertRaises(ValueError):
            Signal('SHORT')
        with self.assertRaises(ValueError):
            ConsensusStrategy(1)

    def test_research_registry_has_no_implicit_legacy_or_live_selection(self):
        self.assertEqual([s.name for s in research_strategies()],
                         ['rsi', 'macd', 'bollinger', 'consensus_2of3', 'consensus_3of3'])
