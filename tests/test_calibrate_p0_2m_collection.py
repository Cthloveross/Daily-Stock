from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pandas as pd
import pytest

from data_provider.base import DataFetchError
from data_provider.moomoo_fetcher import MoomooHistoryEmptyError
from scripts import calibrate_p0_2m as cli
from src.journal.ledger.p0_2m_shadow_repository import P02mShadowEnrollmentRecord


@pytest.fixture
def collection_clock(monkeypatch):
    moment = datetime(2026, 10, 10, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(cli, "_now_utc", lambda: moment)
    return moment


@pytest.fixture
def small_universe(monkeypatch, collection_clock):
    symbols = ("FXI", "TQQQ", "AAPL")
    monkeypatch.setattr(cli, "DEFAULT_P0_UNIVERSE_69", symbols)
    return symbols


class _History:
    def __init__(self, *, quota=3, fail_symbol=None):
        self.quota = quota
        self.fail_symbol = fail_symbol
        self.calls = []

    def get_history_quota(self):
        return {"used_quota": 0, "remain_quota": self.quota, "downloaded_codes": []}

    def fetch_intraday(self, symbol, interval, *, start, end, before_request):
        before_request()
        self.calls.append((symbol, start, end))
        if start != end and symbol == self.fail_symbol:
            raise ValueError("provider disconnected")
        frame = pd.DataFrame([{
            "date": pd.Timestamp(f"{start} 09:31:00", tz="America/New_York").isoformat(),
            "open": 10, "high": 11, "low": 9, "close": 10,
            "volume": 100, "_source_time_key_count": 2,
        }])
        frame.attrs["page_count"] = 1
        return frame

    def _fetch_raw_data(self, symbol, start, end):
        return pd.DataFrame([{"date": start, "open": 10, "high": 11, "low": 9, "close": 10}])

    def _normalize_data(self, frame, symbol):
        return frame

    def get_adjustment_events(self, symbol):
        return []


def test_default_collection_freezes_all_69_and_exact_180_days(tmp_path, collection_clock):
    manifest = cli._collection_manifest(tmp_path / "raw", None)
    assert len(manifest["universe"]) == 69
    assert len(manifest["historical_sessions"]) == 180
    assert manifest["selection_end_session"] == "2026-10-09"
    assert manifest["minute_bar_label"] == "end"
    assert manifest["adjustment"]["consistency_verified"] is False


def test_preflight_reads_three_classes_without_writing(tmp_path, small_universe):
    root = tmp_path / "raw"
    provider = _History()
    result = cli.collect_moomoo_history(root, preflight_only=True, fetcher=provider)
    assert len(result["probes"]) == 6
    assert len({row["asset_class"] for row in result["probes"]}) == 3
    assert all(start == end for _, start, end in provider.calls)
    assert not root.exists()


def test_quota_failure_precedes_download_or_files(tmp_path, small_universe):
    provider = _History(quota=2)
    with pytest.raises(ValueError, match="history_quota_insufficient"):
        cli.collect_moomoo_history(tmp_path / "raw", fetcher=provider)
    assert provider.calls == []
    assert not (tmp_path / "raw").exists()


def test_collection_resume_is_by_whole_symbol_with_hashes_and_no_shards(tmp_path, small_universe):
    root = tmp_path / "raw"
    with pytest.raises(ValueError, match="provider disconnected"):
        cli.collect_moomoo_history(root, fetcher=_History(fail_symbol="TQQQ"))
    manifest = json.loads((root / "manifest.json").read_text())
    assert set(manifest["collection"]["symbols"]) == {"FXI"}
    original_hash = manifest["files"]["FXI"]["minute_sha256"]
    provider = _History()
    result = cli.collect_moomoo_history(root, fetcher=provider)
    manifest = json.loads((root / "manifest.json").read_text())
    assert result["completed_symbols"] == 3
    assert manifest["collection"]["status"] == "complete"
    assert manifest["files"]["FXI"]["minute_sha256"] == original_hash
    assert not any(symbol == "FXI" and start != end for symbol, start, end in provider.calls)
    assert len(list(root.iterdir())) == 7
    assert pd.read_csv(root / "FXI_1m.csv")["_source_time_key_count"].eq(2).all()
    chunks = manifest["collection"]["symbols"]["FXI"]["chunks"]
    assert len(chunks) == 9
    assert all(a["end"] < b["start"] for a, b in zip(chunks, chunks[1:]))
    assert result["adjustment_consistency_verified"] is False


def test_successful_empty_chunks_preserve_later_data_and_zero_coverage(tmp_path, small_universe):
    root = tmp_path / "raw"
    first_day = cli._collection_manifest(root, None)["historical_sessions"][0]

    class Gaps(_History):
        def fetch_intraday(self, symbol, interval, *, start, end, before_request):
            before_request()
            if start != end and (symbol == "TQQQ" or (symbol == "FXI" and start == first_day)):
                raise MoomooHistoryEmptyError("successful empty request")
            return super().fetch_intraday(symbol, interval, start=start, end=end, before_request=before_request)

        def _fetch_raw_data(self, symbol, start, end):
            if symbol == "TQQQ":
                raise MoomooHistoryEmptyError("successful empty request")
            return super()._fetch_raw_data(symbol, start, end)

    result = cli.collect_moomoo_history(root, fetcher=Gaps())
    manifest = json.loads((root / "manifest.json").read_text())
    assert result["completed_symbols"] == 3
    assert len(pd.read_csv(root / "FXI_1m.csv")) == 8
    assert manifest["collection"]["symbols"]["FXI"]["chunks"][0]["status"] == "provider_empty"
    assert pd.read_csv(root / "TQQQ_1m.csv").empty
    assert pd.read_csv(root / "TQQQ_daily.csv").empty
    assert manifest["collection"]["symbols"]["TQQQ"]["daily_status"] == "provider_empty"
    payload = cli.build_calibration_input_from_export_dir(root)
    coverage = [row for row in payload["coverage"] if row["symbol"] == "TQQQ"]
    assert len(coverage) == 60
    assert all(row["complete_minutes"] == 0 and row["eligible_minutes"] > 0 for row in coverage)


def test_provider_permission_failure_stops_batch_without_marking_gap(tmp_path, small_universe):
    class Denied(_History):
        def fetch_intraday(self, symbol, interval, *, start, end, before_request):
            if symbol == "TQQQ" and start != end:
                raise DataFetchError("permission denied")
            return super().fetch_intraday(symbol, interval, start=start, end=end, before_request=before_request)

    root = tmp_path / "raw"
    with pytest.raises(DataFetchError, match="permission denied"):
        cli.collect_moomoo_history(root, fetcher=Denied())
    manifest = json.loads((root / "manifest.json").read_text())
    assert set(manifest["collection"]["symbols"]) == {"FXI"}
    assert not (root / "TQQQ_1m.csv").exists()
    assert not (root / "AAPL_1m.csv").exists()


def test_changed_completed_csv_is_rejected_without_redownload(tmp_path, small_universe):
    root = tmp_path / "raw"
    cli.collect_moomoo_history(root, fetcher=_History())
    path = root / "FXI_1m.csv"
    path.write_text(path.read_text() + "\n")
    provider = _History()
    with pytest.raises(ValueError, match="collection_file_hash_mismatch"):
        cli.collect_moomoo_history(root, fetcher=provider)
    assert provider.calls == []


def test_t0_cannot_move_when_resuming(tmp_path, small_universe):
    root = tmp_path / "raw"
    cli.collect_moomoo_history(root, fetcher=_History())
    with pytest.raises(ValueError, match="selection_end_is_frozen"):
        cli.collect_moomoo_history(root, selection_end="2026-10-08", fetcher=_History())


def test_scan_hours_reject_before_provider_or_directory(tmp_path, small_universe, monkeypatch):
    monkeypatch.setattr(cli, "_now_utc", lambda: datetime(2026, 10, 9, 15, tzinfo=timezone.utc))
    provider = _History()
    with pytest.raises(ValueError, match="scan_window"):
        cli.collect_moomoo_history(tmp_path / "raw", fetcher=provider)
    assert provider.calls == []
    assert not (tmp_path / "raw").exists()


@pytest.mark.parametrize("per_symbol_adjustment", [False, True])
def test_shadow_enrollment_binds_frozen_bytes_and_does_not_calibrate(tmp_path, small_universe, collection_clock, monkeypatch, per_symbol_adjustment):
    manifest = cli._collection_manifest(tmp_path / "raw", None)
    path = tmp_path / "input.json"
    payload = {"schema_version": cli.P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION,
               "universe": list(small_universe), "historical_sessions": manifest["historical_sessions"],
               "universe_hash": cli.compute_universe_hash(small_universe), "signal_version": cli.P0_2M_SIGNAL_VERSION,
               "producer_metadata": {
                   "producer_version": cli.P0_2M_INPUT_PRODUCER_VERSION,
                   "raw_export_schema_version": cli.P0_2M_RAW_EXPORT_SCHEMA_VERSION,
                   "source": "MoomooFetcher", "manifest_sha256": "a" * 64,
                   "selection_end_session": manifest["selection_end_session"],
                   "frozen_at": manifest["frozen_at"], "derivation_errors": [],
                   "adjustment": {"autype": "QFQ", "consistency_verified": True},
                   "per_symbol": {s: {kind: {"sha256": "b" * 64} for kind in ("minute", "daily")} for s in small_universe},
               }}
    if per_symbol_adjustment:
        metadata = payload["producer_metadata"]
        metadata["adjustment_validation_scope"] = "per_symbol_collection"
        metadata["adjustment"].update({
            "consistency_verified": False, "snapshot_at": collection_clock.isoformat(),
            "verification_basis": "per_symbol_checks",
        })
        for symbol, provenance in metadata["per_symbol"].items():
            verified = symbol != "TQQQ"
            reasons = [] if verified else ["corporate_actions_require_point_in_time_reconstruction"]
            provenance.update({
                "adjustment": {"consistency_verified": verified, "reasons": reasons, "checked_sessions": 2, "corporate_actions": []},
                "evidence_eligible": verified, "evidence_exclusion_reasons": reasons,
            })
    path.write_text(json.dumps(payload))
    calls = []

    def enroll(**kwargs):
        calls.append(kwargs)
        return P02mShadowEnrollmentRecord(
            universe_hash=kwargs["universe_hash"], signal_version=kwargs["signal_version"],
            universe=kwargs["symbols"], sessions=(), enrolled_at=collection_clock,
            created_at=collection_clock, enrollment_key="new", supersedes_enrollment_key="old",
        )

    monkeypatch.setattr(cli, "start_new_p0_2m_live_shadow_run", enroll)
    result = cli.start_shadow_from_input(path, reason="previous run had sleep gaps")
    assert calls[0]["restart_request_id"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["supersedes_enrollment_key"] == "old"
    if per_symbol_adjustment:
        provenance = payload["producer_metadata"]["per_symbol"]["TQQQ"]
        original_check = provenance.pop("adjustment")
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="symbol_adjustment_unverifiable"):
            cli.start_shadow_from_input(path, reason="test")
        provenance["adjustment"] = original_check
        del provenance["minute"]["sha256"]
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="missing_raw_file_provenance"):
            cli.start_shadow_from_input(path, reason="test")
        payload["producer_metadata"]["source"] = "unknown"
        path.write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="requires_frozen_raw_export_provenance"):
            cli.start_shadow_from_input(path, reason="test")
        assert len(calls) == 1
    monkeypatch.setattr(cli, "_now_utc", lambda: datetime(2026, 10, 12, 15, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="session to close"):
        cli.start_shadow_from_input(path, reason="test")
    assert len(calls) == 1


def test_shadow_rejects_arbitrary_json_without_enrolling(tmp_path, small_universe, monkeypatch):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"schema_version": cli.P0_2M_CALIBRATION_INPUT_SCHEMA_VERSION, "universe": list(small_universe)}))
    monkeypatch.setattr(cli, "start_new_p0_2m_live_shadow_run", lambda **_kwargs: pytest.fail("must not enroll"))
    with pytest.raises(ValueError, match="identity_mismatch"):
        cli.start_shadow_from_input(path, reason="test")


