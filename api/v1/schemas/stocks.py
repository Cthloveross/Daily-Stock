# -*- coding: utf-8 -*-
"""
===================================
股票数据相关模型
===================================

职责：
1. 定义股票实时行情模型
2. 定义历史 K 线数据模型
"""

from typing import Optional, List

from pydantic import BaseModel, Field


class StockQuote(BaseModel):
    """股票实时行情"""
    
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    current_price: float = Field(..., description="当前价格")
    change: Optional[float] = Field(None, description="涨跌额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    prev_close: Optional[float] = Field(None, description="昨收价")
    volume: Optional[float] = Field(None, description="成交量（股）")
    amount: Optional[float] = Field(None, description="成交额（元）")
    update_time: Optional[str] = Field(None, description="更新时间")
    
    class Config:
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "current_price": 1800.00,
                "change": 15.00,
                "change_percent": 0.84,
                "open": 1785.00,
                "high": 1810.00,
                "low": 1780.00,
                "prev_close": 1785.00,
                "volume": 10000000,
                "amount": 18000000000,
                "update_time": "2024-01-01T15:00:00"
            }
        }


class KLineData(BaseModel):
    """K 线数据点"""

    date: str = Field(..., description="日期（日/周/月周期为 YYYY-MM-DD；分钟级周期为 ISO 8601 datetime，含时区）")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: Optional[float] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "date": "2024-01-01",
                "open": 1785.00,
                "high": 1810.00,
                "low": 1780.00,
                "close": 1800.00,
                "volume": 10000000,
                "amount": 18000000000,
                "change_percent": 0.84
            }
        }


class ExtractItem(BaseModel):
    """单条提取结果（代码、名称、置信度）"""

    code: Optional[str] = Field(None, description="股票代码，None 表示解析失败")
    name: Optional[str] = Field(None, description="股票名称（如有）")
    confidence: str = Field("medium", description="置信度：high/medium/low")


class ExtractFromImageResponse(BaseModel):
    """图片股票代码提取响应"""

    codes: List[str] = Field(..., description="提取的股票代码（已去重，向后兼容）")
    items: List[ExtractItem] = Field(default_factory=list, description="提取结果明细（代码+名称+置信度）")
    raw_text: Optional[str] = Field(None, description="原始 LLM 响应（调试用）")


class StockHistoryResponse(BaseModel):
    """股票历史行情响应"""

    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    period: str = Field(..., description="K 线周期")
    data: List[KLineData] = Field(default_factory=list, description="K 线数据列表")

    class Config:
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "period": "daily",
                "data": []
            }
        }


class StockNewsItem(BaseModel):
    """单条股票新闻"""

    title: str = Field(..., description="新闻标题")
    snippet: str = Field("", description="新闻摘要")
    url: str = Field(..., description="原文链接（http/https）")
    source: Optional[str] = Field(None, description="来源站点")
    published_at: Optional[str] = Field(None, description="发布时间（ISO 8601，来源提供则透传）")


class StockNewsResponse(BaseModel):
    """股票新闻响应"""

    stock_code: str = Field(..., description="股票代码")
    total: int = Field(..., description="新闻条数")
    items: List[StockNewsItem] = Field(default_factory=list, description="新闻列表")
    provider: Optional[str] = Field(None, description="使用的搜索 provider（未配置则为 null）")


class NewsSentimentItem(BaseModel):
    """单条新闻的 sentiment 分类"""

    url: str = Field(..., description="新闻 URL，用于前端和 /news 的 items 配对")
    score: int = Field(..., description="sentiment 打分：-2 强烈利空, -1 利空, 0 中性, 1 利好, 2 强烈利好", ge=-2, le=2)
    reason: Optional[str] = Field(None, description="该条定性的简要理由（中文，一句）")


class StockNewsDigestResponse(BaseModel):
    """股票新闻 LLM 精简摘要（中文）"""

    stock_code: str = Field(..., description="股票代码")
    news_count: int = Field(..., description="参与总结的新闻条数")
    overall_score: int = Field(..., description="整体 sentiment 打分：-2..2", ge=-2, le=2)
    overall_label: str = Field(..., description="整体分类标签（中文，如 '偏利好'/'强烈利空'）")
    summary: str = Field(..., description="一句话总评")
    bullets: List[str] = Field(default_factory=list, description="3-5 条关键触发点（中文 bullet）")
    items: List[NewsSentimentItem] = Field(default_factory=list, description="每条新闻的 sentiment")
    cached: bool = Field(False, description="是否命中 10 分钟 TTL 缓存")
    generated_at: Optional[str] = Field(None, description="生成时间（ISO 8601）")
