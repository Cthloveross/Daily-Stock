# -*- coding: utf-8 -*-
"""用户自身的标的黑名单（历史净亏损标的）——后端共享常量。

单一出处是用户自己的 Playbook 规则文本（与前端
``apps/dsa-web/src/components/opportunities/laneChecklist.ts`` 的
``BLACKLIST_TICKERS`` 同源同值，两处需保持同步）：

- R3「噪音时段与标的黑名单」：PLTR（−$5.3 万）与 QQQ（−$4.1 万）历史净亏损，
  进场前需额外理由；
- V2-E 附带禁令：「不得因『今天只有它有 0DTE』而交易黑名单标的」——周二/周四
  0DTE 中 QQQ 占 43/88 笔，毛 −0.45%；
- 个人画像回灌（G-16）里同口径的「漏斗」标的：PLTR / AMD / QQQ / SMCI。

它只用于**如实标注**（例如盘中提示附加「⚠️ 你的历史亏钱标的」），不排序、
不打分、不压制任何行——本系统只读，不阻止用户做任何事。
"""
from __future__ import annotations

BLACKLIST_TICKERS: tuple[str, ...] = ("PLTR", "AMD", "QQQ", "SMCI")

BLACKLIST_TICKER_SET = frozenset(BLACKLIST_TICKERS)

# 提示文案与前端「黑名单」语义一致：标注事实出处，不是行为禁令。
BLACKLIST_ALERT_TAG = "⚠️ 你的历史亏钱标的"

__all__ = [
    "BLACKLIST_ALERT_TAG",
    "BLACKLIST_TICKERS",
    "BLACKLIST_TICKER_SET",
]
