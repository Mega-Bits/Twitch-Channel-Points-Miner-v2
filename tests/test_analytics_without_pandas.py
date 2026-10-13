"""Verify that the Pi analytics endpoints do not require pandas or NumPy."""

import importlib
import sys
import unittest
from datetime import datetime
from unittest.mock import patch


def _midday(date):
    return datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000 + 12 * 3600 * 1000


def _analytics():
    # A missing pandas module must never break the AnalyticsServer import.
    with patch.dict(sys.modules, {"pandas": None}):
        module = importlib.import_module(
            "TwitchChannelPointsMiner.classes.AnalyticsServer"
        )
    return module


class AnalyticsWithoutPandasTests(unittest.TestCase):
    def test_import_does_not_load_pandas(self):
        module = _analytics()
        self.assertTrue(callable(module.filter_datas))
        self.assertTrue(hasattr(module, "AnalyticsServer"))

    def test_filters_and_sorts_points_and_annotations(self):
        filter_datas = _analytics().filter_datas
        earlier = _midday("2026-09-30")
        inside = _midday("2026-10-02")
        late = _midday("2026-10-04")
        data = {
            "series": [
                {"x": inside, "y": 15, "z": "Watch"},
                {"x": earlier, "y": 12, "z": "Claim"},
                {"x": inside, "y": 11, "z": "Claim"},
                {"x": late, "y": 20, "z": "Watch"},
            ],
            "annotations": [
                {"x": late, "label": "After"},
                {"x": inside + 500, "label": "Later"},
                {"x": inside, "label": "Earlier"},
            ],
            "other": "preserved",
        }
        result = filter_datas("2026-10-01", "2026-10-03", data)
        self.assertEqual([row["y"] for row in result["series"]], [11, 15])
        self.assertEqual(
            [row["label"] for row in result["annotations"]],
            ["Earlier", "Later"],
        )
        self.assertEqual(result["other"], "preserved")

    def test_empty_time_range_retains_prior_balance(self):
        filter_datas = _analytics().filter_datas
        result = filter_datas(
            "2026-10-01",
            "2026-10-03",
            {
                "series": [
                    {"x": _midday("2026-09-28"), "y": 7, "z": "Watch"},
                    {"x": _midday("2026-09-30"), "y": 42, "z": "Claim"},
                    {"x": _midday("2026-10-05"), "y": 88, "z": "Watch"},
                ]
            },
        )
        self.assertEqual([row["y"] for row in result["series"]], [42, 42])
        self.assertTrue(all(row["z"] == "No Stream" for row in result["series"]))
        self.assertEqual(result["annotations"], [])

    def test_no_previous_balance_defaults_to_zero(self):
        filter_datas = _analytics().filter_datas
        result = filter_datas(
            "2026-10-01",
            "2026-10-02",
            {"series": [{"x": _midday("2026-10-05"), "y": 99, "z": "Watch"}]},
        )
        self.assertEqual([row["y"] for row in result["series"]], [0, 0])

    def test_entirely_empty_analytics_json_is_supported(self):
        filter_datas = _analytics().filter_datas
        result = filter_datas("2026-10-01", "2026-10-03", {})
        self.assertEqual(len(result["series"]), 2)
        self.assertEqual([row["y"] for row in result["series"]], [0, 0])
        self.assertEqual(result["annotations"], [])


if __name__ == "__main__":
    unittest.main()
