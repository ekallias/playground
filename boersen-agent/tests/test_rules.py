import unittest
from datetime import date, datetime

from boersen_agent import rules as R
from tests.helpers import daily_series, trend


class WeeklyTests(unittest.TestCase):
    def test_partial_week_is_split_off_until_friday_night(self):
        s = daily_series(date(2026, 10, 5), 8, trend())   # Mon 5 Oct .. Wed 14 Oct
        done, prov = R.weekly(s, datetime(2026, 10, 14, 18, 0))
        self.assertEqual(done[-1][0], "2026-10-09")
        self.assertEqual(prov[0], "2026-10-14")
        done, prov = R.weekly(s, datetime(2026, 10, 17, 9, 0))  # Saturday
        self.assertIsNone(prov)
        self.assertEqual(done[-1][0], "2026-10-14")

    def test_friday_after_23h_counts_as_complete(self):
        s = daily_series(date(2026, 10, 12), 5, trend())   # Mon..Fri 16 Oct
        self.assertIsNotNone(R.weekly(s, datetime(2026, 10, 16, 18))[1])
        self.assertIsNone(R.weekly(s, datetime(2026, 10, 16, 23, 30))[1])


class BreakoutTests(unittest.TestCase):
    def test_n_week_low_and_high(self):
        c = [10, 9, 8, 11, 7]
        self.assertTrue(R.is_n_week_low(c, 4, 5))
        self.assertFalse(R.is_n_week_low(c, 3, 4))
        self.assertTrue(R.is_n_week_high(c, 3, 4))

    def test_latched_keeps_state_and_flips_meaning(self):
        c = [5, 6, 7, 8, 7, 6, 5, 4, 3]
        st = R.latched(c, 3, 3)
        self.assertEqual(st[3], 1)        # new 3-week high
        self.assertEqual(st[4], 1)        # kept
        self.assertEqual(st[-1], -1)      # new 3-week low
        self.assertEqual(R.latched(c, 3, 3, up_is_positive=False)[3], -1)


class QuarterRuleTests(unittest.TestCase):
    def test_warning_needs_close_below_current_and_previous_quarter(self):
        weeks = [(f"2026-{m:02d}-{d:02d}", v) for (m, d), v in zip(
            [(1, 9), (1, 23), (2, 6), (2, 20), (3, 6), (3, 20), (4, 3), (4, 17), (5, 1)],
            [100, 102, 101, 103, 104, 105, 99.5, 101, 98])]
        q = R.quarter_rule(weeks)
        self.assertEqual(q.events[6], "low")      # 99.5 < min of Q1 and earlier Q2 weeks
        self.assertEqual(q.state[-1], -1)
        self.assertEqual(q.low_level, 98)


class NineMonthTests(unittest.TestCase):
    def test_breach_and_level(self):
        closes = [100 + i for i in range(40)] + [90]
        info = R.nine_month(closes, 39)
        self.assertTrue(info.confirmed_breach)
        self.assertEqual((info.breach_weeks_ago, info.breach_level), (0, 101))
        self.assertEqual(info.level, 90)

    def test_breach_persists_while_price_stays_below_broken_level(self):
        closes = [100 + i for i in range(40)] + [90, 90, 92]
        info = R.nine_month(closes, 39)
        self.assertTrue(info.confirmed_breach)
        self.assertEqual(info.breach_weeks_ago, 2)
        recovered = R.nine_month(closes + [110], 39)
        self.assertFalse(recovered.confirmed_breach)

    def test_new_high_and_weeks_since(self):
        closes = [100] * 39 + [101, 100.5, 100.2]
        info = R.nine_month(closes, 39)
        self.assertFalse(info.new_high)
        self.assertEqual(info.weeks_since_high, 2)


class RSAndSeasonTests(unittest.TestCase):
    def test_relative_strength_definition(self):
        s = daily_series(date(2025, 1, 1), 330, lambda i: 100)
        s[-1] = (s[-1][0], 120.0)
        self.assertAlmostEqual(R.relative_strength(s), 120.0)

    def test_rs_needs_15_months(self):
        self.assertIsNone(R.relative_strength(daily_series(date(2026, 1, 1), 100, trend())))

    def test_season_and_strict_window(self):
        self.assertEqual(R.last_friday(2026, 4), date(2026, 4, 24))
        self.assertEqual(R.last_friday(2026, 10), date(2026, 10, 30))
        self.assertTrue(R.season_positive(date(2026, 4, 23)))
        self.assertFalse(R.season_positive(date(2026, 4, 24)))
        self.assertTrue(R.season_positive(date(2026, 10, 30)))
        self.assertTrue(R.strict_summer(date(2026, 8, 3)))
        self.assertFalse(R.strict_summer(date(2026, 7, 30)))


if __name__ == "__main__":
    unittest.main()
