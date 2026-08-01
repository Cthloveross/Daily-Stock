# -*- coding: utf-8 -*-
"""P1.9 回归：根 conftest 必须为非 network 测试剥离 live-integration 开关。

本文件**故意不做任何本地 env patch**：它直接证明即使宿主 shell 导出了
``MOOMOO_OPEND_ENABLED=true``（或 ``.env`` 已被 ``load_dotenv`` 注入），
未自带防护的测试文件也不会再打到真实 OpenD / 触发真实调度器。

验证方式（HANDOFF §15 P1.9）::

    MOOMOO_OPEND_ENABLED=true python -m pytest tests/test_env_isolation_conftest.py -q
"""
from __future__ import annotations

import os

from conftest import LIVE_INTEGRATION_ENV_SWITCHES


def test_live_integration_switches_forced_false_for_plain_tests():
    """未打 network 标记、也未自行 patch 的测试必须看到全部开关为 false。"""
    for name in LIVE_INTEGRATION_ENV_SWITCHES:
        assert os.environ.get(name) == "false", (
            f"{name} 未被根 conftest 强制为 false；"
            "非 network 测试可能打到真实 OpenD 或启动真实调度器"
        )


def test_moomoo_options_enabled_gate_reads_false_without_local_patch():
    """真实 env 消费方（moomoo_options._enabled）在未打补丁时必须被禁用。

    这是 Moomoo-first 期权链路的总闸：conftest 生效时它必须返回 False，
    即使宿主环境导出了 MOOMOO_OPEND_ENABLED=true。
    """
    from data_provider.moomoo_options import _enabled

    assert _enabled() is False


def test_single_test_can_still_opt_in_via_its_own_monkeypatch(monkeypatch):
    """测试自身的 setenv 在根 conftest 之后执行，opt-in 能力保持不变。"""
    from data_provider.moomoo_options import _enabled

    monkeypatch.setenv("MOOMOO_OPEND_ENABLED", "true")
    assert _enabled() is True
