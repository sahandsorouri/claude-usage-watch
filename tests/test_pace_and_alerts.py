import os, sys, unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import claude_usage_watch as cuw

NOW = datetime(2030, 1, 4, 12, 0, tzinfo=timezone.utc)
W = cuw.Words('en')


def meter(week_pct, five_pct, week_days_left=3.5, five_hours_left=2):
    return {'week': {'pct': week_pct, 'resets': (NOW + timedelta(days=week_days_left)).isoformat()},
            'five': {'pct': five_pct, 'resets': (NOW + timedelta(hours=five_hours_left)).isoformat()}}


class PaceTest(unittest.TestCase):
    def test_half_way_expected_is_50(self):
        p = cuw.pace(meter(50, 0)['week'], NOW)
        self.assertAlmostEqual(p['expected'], 50.0)
        self.assertAlmostEqual(p['ahead'], 0.0)
        self.assertAlmostEqual(p['end_pct'], 100.0)

    def test_ahead_runs_out_before_reset(self):
        p = cuw.pace(meter(70, 0)['week'], NOW)
        self.assertAlmostEqual(p['ahead'], 20.0)
        self.assertLess(p['full_at'], p['reset'])
        # 3.5 days used 70%, so full after 5 days: 2 days before the reset
        self.assertAlmostEqual((p['reset'] - p['full_at']).total_seconds(), 2 * 86400, delta=1)

    def test_zero_use_never_runs_out(self):
        self.assertIsNone(cuw.pace(meter(0, 0)['week'], NOW)['full_at'])


class AlertTest(unittest.TestCase):
    def test_quiet_when_low(self):
        out, _ = cuw.decide(meter(30, 20), {}, NOW, '2030-01-04', W)
        self.assertEqual(out, [])

    def test_only_highest_week_mark_announced_once(self):
        sent = {}
        out, _ = cuw.decide(meter(91, 0, week_days_left=0.5), sent, NOW, '2030-01-04', W)
        self.assertEqual(len(out), 1)
        self.assertIn('Weekly usage reached 91%', out[0])
        self.assertEqual(sum(k.startswith('week:') for k in sent), 3)   # 50, 75, 90 remembered
        out, _ = cuw.decide(meter(92, 0, week_days_left=0.5), sent, NOW, '2030-01-04', W)
        self.assertEqual(out, [])

    def test_pace_alert_once_a_day(self):
        sent = {'week:x': '2030-01-01'}
        m = meter(45, 0, week_days_left=5)   # 2 days in, 45% used: ahead about 16 points
        sent.update({f'week:{m["week"]["resets"][:13]}:50': '2030-01-01'})
        out, _ = cuw.decide(m, sent, NOW, '2030-01-04', W)
        self.assertEqual(len(out), 1)
        self.assertIn('running fast', out[0])
        out, _ = cuw.decide(m, sent, NOW, '2030-01-04', W)
        self.assertEqual(out, [])

    def test_no_pace_alert_when_slightly_ahead(self):
        out, _ = cuw.decide(meter(35, 0, week_days_left=5), {}, NOW, '2030-01-04', W)   # about 6 points ahead
        self.assertEqual(out, [])

    def test_five_hour_marks(self):
        sent = {}
        out, _ = cuw.decide(meter(10, 72), sent, NOW, '2030-01-04', W)
        self.assertEqual(len(out), 1)
        self.assertIn('5-hour usage reached 72%', out[0])
        out, _ = cuw.decide(meter(10, 95), sent, NOW, '2030-01-04', W)
        self.assertEqual(len(out), 1)
        out, _ = cuw.decide(meter(10, 99), sent, NOW, '2030-01-04', W)
        self.assertEqual(out, [])

    def test_idle_five_hour_window_counts_as_zero(self):
        m, err = cuw.parse_meter({'five_hour': None, 'seven_day': {'utilization': 12, 'resets_at': NOW.isoformat()}}, NOW)
        self.assertIsNone(err)
        self.assertEqual(m['five']['pct'], 0.0)

    def test_persian_digits(self):
        self.assertEqual(cuw.Words('fa').span(3 * 86400 + 6 * 3600), '۳ روز و ۶ ساعت')


if __name__ == '__main__':
    unittest.main()
