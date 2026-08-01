# -*- coding: utf-8 -*-
"""System configuration endpoints."""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.system_config import (
    DiscoverLLMChannelModelsRequest,
    DiscoverLLMChannelModelsResponse,
    ExportSystemConfigResponse,
    ImportSystemConfigRequest,
    SystemConfigConflictResponse,
    SystemConfigResponse,
    SystemConfigSchemaResponse,
    SystemConfigValidationErrorResponse,
    TestLLMChannelRequest,
    TestLLMChannelResponse,
    UpdateSystemConfigRequest,
    UpdateSystemConfigResponse,
    ValidateSystemConfigRequest,
    ValidateSystemConfigResponse,
)
from src.services.system_config_service import (
    ConfigConflictError,
    ConfigImportError,
    ConfigValidationError,
    SystemConfigService,
)
from src.services.moomoo_runtime import probe_opend_tcp

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_desktop_mode() -> None:
    """Restrict desktop backup/restore endpoints to desktop runtime only."""
    if os.getenv("DSA_DESKTOP_MODE", "").strip().lower() != "true":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "desktop_only_feature",
                "message": "This endpoint is only available in desktop mode",
            },
        )


@router.get(
    "/config",
    response_model=SystemConfigResponse,
    responses={
        200: {"description": "Configuration loaded"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration",
    description="Read current configuration from .env and return raw values.",
)
def get_system_config(
    include_schema: bool = Query(True, description="Whether to include schema metadata"),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigResponse:
    """Load and return current system configuration."""
    try:
        payload = service.get_config(include_schema=include_schema)
        return SystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load system configuration",
            },
        )


@router.put(
    "/config",
    response_model=UpdateSystemConfigResponse,
    responses={
        200: {"description": "Configuration updated"},
        400: {"description": "Validation failed", "model": SystemConfigValidationErrorResponse},
        409: {"description": "Version conflict", "model": SystemConfigConflictResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Update system configuration",
    description="Update key-value pairs in .env. Mask token preserves existing secret values.",
)
def update_system_config(
    request: UpdateSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    """Validate and persist system configuration updates."""
    try:
        payload = service.update(
            config_version=request.config_version,
            items=[item.model_dump() for item in request.items],
            mask_token=request.mask_token,
            reload_now=request.reload_now,
        )
        return UpdateSystemConfigResponse.model_validate(payload)
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "System configuration validation failed",
                "issues": exc.issues,
            },
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "config_version_conflict",
                "message": "Configuration has changed, please reload and retry",
                "current_config_version": exc.current_version,
            },
        )
    except Exception as exc:
        logger.error("Failed to update system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to update system configuration",
            },
        )


@router.get(
    "/config/export",
    response_model=ExportSystemConfigResponse,
    responses={
        200: {"description": "Desktop env exported"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Desktop mode only", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Export desktop env backup",
    description="Desktop-only endpoint that returns the raw saved .env content.",
)
def export_desktop_system_config(
    service: SystemConfigService = Depends(get_system_config_service),
) -> ExportSystemConfigResponse:
    """Export the active `.env` file for desktop backup."""
    _ensure_desktop_mode()
    try:
        payload = service.export_desktop_env()
        return ExportSystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to export desktop system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to export desktop system configuration",
            },
        )


@router.post(
    "/config/import",
    response_model=UpdateSystemConfigResponse,
    responses={
        200: {"description": "Desktop env imported"},
        400: {
            "description": "Import failed",
            "content": {
                "application/json": {
                    "schema": {
                        "anyOf": [
                            {"$ref": "#/components/schemas/ErrorResponse"},
                            {"$ref": "#/components/schemas/SystemConfigValidationErrorResponse"},
                        ]
                    }
                }
            },
        },
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Desktop mode only", "model": ErrorResponse},
        409: {"description": "Version conflict", "model": SystemConfigConflictResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Import desktop env backup",
    description="Desktop-only endpoint that merges raw .env text into the saved configuration.",
)
def import_desktop_system_config(
    request: ImportSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    """Import a desktop `.env` backup into the active config."""
    _ensure_desktop_mode()
    try:
        payload = service.import_desktop_env(
            config_version=request.config_version,
            content=request.content,
            reload_now=request.reload_now,
        )
        return UpdateSystemConfigResponse.model_validate(payload)
    except ConfigImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_import_file",
                "message": exc.message,
            },
        )
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "System configuration validation failed",
                "issues": exc.issues,
            },
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "config_version_conflict",
                "message": "Configuration has changed, please reload and retry",
                "current_config_version": exc.current_version,
            },
        )
    except Exception as exc:
        logger.error("Failed to import desktop system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to import desktop system configuration",
            },
        )


