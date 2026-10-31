import unittest

from luce import stats


def _snap(lost, packets=1000, t=0.0):
    return {"bytes": 0, "packets": packets, "lost": lost, "jitter_ms": 1.0, "time": t}


class FormatLineLossTest(unittest.TestCase):
    def test_shows_loss_of_the_current_interval_and_total(self):
        line = stats.format_line("↑", _snap(lost=15, t=1.0), _snap(lost=10, t=0.0))
        self.assertIn("5/s perdidos (15 total)", line)

    def test_old_burst_shows_zero_per_second(self):
        line = stats.format_line("↑", _snap(lost=15, t=1.0), _snap(lost=15, t=0.0))
        self.assertIn("0/s perdidos (15 total)", line)

    def test_first_reading_has_no_interval(self):
        line = stats.format_line("↓", _snap(lost=7), None)
        self.assertIn("0/s perdidos (7 total)", line)

    def test_counter_going_back_never_shows_negative(self):
        line = stats.format_line("↓", _snap(lost=3, t=1.0), _snap(lost=9, t=0.0))
        self.assertIn("0/s perdidos (3 total)", line)


if __name__ == "__main__":
    unittest.main()
