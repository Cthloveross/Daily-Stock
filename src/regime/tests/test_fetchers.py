# -*- coding: utf-8 -*-
"""Data-fetcher unit tests (everything mocked)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.regime.fetchers import RegimeDataFetcher, SECTOR_ETFS
from src.regime.scorers import score_premarket_activity


class TestSpySnapshot:
    def test_empty_when_yf_missing(self):
        f = RegimeDataFetcher(yf=None)
        assert f.get_spy_snapshot(date(2026, 4, 17)) == {}

    def test_computes_ma_and_5d(self):
        # 60 closes with a gentle uptrend.
        closes = [100.0 + i * 0.1 for i in range(60)]
        import pandas as pd

        hist = pd.DataFrame({"Close": closes})
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        snap = f.get_spy_snapshot(date(2026, 4, 17))
        assert "close" in snap
        assert snap["ma20"] is not None
        assert snap["pct_change_5d"] > 0
        assert snap["_status"] == "ready"

    def test_future_dated_daily_rows_do_not_leak_into_snapshot(self):
        import pandas as pd

        target = date(2026, 4, 17)
        valid_dates = pd.bdate_range(end=target, periods=60)
        valid_closes = [100.0 + index for index in range(60)]
        hist = pd.DataFrame(
            {
                "date": [
                    *(value.date().isoformat() for value in valid_dates),
                    "2026-04-20",
                ],
                "close": [*valid_closes, 10_000.0],
            }
        )
        fetcher = RegimeDataFetcher(yf=None, manager=None)
        fetcher._daily_frame = MagicMock(
            return_value=(hist, "future-leak-fixture")
        )

        snapshot = fetcher.get_spy_snapshot(target)

        assert snapshot["close"] == valid_closes[-1]
        assert snapshot["_observations"] == len(valid_closes)
        assert snapshot["ma20"] < 200.0

    def test_future_dated_index_rows_do_not_leak_into_snapshot(self):
        import pandas as pd

        target = date(2026, 4, 17)
        valid_dates = pd.bdate_range(end=target, periods=60)
        valid_closes = [200.0 + index for index in range(60)]
        hist = pd.DataFrame(
            {"Close": [*valid_closes, 20_000.0]},
            index=valid_dates.append(pd.DatetimeIndex(["2026-04-20"])),
        )
        fetcher = RegimeDataFetcher(yf=None, manager=None)
        fetcher._daily_frame = MagicMock(
            return_value=(hist, "future-index-fixture")
        )

        snapshot = fetcher.get_spy_snapshot(target)

        assert snapshot["close"] == valid_closes[-1]
        assert snapshot["_observations"] == len(valid_closes)
        assert snapshot["ma20"] < 300.0


class TestVix:
    def test_vix_history_used(self):
        import pandas as pd

        hist = pd.DataFrame({"Close": [12.0] * 10 + [15.0]})
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        snap = f.get_vix(date(2026, 4, 17))
        assert snap["level"] == 15.0
        assert snap["pct_change_5d"] > 0
        assert snap["_status"] == "ready"


class TestMacroEvents:
    def test_no_finnhub_still_serves_official_economic_series(self):
        """Zero-config path: FOMC/CPI/NFP need no API key at all."""
        f = RegimeDataFetcher(finnhub=None, manager=None)
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["fomc_today"] is False
        assert ev["cpi_today"] is False
        assert ev["earnings_count_watchlist"] == 0
        # Economic series is ready from the official schedule; only the
        # earnings calendar is missing without Finnhub.
        assert ev["_status"] == "degraded"
        assert ev["_readiness"] == {
            "economic_calendar": "ready",
            "earnings_calendar": "unavailable",
        }
        assert ev["_economic_calendar"]["source"] == "official_schedule"

    def test_fomc_decision_day_flagged_and_events_ready(self):
        finnhub = MagicMock()
        finnhub.configured = True
        finnhub.request_succeeded.return_value = True
        finnhub.get_earnings_calendar.return_value = [
            {"symbol": "NVDA"},
            {"symbol": "AAPL"},
        ]
        f = RegimeDataFetcher(finnhub=finnhub, manager=None)
        # 2026-04-29 is the decision day of the April 28-29 FOMC meeting.
        ev = f.get_macro_events(date(2026, 4, 29), watchlist=["NVDA"])
        assert ev["fomc_today"] is True
        assert ev["cpi_today"] is False
        assert ev["nfp_today"] is False
        assert ev["earnings_count_watchlist"] == 1
        assert ev["_status"] == "ready"
        assert ev["_readiness"] == {
            "economic_calendar": "ready",
            "earnings_calendar": "ready",
        }
        # The paid Finnhub economic endpoint must not be called any more.
        finnhub.get_economic_calendar.assert_not_called()
        finnhub.get_earnings_calendar.assert_called_once_with(
            date(2026, 4, 29), date(2026, 5, 6)
        )

    def test_first_day_of_fomc_meeting_is_not_flagged(self):
        f = RegimeDataFetcher(finnhub=None, manager=None)
        ev = f.get_macro_events(date(2026, 4, 28), watchlist=["NVDA"])
        assert ev["fomc_today"] is False

    def test_cpi_and_nfp_release_days_flagged(self):
        f = RegimeDataFetcher(finnhub=None, manager=None)
        cpi_day = f.get_macro_events(date(2026, 5, 12), watchlist=[])
        nfp_day = f.get_macro_events(date(2026, 6, 5), watchlist=[])
        assert cpi_day["cpi_today"] is True
        assert cpi_day["fomc_today"] is False
        assert nfp_day["nfp_today"] is True
        assert nfp_day["cpi_today"] is False

    def test_us_agenda_lists_official_events_in_window(self):
        f = RegimeDataFetcher(finnhub=None, manager=None)
        # 2026-09-10 .. 09-17 window: CPI on 09-11, FOMC decision on 09-16.
        ev = f.get_macro_events(date(2026, 9, 10), watchlist=[])
        assert [(row["date"], row["impact"]) for row in ev["us_agenda"]] == [
            ("2026-09-11", "high"),
            ("2026-09-16", "high"),
        ]

    def test_beyond_schedule_coverage_fails_closed_to_degraded(self):
        """A stale schedule must not silently report a clean calendar."""
        finnhub = MagicMock()
        finnhub.configured = True
        finnhub.request_succeeded.return_value = True
        finnhub.get_earnings_calendar.return_value = []
        f = RegimeDataFetcher(finnhub=finnhub, manager=None)
        # 2027-03-17 IS a known FOMC decision day, but BLS coverage ends in
        # 2026, so the economic series must fail closed rather than flag.
        ev = f.get_macro_events(date(2027, 3, 17), watchlist=["NVDA"])
        assert ev["_status"] == "degraded"
        assert ev["_readiness"] == {
            "economic_calendar": "unavailable",
            "earnings_calendar": "ready",
        }
        assert ev["fomc_today"] is False
        assert ev["us_agenda"] == []
        assert (
            ev["_economic_calendar"]["reason"]
            == "target_window_outside_coverage"
        )

    def test_earnings_permission_failure_keeps_official_economic_ready(self):
        class ForbiddenFinnhub:
            configured = True

            def __init__(self):
                self.calls = []

            def get_economic_calendar(self, from_, to):
                self.calls.append(("economic", from_, to))
                return []

            def get_earnings_calendar(self, from_, to):
                self.calls.append(("earnings", from_, to))
                return []

            def request_succeeded(self, operation):
                return False

        finnhub = ForbiddenFinnhub()
        f = RegimeDataFetcher(
            finnhub=finnhub,
            manager=None,
            request_budget_seconds=5,
        )
        ev = f.get_macro_events(date(2026, 4, 17), watchlist=["NVDA"])
        assert ev["_status"] == "degraded"
        assert ev["_readiness"] == {
            "economic_calendar": "ready",
            "earnings_calendar": "unavailable",
        }
        assert ev["fomc_today"] is False
        assert [call[0] for call in finnhub.calls] == ["earnings"]

    def test_earnings_failure_does_not_treat_returned_rows_as_observed(self):
        class PartialFinnhub:
            configured = True

            def get_earnings_calendar(self, from_, to):
                # Defensive regression: a provider may return a partial body
                # while still reporting the request as failed.
                return [{"date": "2026-04-29", "symbol": "NVDA"}]

            def request_succeeded(self, operation):
                return operation == "economic_calendar"

        f = RegimeDataFetcher(
            finnhub=PartialFinnhub(),
            manager=None,
            request_budget_seconds=5,
        )
        ev = f.get_macro_events(date(2026, 4, 29), watchlist=["NVDA"])

        assert ev["_status"] == "degraded"
        assert ev["_readiness"] == {
            "economic_calendar": "ready",
            "earnings_calendar": "unavailable",
        }
        assert ev["fomc_today"] is True
        assert ev["earnings_count_watchlist"] == 0
        assert ev["watchlist_earnings"] == []

    def test_finnhub_adapter_records_http_permission_failure(self):
        import requests

        from data_provider.finnhub_fetcher import FinnhubFetcher

        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")
        with patch(
            "data_provider.finnhub_fetcher.requests.get",
            return_value=response,
        ):
            finnhub = FinnhubFetcher(api_key="test", timeout=0.1)
            rows = finnhub.get_economic_calendar(
                date(2026, 4, 17), date(2026, 4, 24)
            )

        assert rows == []
        assert finnhub.request_succeeded("economic_calendar") is False
        assert "403 Forbidden" in (
            finnhub.last_request_error("economic_calendar") or ""
        )

    def test_finnhub_adapter_redacts_token_and_query_from_errors(self, caplog):
        import logging
        import requests

        from data_provider.finnhub_fetcher import FinnhubFetcher

        secret = "do-not-log-this-token"
        response = requests.Response()
        response.status_code = 403
        response.url = (
            "https://finnhub.io/api/v1/calendar/economic"
            f"?token={secret}&from=2026-04-17"
        )
        response.request = requests.Request(
            "GET",
            response.url,
        ).prepare()

        with patch(
            "data_provider.finnhub_fetcher.requests.get",
            return_value=response,
        ):
            finnhub = FinnhubFetcher(api_key=secret, timeout=0.1)
            with caplog.at_level(
                logging.WARNING,
                logger="data_provider.finnhub_fetcher",
            ):
                rows = finnhub.get_economic_calendar(
                    date(2026, 4, 17),
                    date(2026, 4, 24),
                )

        error = finnhub.last_request_error("economic_calendar") or ""
        assert rows == []
        assert "403" in error
        assert secret not in error
        assert secret not in caplog.text
        assert "?token=" not in error
        assert "?token=" not in caplog.text


class TestSectorPerformance:
    def test_counts_sectors_above_ma20(self):
        import pandas as pd

        # Two scenarios: half of sectors above MA20.
        def make_history(above_ma: bool):
            if above_ma:
                closes = list(range(100, 130))  # trending up, last > MA20
            else:
                closes = list(range(130, 100, -1))  # trending down
            return pd.DataFrame({"Close": [float(c) for c in closes]})

        yf_mock = MagicMock()

        counter = {"n": 0}

        def ticker_side_effect(sym):
            counter["n"] += 1
            t = MagicMock()
            # First 6 symbols "above MA20", next 5 below.
            t.history.return_value = make_history(counter["n"] <= 6)
            return t

        yf_mock.Ticker.side_effect = ticker_side_effect
        f = RegimeDataFetcher(yf=yf_mock)
        perf = f.get_sector_performance(date(2026, 4, 17))
        assert perf["sectors_above_ma20"] == 6
        assert perf["total_sectors_seen"] == 11
        assert perf["_status"] == "ready"


class TestPrevDay:
    def test_prev_day_structure(self):
        import pandas as pd

        idx = pd.date_range(end="2026-04-16", periods=6, freq="D")
        hist = pd.DataFrame(
            {
                "Low": [490.0] * 6,
                "High": [510.0] * 6,
                "Close": [508.0] * 5 + [508.0],
            },
            index=idx,
        )
        tkr = MagicMock()
        tkr.history.return_value = hist
        yf_mock = MagicMock()
        yf_mock.Ticker.return_value = tkr
        f = RegimeDataFetcher(yf=yf_mock)
        prev = f.get_prev_day_structure(date(2026, 4, 17))
        assert "close_vs_high_pct" in prev
        assert 0.85 <= prev["close_vs_high_pct"] <= 1.0
        assert prev["_status"] == "ready"


class TestPremarket:
    def test_unconfigured_source_is_explicitly_unavailable(self):
        f = RegimeDataFetcher(alpaca=None, manager=None)
        snapshot = f.get_premarket_activity(["SPY", "NVDA"], date(2026, 4, 17))
        assert snapshot["_status"] == "unavailable"
        assert snapshot["spy_pre_pct"] is None
        assert score_premarket_activity(snapshot) == 0

    def test_uses_provider_move_relative_to_previous_close(self):
        class Alpaca:
            configured = True

            def __init__(self):
                self.calls = []

            def get_premarket(self, symbol, *, target_date, as_of):
                self.calls.append((symbol, target_date, as_of))
                moves = {
                    "SPY": (1.25, 101.25, 100.0),
                    "NVDA": (6.0, 106.0, 100.0),
                    "AAPL": (-5.5, 94.5, 100.0),
                }
                move, price, prior = moves[symbol]
                return {
                    "pct_change": move,
                    "price": price,
                    "previous_close": prior,
                    "as_of": "2026-04-17T13:12:00+00:00",
                    "_status": "ready",
                    "_reason": None,
                }

        alpaca = Alpaca()
        observed_at = datetime(
            2026,
            4,
            17,
            13,
            12,
            45,
            tzinfo=timezone.utc,
        )
        f = RegimeDataFetcher(alpaca=alpaca, manager=None)
        snapshot = f.get_premarket_activity(
            ["NVDA", "AAPL"],
            date(2026, 4, 17),
            as_of=observed_at,
        )

        assert snapshot["_status"] == "ready"
        assert snapshot["spy_pre_pct"] == 1.25
        assert snapshot["watchlist_up_5pct"] == 1
        assert snapshot["watchlist_down_5pct"] == 1
        assert snapshot["_spy_previous_close"] == 100.0
        assert snapshot["_as_of"] == "2026-04-17T13:12:00+00:00"
        assert snapshot["_requested_as_of"] == observed_at.isoformat()
        assert snapshot["_attempted_sources"] == ["Alpaca"]
        assert snapshot["_reason"] is None
        assert [item["pct"] for item in snapshot["movers"]] == [6.0, -5.5]
        assert all(call[2] is observed_at for call in alpaca.calls)

    def test_legacy_minute_open_payload_is_not_treated_as_a_gap(self):
        class LegacyAlpaca:
            configured = True

            def __init__(self):
                self.calls = 0

            def get_premarket(self, symbol, *, target_date, as_of):
                self.calls += 1
                return {
                    "o": 100.0,
                    "c": 110.0,
                    "t": "2026-04-17T13:11:00Z",
                }

        alpaca = LegacyAlpaca()
        f = RegimeDataFetcher(alpaca=alpaca, manager=None)
        snapshot = f.get_premarket_activity(
            ["NVDA"],
            date(2026, 4, 17),
        )

        assert snapshot["_status"] == "degraded"
        assert snapshot["spy_pre_pct"] is None
        assert snapshot["watchlist_up_5pct"] == 0
        assert snapshot["_requested_symbols"] == 0
        assert score_premarket_activity(snapshot) == 0
        assert alpaca.calls == 1

    def test_permission_failure_reason_survives_regime_aggregation(self):
        class ForbiddenAlpaca:
            configured = True

            def get_premarket(self, symbol, *, target_date, as_of):
                return {
                    "pct_change": None,
                    "price": None,
                    "previous_close": None,
                    "as_of": None,
                    "_status": "unavailable",
                    "_reason": "premarket_minute_permission_denied",
                }

        f = RegimeDataFetcher(alpaca=ForbiddenAlpaca(), manager=None)
        snapshot = f.get_premarket_activity(
            ["NVDA"],
            date(2026, 4, 17),
            as_of=datetime(
                2026,
                4,
                17,
                13,
                12,
                45,
                tzinfo=timezone.utc,
            ),
        )

        assert snapshot["_status"] == "unavailable"
        assert snapshot["_source"] is None
        assert snapshot["_attempted_sources"] == ["Alpaca"]
        assert snapshot["_reason"] == (
            "SPY:premarket_minute_permission_denied"
        )
        assert snapshot["_reasons"] == [
            "SPY:premarket_minute_permission_denied"
        ]
        assert snapshot["spy_pre_pct"] is None
        assert score_premarket_activity(snapshot) == 0

    def test_naive_as_of_is_rejected_before_any_provider_call(self):
        alpaca = MagicMock()
        alpaca.configured = True
        fetcher = RegimeDataFetcher(alpaca=alpaca, manager=None)

        with pytest.raises(ValueError, match="timezone-aware"):
            fetcher.get_premarket_activity(
                ["NVDA"],
                date(2026, 4, 17),
                as_of=datetime(2026, 4, 17, 13, 12, 45),
            )

        alpaca.get_premarket.assert_not_called()


class TestMoomooFirstProviderPath:
    @staticmethod
    def _frame(rows=70):
        import pandas as pd

        dates = pd.date_range(end="2026-04-17", periods=rows, freq="B")
        closes = [100.0 + idx for idx in range(rows)]
        return pd.DataFrame(
            {
                "date": dates,
                "open": [value - 1 for value in closes],
                "high": [value + 1 for value in closes],
                "low": [value - 2 for value in closes],
                "close": closes,
                "volume": [1_000_000] * rows,
            }
        )

    def test_reuses_one_moomoo_spy_frame_for_direction_and_prev_day(self):
        class FakeMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def __init__(self):
                self.calls = []

            def get_daily_data(self, **kwargs):
                self.calls.append(kwargs["stock_code"])
                return TestMoomooFirstProviderPath._frame()

        class FakeManager:
            def __init__(self, fetcher):
                self.fetcher = fetcher

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        moomoo = FakeMoomoo()
        f = RegimeDataFetcher(
            yf=None,
            manager=FakeManager(moomoo),
            alpaca=None,
            finnhub=None,
        )
        spy = f.get_spy_snapshot(date(2026, 4, 17))
        prev = f.get_prev_day_structure(date(2026, 4, 17))

        assert spy["_source"] == "MoomooFetcher"
        assert prev["_source"] == "MoomooFetcher"
        assert moomoo.calls.count("SPY") == 1

    def test_sector_breadth_uses_moomoo_without_yfinance_fanout(self):
        class FakeMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def get_daily_data(self, **kwargs):
                return TestMoomooFirstProviderPath._frame()

        class FakeManager:
            def __init__(self):
                self.fetcher = FakeMoomoo()

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        yf_mock = MagicMock()
        f = RegimeDataFetcher(
            yf=yf_mock,
            manager=FakeManager(),
            alpaca=None,
            finnhub=None,
        )
        sectors = f.get_sector_performance(date(2026, 4, 17))

        assert sectors["_status"] == "ready"
        assert set(sectors["_sources"]) == set(SECTOR_ETFS)
        assert set(sectors["_sources"].values()) == {"MoomooFetcher"}
        yf_mock.Ticker.assert_not_called()

    def test_no_moomoo_does_not_fan_out_sector_yfinance_requests(self):
        class EmptyManager:
            def _get_fetchers_snapshot(self):
                return []

        yf_mock = MagicMock()
        f = RegimeDataFetcher(
            yf=yf_mock,
            manager=EmptyManager(),
            alpaca=None,
            finnhub=None,
        )
        sectors = f.get_sector_performance(date(2026, 4, 17))

        assert sectors["_status"] == "unavailable"
        assert sectors["total_sectors_seen"] == 0
        yf_mock.Ticker.assert_not_called()

    def test_vix_uses_official_cboe_fallback_when_moomoo_has_no_symbol(self):
        class MissingVixMoomoo:
            name = "MoomooFetcher"
            priority = 2

            def get_daily_data(self, **kwargs):
                raise RuntimeError("Unknown stock. VIX")

        class FakeManager:
            def __init__(self):
                self.fetcher = MissingVixMoomoo()

            def _get_fetchers_snapshot(self):
                return [self.fetcher]

            def _call_fetcher_method(self, fetcher, method, **kwargs):
                return getattr(fetcher, method)(**kwargs)

        response = MagicMock()
        response.text = (
            "DATE,OPEN,HIGH,LOW,CLOSE\n"
            "04/08/2026,18,19,17,18\n"
            "04/09/2026,17,18,16,17\n"
            "04/10/2026,16,17,15,16\n"
            "04/13/2026,15,16,14,15\n"
            "04/14/2026,14,15,13,14\n"
            "04/15/2026,13,14,12,13\n"
            "04/16/2026,12,13,11,12\n"
            "04/17/2026,11,12,10,11\n"
        )
        response.raise_for_status.return_value = None
        yf_mock = MagicMock()
        with patch(
            "requests.get",
            return_value=response,
        ):
            f = RegimeDataFetcher(
                yf=yf_mock,
                manager=FakeManager(),
                alpaca=None,
                finnhub=None,
            )
            vix = f.get_vix(date(2026, 4, 17))

        assert vix["_source"] == "Cboe"
        assert vix["_status"] == "ready"
        assert vix["level"] == 11.0
        yf_mock.Ticker.assert_not_called()