@router.post(
    "/config/validate",
    response_model=ValidateSystemConfigResponse,
    responses={
        200: {"description": "Validation completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Validate system configuration",
    description="Validate submitted configuration values without writing to .env.",
)
def validate_system_config(
    request: ValidateSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> ValidateSystemConfigResponse:
    """Run pre-save validation only."""
    try:
        payload = service.validate(items=[item.model_dump() for item in request.items])
        return ValidateSystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to validate system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to validate system configuration",
            },
        )


@router.post(
    "/config/llm/test-channel",
    response_model=TestLLMChannelResponse,
    responses={
        200: {"description": "Channel test completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Test one LLM channel",
    description="Run a minimal LLM request against one unsaved or saved channel definition.",
)
def test_llm_channel(
    request: TestLLMChannelRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> TestLLMChannelResponse:
    """Validate and test one channel definition without writing `.env`."""
    try:
        payload = service.test_llm_channel(
            name=request.name,
            protocol=request.protocol,
            base_url=request.base_url,
            api_key=request.api_key,
            models=request.models,
            enabled=request.enabled,
            timeout_seconds=request.timeout_seconds,
        )
        return TestLLMChannelResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Failed to test LLM channel: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to test LLM channel",
            },
        )


@router.post(
    "/config/llm/discover-models",
    response_model=DiscoverLLMChannelModelsResponse,
    responses={
        200: {"description": "Model discovery completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Discover models for one LLM channel",
    description="Call one unsaved or saved channel's `/models` endpoint and return discovered model IDs.",
)
def discover_llm_channel_models(
    request: DiscoverLLMChannelModelsRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> DiscoverLLMChannelModelsResponse:
    """Discover models for one channel definition without writing `.env`."""
    try:
        payload = service.discover_llm_channel_models(
            name=request.name,
            protocol=request.protocol,
            base_url=request.base_url,
            api_key=request.api_key,
            models=request.models,
            timeout_seconds=request.timeout_seconds,
        )
        return DiscoverLLMChannelModelsResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": str(exc),
            },
        )
    except Exception as exc:
        logger.error("Failed to discover LLM channel models: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to discover LLM channel models",
            },
        )


@router.get(
    "/config/schema",
    response_model=SystemConfigSchemaResponse,
    responses={
        200: {"description": "Schema loaded"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration schema",
    description="Return categorized field metadata used for dynamic settings form rendering.",
)
def get_system_config_schema(
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigSchemaResponse:
    """Return schema metadata for system configuration fields."""
    try:
        payload = service.get_schema()
        return SystemConfigSchemaResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration schema: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load system configuration schema",
            },
        )


# --- Moomoo OpenD live status (Phase A/B/C/D indicator for the UI) ---


@router.get(
    "/moomoo-status",
    summary="Moomoo OpenD live status",
    description=(
        "Lightweight probe used by the frontend TopBar badge. Returns whether "
        "the Moomoo OpenD daemon is reachable and whether the SDK is wired up. "
        "Bounded TCP-only check — no SDK context or quota cost. Browser clients "
        "should coordinate polling across tabs instead of creating one timer "
        "per mounted badge."
    ),
)
def get_moomoo_status() -> dict:
    """Return a small dict describing the live Moomoo integration health."""
    import os

    enabled = (os.environ.get("MOOMOO_OPEND_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    host = (os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip()
    try:
        port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
    except ValueError:
        port = 11111
    trd_env = (os.environ.get("MOOMOO_TRADE_ENV") or "SIMULATE").upper()

    if not enabled:
        return {
            "enabled": False,
            "sdk_installed": False,
            "connected": False,
            "read_only": True,
            "probe_level": "tcp",
            "host": host,
            "port": port,
            "trd_env": trd_env,
            "message": "MOOMOO_OPEND_ENABLED is false; using yfinance fallback",
        }

    # SDK probe — check for a real symbol, not just `import moomoo` (which can
    # resolve to an empty namespace package — see MoomooFetcher comment).
    try:
        import moomoo as moomoo_sdk

        sdk_ok = hasattr(moomoo_sdk, "OpenQuoteContext")
        if not sdk_ok:
            raise ImportError("OpenQuoteContext missing")
        sdk_version = getattr(moomoo_sdk, "__version__", None)
    except (ImportError, AttributeError):
        return {
            "enabled": True,
            "sdk_installed": False,
            "connected": False,
            "read_only": True,
            "probe_level": "tcp",
            "host": host,
            "port": port,
            "trd_env": trd_env,
            "message": "moomoo-api SDK not installed",
        }
    except Exception as exc:  # noqa: BLE001 - surface an SDK import failure safely
        return {
            "enabled": True,
            "sdk_installed": False,
            "connected": False,
            "read_only": True,
            "probe_level": "tcp",
            "host": host,
            "port": port,
            "trd_env": trd_env,
            "message": f"moomoo-api SDK import failed: {type(exc).__name__}",
        }

    # Never construct DataFetcherManager or an SDK context from a polling
    # endpoint.  A 500 ms TCP deadline keeps the API worker available even
    # while OpenD is offline.
    connected = probe_opend_tcp(host, port)

    return {
        "enabled": True,
        "sdk_installed": sdk_ok,
        "connected": connected,
        "read_only": True,
        "probe_level": "tcp",
        "host": host,
        "port": port,
        "trd_env": trd_env,
        "sdk_version": sdk_version,
        "message": (
            "OpenD TCP endpoint reachable"
            if connected
            else "OpenD daemon not reachable — open the OpenD app and log in"
        ),
    }


# --- Layered health（§11.3：把“一个绿点”拆成可解释的分层状态） ---


def _health_layer(layer: str, state: str, detail: str, **extra) -> dict:
    return {"layer": layer, "state": state, "detail": detail, **extra}


@router.get(
    "/health-layers",
    summary="Layered system health",
    description=(
        "Read-only, bounded probes for each health domain: API process, OpenD "
        "TCP, Moomoo SDK, Journal refresh configuration, premarket scheduler "
        "last publication, outcome maintenance last run, and official economic "
        "schedule coverage (annual data-file renewal early warning). One layer "
        "failing degrades only that layer; the endpoint itself never 500s on "
        "probe errors. States: ok | degraded | down | disabled | unknown."
    ),
)
def get_health_layers() -> dict:
    import os
    from datetime import date, datetime, timezone

    layers: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    layers.append(_health_layer("api_process", "ok", "API 进程存活", as_of=now_iso))

    enabled = (os.environ.get("MOOMOO_OPEND_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    host = (os.environ.get("MOOMOO_OPEND_HOST") or "127.0.0.1").strip()
    try:
        port = int((os.environ.get("MOOMOO_OPEND_PORT") or "11111").strip())
    except ValueError:
        port = 11111

    if not enabled:
        layers.append(_health_layer(
            "opend_tcp", "disabled", "MOOMOO_OPEND_ENABLED=false", as_of=now_iso,
        ))
        layers.append(_health_layer(
            "moomoo_sdk", "disabled", "OpenD 未启用，SDK 不参与", as_of=now_iso,
        ))
    else:
        try:
            from src.services.moomoo_runtime import probe_opend_tcp

            reachable = probe_opend_tcp(host, port)
            layers.append(_health_layer(
                "opend_tcp",
                "ok" if reachable else "down",
                f"{host}:{port} " + ("TCP 可达（不代表已登录/有权限）" if reachable else "TCP 不可达"),
                as_of=now_iso,
            ))
        except Exception as exc:  # noqa: BLE001 - probe failure is a health signal
            layers.append(_health_layer(
                "opend_tcp", "unknown", f"探测异常: {type(exc).__name__}", as_of=now_iso,
            ))
        try:
            import moomoo as moomoo_sdk

            sdk_ok = hasattr(moomoo_sdk, "OpenQuoteContext")
            layers.append(_health_layer(
                "moomoo_sdk",
                "ok" if sdk_ok else "down",
                ("SDK 就绪 " + str(getattr(moomoo_sdk, "__version__", ""))) if sdk_ok
                else "moomoo 包缺少 OpenQuoteContext",
                as_of=now_iso,
            ))
        except Exception as exc:  # noqa: BLE001
            layers.append(_health_layer(
                "moomoo_sdk", "down", f"SDK 导入失败: {type(exc).__name__}", as_of=now_iso,
            ))

    refresh_enabled = (os.environ.get("MOOMOO_JOURNAL_REFRESH_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not refresh_enabled:
        layers.append(_health_layer(
            "journal_refresh_config", "disabled",
            "MOOMOO_JOURNAL_REFRESH_ENABLED=false", as_of=now_iso,
        ))
    else:
        journal_env = (os.environ.get("MOOMOO_JOURNAL_ENV") or "").strip().upper()
        secret_ok = len((os.environ.get("MOOMOO_JOURNAL_ACCOUNT_BINDING_SECRET") or "").strip()) >= 32
        problems = []
        if journal_env != "LIVE":
            problems.append("MOOMOO_JOURNAL_ENV 不是 LIVE")
        if not secret_ok:
            problems.append("binding secret 缺失或短于 32 字符")
        layers.append(_health_layer(
            "journal_refresh_config",
            "ok" if not problems else "degraded",
            "配置就绪（只读刷新可用）" if not problems else "；".join(problems),
            as_of=now_iso,
        ))

    try:
        from src.config import get_config

        cfg = get_config()
        premarket_enabled = bool(getattr(cfg, "premarket_research_scheduler_enabled", False))
        outcome_enabled = bool(getattr(cfg, "opportunity_outcome_scheduler_enabled", False))
    except Exception:  # noqa: BLE001 - config load failure must not break health
        premarket_enabled = None
        outcome_enabled = None

    try:
        from src.opportunities.repository import list_snapshots

        snapshots = list_snapshots(limit=1)
        latest = snapshots[0] if snapshots else None
        if latest is None:
            layers.append(_health_layer(
                "premarket_publication",
                "disabled" if premarket_enabled is False else "degraded",
                "尚无任何官方盘前发布", as_of=now_iso,
                scheduler_enabled=premarket_enabled,
            ))
        else:
            layers.append(_health_layer(
                "premarket_publication", "ok",
                f"最新发布 {latest.market_date_et}（冻结于 {latest.frozen_at.isoformat()}）",
                as_of=now_iso,
                scheduler_enabled=premarket_enabled,
                snapshot_key=latest.snapshot_key,
            ))
    except Exception as exc:  # noqa: BLE001
        layers.append(_health_layer(
            "premarket_publication", "unknown",
            f"读取失败: {type(exc).__name__}", as_of=now_iso,
        ))

    try:
        from src.opportunities.maintenance_repository import (
            get_latest_outcome_maintenance,
        )

        run = get_latest_outcome_maintenance()
        if run is None:
            layers.append(_health_layer(
                "outcome_maintenance",
                "disabled" if outcome_enabled is False else "degraded",
                "尚无 outcome 维护记录", as_of=now_iso,
                scheduler_enabled=outcome_enabled,
            ))
        else:
            layers.append(_health_layer(
                "outcome_maintenance", "ok",
                f"最近维护 session {run.session_date_et}",
                as_of=now_iso,
                scheduler_enabled=outcome_enabled,
            ))
    except Exception as exc:  # noqa: BLE001
        layers.append(_health_layer(
            "outcome_maintenance", "unknown",
            f"读取失败: {type(exc).__name__}", as_of=now_iso,
        ))

    # E-5 年度日程续期预警：官方 Fed/BLS 日程按年发布，数据文件覆盖到期后
    # events 域会 fail-closed 降级。这里提前 30 天在健康层给出运维提醒。
    try:
        from src.regime.official_schedule import get_cached_official_schedule

        schedule = get_cached_official_schedule()
        if schedule is None:
            layers.append(_health_layer(
                "economic_schedule_coverage", "down",
                "官方经济日程数据文件缺失或不可用（events 域将按 fail-closed 降级）",
                as_of=now_iso,
            ))
        else:
            coverage_through = schedule.coverage_through
            days_remaining = (coverage_through - date.today()).days
            if days_remaining < 0:
                state = "down"
                detail = (
                    f"官方经济日程已超出覆盖范围（覆盖至 {coverage_through.isoformat()}），"
                    "请放入下一年度数据文件"
                )
            elif days_remaining <= 30:
                state = "degraded"
                detail = (
                    f"官方经济日程覆盖至 {coverage_through.isoformat()}，"
                    "请在到期前放入下一年度数据文件"
                )
            else:
                state = "ok"
                detail = f"官方经济日程覆盖至 {coverage_through.isoformat()}"
            layers.append(_health_layer(
                "economic_schedule_coverage", state, detail,
                as_of=now_iso,
                coverage_through=coverage_through.isoformat(),
                days_remaining=days_remaining,
            ))
    except Exception as exc:  # noqa: BLE001 - schedule read failure is a health signal
        layers.append(_health_layer(
            "economic_schedule_coverage", "unknown",
            f"读取失败: {type(exc).__name__}", as_of=now_iso,
        ))

    states = [layer["state"] for layer in layers]
    if any(state == "down" for state in states):
        overall = "down"
    elif any(state in {"degraded", "unknown"} for state in states):
        overall = "degraded"
    else:
        overall = "ok"

    return {
        "schema_version": "system-health-layers/1.0",
        "generated_at": now_iso,
        "overall": overall,
        "layers": layers,
    }
