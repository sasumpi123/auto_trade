"""Bounded-repeat synthetic timestamps only; no captured live account data."""
import inspect
import unittest

from services.tick_timing import TickTiming,TickTimingError


class RepeatTimingTests(unittest.TestCase):
    def guard(self,repeat):
        # Run this same success contract on the baseline before the new option exists.
        kw={'discard_repeated_small_regressions':repeat} if 'discard_repeated_small_regressions' in inspect.signature(TickTiming).parameters else {}
        return TickTiming(['KRW-BTC'],1800000000,0,discard_small_regressions=True,**kw)
    def frame(self,stamp):
        return {'type':'ticker','code':'KRW-BTC','timestamp':1800000000000+stamp,'trade_price':'POISON_NOT_A_PRICE'}
    def send(self,g,stamp,at):return g.observe(self.frame(stamp),1800000000+at,at)
    def test_p15_still_halts_on_second_separated_minus_two(self):
        g=self.guard(False)
        self.send(g,100,.1);self.assertIsNone(self.send(g,98,.2));self.send(g,101,.3);self.send(g,200,.4)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,198,.5)
        self.assertEqual(g.discarded,1)
    def test_new_policy_survives_two_separated_minus_two_with_newer_followups(self):
        g=self.guard(True)
        self.send(g,100,.1);self.assertIsNone(self.send(g,98,.2));self.send(g,101,.3);self.send(g,200,.4)
        self.assertIsNone(self.send(g,198,.5));self.send(g,201,.6)
        self.assertEqual(g.discarded,2);self.assertEqual(g.accepted,4)
        self.assertIsNone(g.awaiting_followup);self.assertEqual(g.previous['KRW-BTC'],1800000000201)