@pytest.mark.parametrize("base_hash", [None, "b" * 64])
def test_hash_bound_merge_rejects_missing_or_wrong_base_before_attestation(monkeypatch, base_hash):
    universe = list(cli.DEFAULT_P0_UNIVERSE_69)
    live = {
        "schema_version": cli.P0_2M_SHADOW_EXPORT_SCHEMA_VERSION,
        "shadow_observation_mode": cli.LIVE_FORWARD_SHADOW_MODE,
        "signal_version": cli.P0_2M_SIGNAL_VERSION,
        "universe": universe, "universe_hash": cli.compute_universe_hash(universe),
        "export_attestation": {"enrollment_key": "e" * 64},
    }
    monkeypatch.setattr(cli, "_validate_live_shadow_sessions", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(cli, "_validate_live_shadow_enrollment", lambda *_args, **_kwargs: {"run_request_id": "a" * 64})
    monkeypatch.setattr(cli, "validate_p0_2m_live_shadow_export_attestation", lambda *_args, **_kwargs: pytest.fail("must reject before accessing DB"))
    with pytest.raises(ValueError, match="frozen --input|base_input_sha256"):
        cli.merge_live_shadow_input({"universe": universe}, live, base_input_sha256=base_hash)


def test_cli_passes_exact_base_file_hash_to_merge(tmp_path, monkeypatch):
    base, shadow = tmp_path / "input.json", tmp_path / "shadow.json"
    base.write_text('{ "frozen": true }\n')
    shadow.write_text("{}")
    calls = []

    def merge(payload, _live, **kwargs):
        calls.append(kwargs)
        return payload

    monkeypatch.setattr(cli, "merge_live_shadow_input", merge)
    monkeypatch.setattr(cli, "load_live_shadow_input", lambda _path: {})
    monkeypatch.setattr(cli, "calibrate_p0_2m", lambda *_args, **_kwargs: {"activation_ready": False})
    assert cli.main(["--input", str(base), "--live-shadow-input", str(shadow)]) == 1
    assert calls[0]["base_input_sha256"] == hashlib.sha256(base.read_bytes()).hexdigest()


def test_cli_rejects_base_modified_during_load(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    path.write_text("{}")
    original_load = cli.load_input

    def modified_load(input_path):
        payload = original_load(input_path)
        input_path.write_text('{"changed": true}')
        return payload

    monkeypatch.setattr(cli, "load_input", modified_load)
    monkeypatch.setattr(cli, "calibrate_p0_2m", lambda *_args, **_kwargs: pytest.fail("changed input must not be calibrated"))
    assert cli.main(["--input", str(path)]) == 2


@pytest.mark.parametrize("enrollment_location", ["top", "merge"])
def test_cli_rejects_changed_premerged_history_with_old_hash(tmp_path, monkeypatch, capsys, enrollment_location):
    path = tmp_path / "merged.json"
    enrollment = {"run_request_id": "a" * 64}
    payload = {
        "coverage": [{"complete_minutes": 390}],
        "events": [{"direction_adjusted_return_20m": 0.75}],
        "producer_metadata": {"live_shadow_merge": {"base_input_sha256": "a" * 64}},
    }
    if enrollment_location == "top":
        payload["shadow_enrollment"] = enrollment
    else:
        payload["producer_metadata"]["live_shadow_merge"]["enrollment"] = enrollment
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(cli, "calibrate_p0_2m", lambda *_args, **_kwargs: pytest.fail("premerged history must not be calibrated"))
    monkeypatch.setattr(cli, "attest_p0_2m_activation_artifact", lambda *_args, **_kwargs: pytest.fail("must not attest"))
    assert cli.main(["--input", str(path)]) == 2
    assert "premerged input cannot be calibrated" in capsys.readouterr().err


def test_cli_rejects_premerged_base_before_loading_another_export(tmp_path, monkeypatch, capsys):
    path = tmp_path / "merged.json"
    path.write_text(json.dumps({"producer_metadata": {"live_shadow_merge": {}}}))
    monkeypatch.setattr(cli, "load_live_shadow_input", lambda *_args: pytest.fail("must reject nested merge before loading export"))
    assert cli.main(["--input", str(path), "--live-shadow-input", str(tmp_path / "unused.json")]) == 2
    assert "nested shadow merges are not allowed" in capsys.readouterr().err


def test_cli_preserves_legacy_nonhash_premerged_input(tmp_path, monkeypatch):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "shadow_enrollment": {"run_request_id": "legacy-initial"},
        "producer_metadata": {"live_shadow_merge": {"enrollment": {"run_request_id": "legacy-initial"}}},
    }))
    monkeypatch.setattr(cli, "calibrate_p0_2m", lambda *_args, **_kwargs: {"activation_ready": False})
    assert cli.main(["--input", str(path)]) == 1


@pytest.mark.parametrize("args", [
    ["--input", "dummy", "--collect-moomoo"],
    ["--raw-export-dir", "dummy", "--start-shadow"],
    ["--input", "dummy", "--selection-end-session", "2026-10-09"],
    ["--raw-export-dir", "dummy", "--preflight-moomoo", "--output", "dummy"],
])
def test_cli_rejects_mixed_modes_before_io(args):
    assert cli.main(args) == 2
