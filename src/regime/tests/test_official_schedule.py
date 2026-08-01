# -*- coding: utf-8 -*-
"""Official annual schedule provider tests (local data file, no network)."""
from __future__ import annotations

import json
from datetime import date

from src.regime.official_schedule import (
    get_official_macro_snapshot,
    load_official_schedule,
)


class TestShippedScheduleFlags:
    def test_fomc_decision_day_flagged(self):
        snap = get_official_macro_snapshot(date(2026, 4, 29))
        assert snap["readiness"] == "ready"
        assert snap["fomc_today"] is True
        assert snap["cpi_today"] is False
        assert snap["nfp_today"] is False

    def test_first_day_of_two_day_fomc_meeting_is_not_the_decision(self):
        # April 28-29 2026 meeting: the decision lands on the 29th.
        snap = get_official_macro_snapshot(date(2026, 4, 28))
        assert snap["readiness"] == "ready"
        assert snap["fomc_today"] is False

    def test_cpi_release_day_flagged(self):
        snap = get_official_macro_snapshot(date(2026, 5, 12))
        assert snap["cpi_today"] is True
        assert snap["fomc_today"] is False
        assert snap["nfp_today"] is False

    def test_nfp_release_day_flagged(self):
        snap = get_official_macro_snapshot(date(2026, 6, 5))
        assert snap["nfp_today"] is True
        assert snap["cpi_today"] is False

    def test_non_event_day_is_a_ready_clean_calendar(self):
        snap = get_official_macro_snapshot(date(2026, 4, 17))
        assert snap["readiness"] == "ready"
        assert snap["fomc_today"] is False
        assert snap["cpi_today"] is False
        assert snap["nfp_today"] is False

    def test_metadata_identifies_the_official_sources(self):
        snap = get_official_macro_snapshot(date(2026, 4, 17))
        assert snap["source"] == "official_schedule"
        assert snap["schedule_version"]
        assert snap["retrieved_at"]
        assert snap["coverage_through"]
        assert "federalreserve.gov" in snap["source_urls"]["fomc"]
        assert "bls.gov" in snap["source_urls"]["cpi"]
        assert "bls.gov" in snap["source_urls"]["nfp"]


class TestAgendaWindow:
    def test_window_collects_cpi_then_fomc_sorted_by_date(self):
        # 2026-09-10 .. 09-17 window: CPI on 09-11, FOMC decision on 09-16.
        snap = get_official_macro_snapshot(date(2026, 9, 10))
        assert snap["readiness"] == "ready"
        assert [(row["date"], row["impact"]) for row in snap["us_agenda"]] == [
            ("2026-09-11", "high"),
            ("2026-09-16", "high"),
        ]
        assert "CPI" in snap["us_agenda"][0]["event"]
        assert "FOMC" in snap["us_agenda"][1]["event"]

    def test_events_beyond_the_window_are_excluded(self):
        # 2026-07-20 .. 07-27 window: FOMC decision 07-29 is outside.
        snap = get_official_macro_snapshot(date(2026, 7, 20))
        assert snap["readiness"] == "ready"
        assert snap["us_agenda"] == []


class TestCoverageFailClosed:
    def test_target_beyond_coverage_is_unavailable_not_a_clean_calendar(self):
        snap = get_official_macro_snapshot(date(2027, 3, 17))
        # 2027-03-17 IS a known FOMC decision day, but CPI/NFP coverage ends
        # in 2026, so "no CPI today" would be a guess.  Fail closed.
        assert snap["readiness"] == "unavailable"
        assert snap["reason"] == "target_window_outside_coverage"
        assert snap["fomc_today"] is False
        assert snap["us_agenda"] == []

    def test_window_crossing_coverage_boundary_fails_closed(self):
        # Overall coverage_through is 2026-12-04 (NFP series).  The last ready
        # target is 2026-11-27; one day later the 7-day window leaks past it.
        assert (
            get_official_macro_snapshot(date(2026, 11, 27))["readiness"]
            == "ready"
        )
        assert (
            get_official_macro_snapshot(date(2026, 11, 28))["readiness"]
            == "unavailable"
        )

    def test_target_before_coverage_fails_closed(self):
        snap = get_official_macro_snapshot(date(2025, 6, 2))
        assert snap["readiness"] == "unavailable"
        assert snap["fomc_today"] is False


class TestLoader:
    def test_empty_data_dir_fails_closed(self, tmp_path):
        assert load_official_schedule(tmp_path) is None
        snap = get_official_macro_snapshot(
            date(2026, 4, 17), data_dir=tmp_path
        )
        assert snap["readiness"] == "unavailable"
        assert snap["reason"] == "schedule_unavailable"

    def test_missing_required_series_fails_closed(self, tmp_path):
        payload = {
            "schedule_version": "test.1",
            "retrieved_at": "2026-08-01T00:00:00Z",
            "series": {
                "fomc": {
                    "event": "FOMC Rate Decision",
                    "source_url": "https://www.federalreserve.gov/x",
                    "retrieved_at": "2026-08-01T00:00:00Z",
                    "coverage_start": "2026-01-01",
                    "coverage_through": "2026-12-31",
                    "dates": ["2026-04-29"],
                }
            },
        }
        (tmp_path / "official_economic_schedule_test.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        assert load_official_schedule(tmp_path) is None
        snap = get_official_macro_snapshot(
            date(2026, 4, 17), data_dir=tmp_path
        )
        assert snap["readiness"] == "unavailable"

    def test_malformed_file_fails_closed(self, tmp_path):
        (tmp_path / "official_economic_schedule_bad.json").write_text(
            "{not json", encoding="utf-8"
        )
        assert load_official_schedule(tmp_path) is None

    def test_multiple_year_files_merge_and_extend_coverage(self, tmp_path):
        def series(dates, start, through):
            return {
                "event": "x",
                "source_url": "https://official.example/x",
                "retrieved_at": "2026-08-01T00:00:00Z",
                "coverage_start": start,
                "coverage_through": through,
                "dates": dates,
            }

        year_a = {
            "schedule_version": "2026.t",
            "retrieved_at": "2026-08-01T00:00:00Z",
            "series": {
                "fomc": series(["2026-04-29"], "2026-01-01", "2026-12-31"),
                "cpi": series(["2026-05-12"], "2026-01-01", "2026-12-31"),
                "nfp": series(["2026-05-08"], "2026-01-01", "2026-12-31"),
            },
        }
        year_b = {
            "schedule_version": "2027.t",
            "retrieved_at": "2026-12-01T00:00:00Z",
            "series": {
                "fomc": series(["2027-01-27"], "2027-01-01", "2027-12-31"),
                "cpi": series(["2027-01-13"], "2027-01-01", "2027-12-31"),
                "nfp": series(["2027-01-08"], "2027-01-01", "2027-12-31"),
            },
        }
        (tmp_path / "official_economic_schedule_2026.json").write_text(
            json.dumps(year_a), encoding="utf-8"
        )
        (tmp_path / "official_economic_schedule_2027.json").write_text(
            json.dumps(year_b), encoding="utf-8"
        )
        schedule = load_official_schedule(tmp_path)
        assert schedule is not None
        assert schedule.coverage_start == date(2026, 1, 1)
        assert schedule.coverage_through == date(2027, 12, 31)
        snap = schedule.snapshot(date(2027, 1, 27))
        assert snap["readiness"] == "ready"
        assert snap["fomc_today"] is True
        assert snap["schedule_version"] == "2026.t+2027.t"
