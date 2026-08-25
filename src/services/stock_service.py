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
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List

from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)

# 进程级共享 DataFetcherManager（盘中波段爆发车道修复）：
# 历史实现每次请求（波段爆发甚至每标的、每 60 秒轮询）都新建 manager →
# 新建 MoomooFetcher → 新的 OpenD 连接握手，把轮询变成连接放大器。
# DataFetcherManager 自身线程安全（逐 fetcher 调用锁），MoomooFetcher 的
# ctx 每次调用都做健康检查并自愈重连，因此进程级单例无需显式 close。
# 按类身份缓存：测试 monkeypatch ``data_provider.base.DataFetcherManager``
# 时自动重建，互不串味。
_shared_manager_lock = threading.Lock()
_shared_manager: Optional[Any] = None
_shared_manager_cls: Optional[Any] = None


def _get_shared_fetcher_manager():
    """Return the process-wide DataFetcherManager, rebuilding on class swap."""

    global _shared_manager, _shared_manager_cls
    from data_provider.base import DataFetcherManager

    with _shared_manager_lock:
        if _shared_manager is None or _shared_manager_cls is not DataFetcherManager:
            _shared_manager = DataFetcherManager()
            _shared_manager_cls = DataFetcherManager
        return _shared_manager


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
            # 调用数据获取器获取实时行情（进程级共享 manager，见模块注释）
            manager = _get_shared_fetcher_manager()
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
    _INTRADAY_INTERVALS = {
        "1m",
        "2m",
        "5m",
        "15m",
        "30m",
        "60m",
        "90m",
        "1h",
    }
    _RESAMPLE_RULES = {
        "weekly": "W-FRI",
        "monthly": "ME",
    }

    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30,
        *,
        include_stock_name: bool = True,
    ) -> Dict[str, Any]:
        """
        获取股票历史行情

        Args:
            stock_code: 股票代码
            period: K 线周期
                - daily: 日线
                - weekly / monthly: 基于日线 resample 聚合
                - 1m / 2m / 5m / 15m / 30m / 60m / 90m / 1h: 分钟级
                  （2m 基于带时区的 1m K 线聚合）
            days: 获取天数（周/月会按聚合因子放大，intraday 受 yfinance 上限约束）
            include_stock_name: 是否额外查询证券名称。只需要 OHLCV 的内部调用可关闭，
                避免一次与行情复盘无关的实时报价请求。

        Returns:
            历史行情数据字典

        Raises:
            ValueError: 不支持的 period
        """
        if period in self._INTRADAY_INTERVALS:
            return self._get_intraday_history(
                stock_code,
                period,
                days,
                include_stock_name=include_stock_name,
            )

        if period in ("daily",) or period in self._RESAMPLE_RULES:
            return self._get_daily_or_resampled_history(
                stock_code,
                period,
                days,
                include_stock_name=include_stock_name,
            )

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
        *,
        include_stock_name: bool,
    ) -> Dict[str, Any]:
        """日线 / 周线（W-FRI 聚合）/ 月线（ME 聚合）"""
        try:
            manager = _get_shared_fetcher_manager()

            # 周/月需要更多日线作为聚合原料
            if period == "weekly":
                fetch_days = max(days * 7 + 30, 200)
            elif period == "monthly":
                fetch_days = max(days * 31 + 60, 400)
            else:
                fetch_days = days

            df, source = manager.get_daily_data(stock_code, days=fetch_days)
            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return self._history_result(stock_code, period, [])

            stock_name = (
                manager.get_stock_name(stock_code)
                if include_stock_name
                else None
            )

            if period in self._RESAMPLE_RULES:
                df = self._resample_ohlcv(df, self._RESAMPLE_RULES[period])
                # 聚合后只保留最新 `days` 根
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)

            data = [self._row_to_kline(row, intraday=False) for _, row in df.iterrows()]

            return self._history_result(
                stock_code,
                period,
                data,
                stock_name=stock_name,
                source=source,
            )

        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return self._history_result(stock_code, period, [])
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return self._history_result(stock_code, period, [])

    def _get_intraday_history(
        self,
        stock_code: str,
        interval: str,
        days: int,
        *,
        include_stock_name: bool,
    ) -> Dict[str, Any]:
        """分钟级 K 线（仅美股）。"""
        derived_from_period = "1m" if interval == "2m" else None
        aggregation_method = (
            "time_bucket_2m_ohlcv" if interval == "2m" else None
        )
        try:
            from data_provider.base import DataFetchError
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return self._history_result(
                stock_code,
                interval,
                [],
                derived_from_period=derived_from_period,
                aggregation_method=aggregation_method,
            )

        manager = _get_shared_fetcher_manager()
        source_interval = "1m" if interval == "2m" else interval
        try:
            df, source = manager.get_intraday_data(
                stock_code,
                interval=source_interval,
                days=days,
            )
        except DataFetchError as e:
            # 非美股 / fetcher 缺失 → 转为 422 由上层处理
            raise ValueError(str(e)) from e
        except ValueError:
            # interval 非法 → 直接抛，上层转 422
            raise

        if df is None or df.empty:
            return self._history_result(
                stock_code,
                interval,
                [],
                derived_from_period=derived_from_period,
                aggregation_method=aggregation_method,
            )

        if interval == "2m":
            df = self._resample_intraday_ohlcv(df, "2min")
            if df.empty:
                return self._history_result(
                    stock_code,
                    interval,
                    [],
                    derived_from_period=derived_from_period,
                    aggregation_method=aggregation_method,
                )

        stock_name = (
            manager.get_stock_name(stock_code)
            if include_stock_name
            else None
        )
        data = [self._row_to_kline(row, intraday=True) for _, row in df.iterrows()]

        return self._history_result(
            stock_code,
            interval,
            data,
            stock_name=stock_name,
            source=source,
            derived_from_period=derived_from_period,
            aggregation_method=aggregation_method,
        )

    @staticmethod
    def _history_result(
        stock_code: str,
        period: str,
        data: List[Dict[str, Any]],
        *,
        stock_name: Optional[str] = None,
        source: Optional[str] = None,
        derived_from_period: Optional[str] = None,
        aggregation_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build history output and derive provenance from returned bars only."""
        result: Dict[str, Any] = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "period": period,
            "data": data,
            "source": None,
            "coverage_start": None,
            "coverage_end": None,
            "last_bar_at": None,
            "derived_from_period": derived_from_period,
            "aggregation_method": aggregation_method,
        }
        timestamps = [
            str(item.get("date")).strip()
            for item in data
            if item.get("date") is not None and str(item.get("date")).strip()
        ]
        if not timestamps:
            return result

        result.update(
            source=source,
            coverage_start=min(timestamps),
            coverage_end=max(timestamps),
            last_bar_at=max(timestamps),
            derived_from_period=derived_from_period,
            aggregation_method=aggregation_method,
        )
        return result

    @staticmethod
    def _resample_intraday_ohlcv(df, rule: str):
        """Aggregate timezone-aware intraday bars without crossing local dates.

        Each market-local trading date is resampled independently. Empty time
        buckets are discarded, so overnight and midday session gaps cannot be
        combined into one derived candle.
        """
        import pandas as pd

        work = df.copy()

        def _aware_timestamp(value):
            timestamp = pd.to_datetime(value, errors="coerce")
            if pd.isna(timestamp) or timestamp.tzinfo is None:
                return pd.NaT
            return timestamp

        work["_ts"] = work["date"].map(_aware_timestamp)
        work = work.dropna(subset=["_ts", "close"])
        if work.empty:
            return work.drop(columns=["_ts"], errors="ignore")

        work["_local_date"] = work["_ts"].map(
            lambda value: value.strftime("%Y-%m-%d")
        )
        aggregation = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
        }

        def _sum_with_missing(series):
            return series.sum(min_count=1)

        if "volume" in work.columns:
            aggregation["volume"] = _sum_with_missing
        if "amount" in work.columns:
            aggregation["amount"] = _sum_with_missing

        chunks = []
        for _, daily in work.groupby("_local_date", sort=True):
            daily = daily.sort_values("_ts").copy()
            timestamps = pd.DatetimeIndex(daily.pop("_ts").tolist())
            daily.index = timestamps
            daily.index.name = "_ts"
            resampled = daily.resample(
                rule,
                origin="start_day",
                closed="left",
                label="left",
            ).agg(aggregation)
            resampled = resampled.dropna(subset=["close"])
            if resampled.empty:
                continue
            resampled["date"] = [value.isoformat() for value in resampled.index]
            chunks.append(resampled.reset_index(drop=True))

        if not chunks:
            return work.iloc[0:0].drop(
                columns=["_ts", "_local_date"],
                errors="ignore",
            )

        result = pd.concat(chunks, ignore_index=True)
        result["pct_chg"] = (
            result["close"].pct_change() * 100
        ).fillna(0).round(2)
        return result

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
