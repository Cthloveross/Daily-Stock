# -*- coding: utf-8 -*-
"""
===================================
股票数据服务层
===================================

职责：
1. 封装股票数据获取逻辑
2. 提供实时行情和历史数据接口
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


class StockService:
    """
    股票数据服务
    
    封装股票数据获取的业务逻辑
    """
    
    def __init__(self):
        """初始化股票数据服务"""
        self.repo = StockRepository()
    
    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取股票实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            实时行情数据字典
        """
        try:
            # 调用数据获取器获取实时行情
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None
            
            # UnifiedRealtimeQuote 是 dataclass，使用 getattr 安全访问字段
            # 字段映射: UnifiedRealtimeQuote -> API 响应
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "update_time": datetime.now().isoformat(),
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，使用占位数据")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None
    
    # 支持的 K 线周期分类
    _INTRADAY_INTERVALS = {"1m", "5m", "15m", "30m", "60m", "90m", "1h"}
    _RESAMPLE_RULES = {
        "weekly": "W-FRI",
        "monthly": "ME",
    }

    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        获取股票历史行情

        Args:
            stock_code: 股票代码
            period: K 线周期
                - daily: 日线
                - weekly / monthly: 基于日线 resample 聚合
                - 1m / 5m / 15m / 30m / 60m / 90m / 1h: 分钟级（仅美股 via yfinance）
            days: 获取天数（周/月会按聚合因子放大，intraday 受 yfinance 上限约束）

        Returns:
            历史行情数据字典

        Raises:
            ValueError: 不支持的 period
        """
        if period in self._INTRADAY_INTERVALS:
            return self._get_intraday_history(stock_code, period, days)

        if period in ("daily",) or period in self._RESAMPLE_RULES:
            return self._get_daily_or_resampled_history(stock_code, period, days)

        raise ValueError(
            f"不支持的 K 线周期 '{period}'。"
            f"支持列表：daily/weekly/monthly/"
            f"{'/'.join(sorted(self._INTRADAY_INTERVALS))}"
        )

    def _get_daily_or_resampled_history(
        self,
        stock_code: str,
        period: str,
        days: int,
    ) -> Dict[str, Any]:
        """日线 / 周线（W-FRI 聚合）/ 月线（ME 聚合）"""
        try:
            from data_provider.base import DataFetcherManager

            manager = DataFetcherManager()

            # 周/月需要更多日线作为聚合原料
            if period == "weekly":
                fetch_days = max(days * 7 + 30, 200)
            elif period == "monthly":
                fetch_days = max(days * 31 + 60, 400)
            else:
                fetch_days = days

            df, _source = manager.get_daily_data(stock_code, days=fetch_days)
            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return {"stock_code": stock_code, "period": period, "data": []}

            stock_name = manager.get_stock_name(stock_code)

            if period in self._RESAMPLE_RULES:
                df = self._resample_ohlcv(df, self._RESAMPLE_RULES[period])
                # 聚合后只保留最新 `days` 根
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)

            data = [self._row_to_kline(row, intraday=False) for _, row in df.iterrows()]

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": period,
                "data": data,
            }

        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}

    def _get_intraday_history(
        self,
        stock_code: str,
        interval: str,
        days: int,
    ) -> Dict[str, Any]:
        """分钟级 K 线（仅美股）。"""
        try:
            from data_provider.base import DataFetcherManager, DataFetchError
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": interval, "data": []}

        manager = DataFetcherManager()
        try:
            df, _source = manager.get_intraday_data(stock_code, interval=interval, days=days)
        except DataFetchError as e:
            # 非美股 / fetcher 缺失 → 转为 422 由上层处理
            raise ValueError(str(e)) from e
        except ValueError:
            # interval 非法 → 直接抛，上层转 422
            raise

        if df is None or df.empty:
            return {"stock_code": stock_code, "period": interval, "data": []}

        stock_name = manager.get_stock_name(stock_code)
        data = [self._row_to_kline(row, intraday=True) for _, row in df.iterrows()]

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "period": interval,
            "data": data,
        }

    @staticmethod
    def _resample_ohlcv(df, rule: str):
        """Resample daily OHLCV to weekly/monthly bars."""
        import pandas as pd

        work = df.copy()
        work["_ts"] = pd.to_datetime(work["date"], errors="coerce")
        work = work.dropna(subset=["_ts", "close"]).set_index("_ts")

        agg = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }
        if "volume" in work.columns:
            agg["volume"] = "sum"
        if "amount" in work.columns:
            agg["amount"] = "sum"

        resampled = work.resample(rule).agg(agg).dropna(subset=["close"]).reset_index()
        resampled["date"] = resampled["_ts"].dt.strftime("%Y-%m-%d")
        resampled["pct_chg"] = (resampled["close"].pct_change() * 100).fillna(0).round(2)
        return resampled.drop(columns=["_ts"])

    @staticmethod
    def _row_to_kline(row, *, intraday: bool) -> Dict[str, Any]:
        """将 DataFrame row 转成响应字典。"""
        date_val = row.get("date")
        if intraday:
            # intraday 的 date 已经是 ISO 8601 字符串（来自 yfinance_fetcher._normalize_intraday）
            date_str = str(date_val) if date_val is not None else ""
        else:
            if hasattr(date_val, "strftime"):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val) if date_val is not None else ""

        def _opt_float(v):
            if v is None:
                return None
            try:
                import pandas as pd

                if pd.isna(v):
                    return None
            except Exception:
                pass
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        return {
            "date": date_str,
            "open": _opt_float(row.get("open")) or 0.0,
            "high": _opt_float(row.get("high")) or 0.0,
            "low": _opt_float(row.get("low")) or 0.0,
            "close": _opt_float(row.get("close")) or 0.0,
            "volume": _opt_float(row.get("volume")),
            "amount": _opt_float(row.get("amount")),
            "change_percent": _opt_float(row.get("pct_chg")),
        }
    
    def _get_placeholder_quote(self, stock_code: str) -> Dict[str, Any]:
        """
        获取占位行情数据（用于测试）
        
        Args:
            stock_code: 股票代码
            
        Returns:
            占位行情数据
        """
        return {
            "stock_code": stock_code,
            "stock_name": f"股票{stock_code}",
            "current_price": 0.0,
            "change": None,
            "change_percent": None,
            "open": None,
            "high": None,
            "low": None,
            "prev_close": None,
            "volume": None,
            "amount": None,
            "update_time": datetime.now().isoformat(),
        }
