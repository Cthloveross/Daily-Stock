from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from api.v1.endpoints.journal import sync_live
from api.v1.schemas.journal import MoomooSyncRequest
from scripts import sync_moomoo_live


def test_legacy_sync_endpoint_refuses_before_opening_sdk_or_database() -> None:
    with pytest.raises(HTTPException) as caught:
        sync_live(MoomooSyncRequest(window_days=7, market="US"))

    assert caught.value.status_code == 409
    assert "read-only" in str(caught.value.detail)


def test_legacy_sync_cli_is_a_safe_non_writing_sentinel(capsys) -> None:
    assert sync_moomoo_live.main([]) == 3

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["kind"] == "LegacySyncPaused"
    assert payload["journal_database_written"] is False
