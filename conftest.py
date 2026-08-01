# -*- coding: utf-8 -*-
"""Repo-root pytest fixtures shared by every test package.

P1.9（New-docs/HANDOFF.md §15）：套件里任何一个测试触发 ``get_config()`` 都会
``load_dotenv`` 把本机 ``.env``（例如 ``MOOMOO_OPEND_ENABLED=true``）注入
``os.environ``；此后所有「Moomoo 优先、fallback 兜底」的代码路径在
``pytest -m "not network"`` 里都可能打到真实 OpenD / 启动真实调度器。

系统性修复：下面的 autouse fixture 在每个**非 network** 测试开始前，把
live-integration 环境开关强制为 ``"false"``（不是删除——``load_dotenv``
默认 ``override=False``，已存在的值不会被 ``.env`` 覆盖，因此强制写
``"false"`` 同时挡住「测试中途才 load_dotenv」的路径）。

带 ``@pytest.mark.network`` 的测试保持进程环境原样，可继续 opt-in 真实
集成；单个测试仍可用自己的 ``monkeypatch.setenv(..., "true")`` 覆盖本
fixture（测试自身的 setenv 在本 fixture 之后执行）。
"""
from __future__ import annotations

import pytest

# 会触发真实外部副作用（OpenD 连接 / 后台调度器）的环境开关。
LIVE_INTEGRATION_ENV_SWITCHES = (
    "MOOMOO_OPEND_ENABLED",
    "MOOMOO_JOURNAL_REFRESH_ENABLED",
    "MOOMOO_PREMARKET_PREFETCH_ENABLED",
    "PREMARKET_RESEARCH_SCHEDULER_ENABLED",
    "OPPORTUNITY_OUTCOME_SCHEDULER_ENABLED",
)


@pytest.fixture(autouse=True)
def _isolate_live_integration_env(request, monkeypatch):
    """Force live-integration env switches off for non-network tests."""
    if request.node.get_closest_marker("network") is not None:
        # network 测试显式 opt-in 真实集成，保持进程环境不动。
        yield
        return
    for name in LIVE_INTEGRATION_ENV_SWITCHES:
        monkeypatch.setenv(name, "false")
    yield
