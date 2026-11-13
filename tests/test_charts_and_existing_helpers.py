"""Tests for the optional value formatting in helpers/charts.py and for the compatibility of the
existing helpers. Run from the repo root: poetry run python -m unittest discover -s tests -v
"""

import unittest

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from helpers import charts, checks  # noqa: E402


def bar_labels(fig):
    return [t.get_text() for t in fig.axes[0].texts]


class ChartFormatTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_bar_default_is_unchanged(self):
        fig = charts.bar(["a", "b"], [1234.4, 5.0], "title")
        self.assertEqual(bar_labels(fig), ["1,234", "5"])

    def test_bar_shows_rates_as_percentages_without_changing_values(self):
        values = [0.043, 0.0512]
        fig = charts.bar(["pre", "post"], values, "title", value_fmt="{:.1%}")
        self.assertEqual(bar_labels(fig), ["4.3%", "5.1%"])
        self.assertEqual([p.get_height() for p in fig.axes[0].patches], values)

    def test_horizontal_bar_accepts_value_fmt(self):
        fig = charts.bar(["a"], [0.25], "title", horizontal=True, value_fmt="{:.0%}")
        self.assertEqual(bar_labels(fig), ["25%"])

    def test_line_default_ticks_have_no_percent_sign(self):
        fig = charts.line([1, 2, 3], {"s": [0.03, 0.04, 0.05]}, "title")
        fig.canvas.draw()
        self.assertFalse(any("%" in t.get_text() for t in fig.axes[0].get_yticklabels()))

    def test_line_value_fmt_formats_ticks_only(self):
        series = {"s": [0.03, 0.04, 0.05]}
        fig = charts.line([1, 2, 3], series, "title", value_fmt="{:.0%}")
        fig.canvas.draw()
        labels = [t.get_text() for t in fig.axes[0].get_yticklabels() if t.get_text()]
        self.assertTrue(labels and all(t.endswith("%") for t in labels))
        self.assertEqual(list(fig.axes[0].lines[0].get_ydata()), series["s"])

    def test_save_still_works(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            fig = charts.bar(["a"], [0.5], "title", value_fmt="{:.1%}")
            path = charts.save(fig, Path(tmp) / "sub" / "c.png")
            self.assertTrue(path.exists() and path.stat().st_size > 0)


class ExistingHelperCompatibilityTests(unittest.TestCase):
    """Pins what the existing checks do, including where they do not fit saved aggregates."""

    def test_parts_sum_to_total_works_on_numbers_with_zero_tolerance(self):
        self.assertTrue(checks.parts_sum_to_total([3, 4], 7, tolerance=0)["ok"])
        self.assertFalse(checks.parts_sum_to_total([3, 4], 8, tolerance=0)["ok"])

    def test_parts_sum_default_tolerance_is_loose_for_counts(self):
        # 0.5 percent of 3,719 is 18, so the default would accept a miss of 18. Use tolerance=0.
        self.assertTrue(checks.parts_sum_to_total([3701], 3719)["ok"])
        self.assertFalse(checks.parts_sum_to_total([3701], 3719, tolerance=0)["ok"])

    def test_join_fan_out_still_flags_row_level_fan_out(self):
        left = pd.DataFrame({"k": [1, 2]})
        right = pd.DataFrame({"k": [1, 1, 2]})
        self.assertFalse(checks.join_fan_out(left, right, "k")["ok"])
        self.assertTrue(checks.join_fan_out(left, right.drop_duplicates("k"), "k")["ok"])

    def test_date_boundaries_is_fine_at_day_grain(self):
        days = pd.DataFrame({"d": pd.date_range("2026-08-01", "2026-08-31")})
        self.assertTrue(checks.date_boundaries(days, "d")["ok"])

    def test_date_boundaries_misreads_month_start_values_as_a_partial_month(self):
        months = pd.DataFrame({"m": pd.to_datetime(["2026-07-01", "2026-08-01"])})
        result = checks.date_boundaries(months, "m")
        self.assertTrue(result["last_partial"])   # a false alarm: use last_closed_month_start instead

    def test_matured_only_defaults_to_the_real_today_not_the_cutoff(self):
        df = pd.DataFrame({"ends_at": ["2026-09-24"]})
        self.assertEqual(checks.matured_only(df, "ends_at")["as_of"], pd.Timestamp.today().date().isoformat())
        explicit = checks.matured_only(df, "ends_at", as_of="2026-09-25")
        self.assertEqual(explicit["as_of"], "2026-09-25")
        self.assertEqual(explicit["dropped"], 0)


if __name__ == "__main__":
    unittest.main()
