# -*- coding: utf-8 -*-
"""
===================================
日志配置模块 - 统一的日志系统初始化
===================================

职责：
1. 提供统一的日志格式和配置常量
2. 支持控制台 + 文件（常规/调试）三层日志输出
3. 自动降低第三方库日志级别
"""

import logging
import os
import re
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional


LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(pathname)s:%(lineno)d | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class RelativePathFormatter(logging.Formatter):
    """自定义 Formatter，输出相对路径而非绝对路径"""

    def __init__(self, fmt=None, datefmt=None, relative_to=None):
        super().__init__(fmt, datefmt)
        self.relative_to = Path(relative_to) if relative_to else Path.cwd()

    def format(self, record):
        # 将绝对路径转为相对路径
        try:
            record.pathname = str(Path(record.pathname).relative_to(self.relative_to))
        except ValueError:
            # 如果无法转换为相对路径，保持原样
            pass
        return super().format(record)


# 默认需要降低日志级别的第三方库
DEFAULT_QUIET_LOGGERS = [
    'urllib3',
    'sqlalchemy',
    'google',
    'httpx',
    # LiteLLM DEBUG records include full prompts and can overwhelm both the
    # terminal and rotating debug log during an on-demand trade review.
    'LiteLLM',
    'LiteLLM Router',
    'LiteLLM Proxy',
]


def _env_non_negative_int(name: str, default: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "%s=%r 不是整数，按 %d（关闭）处理", name, raw, default
        )
        return default
    return max(value, 0)


def cleanup_old_logs(
    log_dir: str,
    log_prefix: str,
    *,
    retention_days: int = 0,
    max_total_bytes: int = 0,
) -> List[Path]:
    """按天数与总体积清理本模块自己生成的按日期日志文件。

    安全边界（fail closed，宁可少删不多删）：
    - 只匹配 ``{prefix}_{YYYYMMDD}.log`` / ``{prefix}_debug_{YYYYMMDD}.log``
      及其 RotatingFileHandler 备份 ``*.log.N``；其他文件一概不碰；
    - 只看 ``log_dir`` 顶层，不递归，因此 ``logs/archive/`` 等归档目录不受影响；
    - 年龄以文件名内嵌日期为准（而非 mtime），当天文件永不删除；
    - ``retention_days<=0`` 且 ``max_total_bytes<=0`` 时不做任何事；
    - 单个文件删除失败只告警，不中断调用方。

    Returns:
        实际删除的文件路径列表。
    """

    if retention_days <= 0 and max_total_bytes <= 0:
        return []

    log_path = Path(log_dir)
    if not log_path.is_dir():
        return []

    pattern = re.compile(
        rf"^{re.escape(log_prefix)}(?:_debug)?_(\d{{8}})\.log(?:\.\d+)?$"
    )
    today = datetime.now().strftime("%Y%m%d")

    dated_files: list[tuple[str, Path, int]] = []
    for entry in log_path.iterdir():
        if not entry.is_file():
            continue
        match = pattern.match(entry.name)
        if match is None:
            continue
        file_date = match.group(1)
        if file_date >= today:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        dated_files.append((file_date, entry, size))

    deleted: List[Path] = []
    logger = logging.getLogger(__name__)

    def _delete(entry: Path) -> bool:
        try:
            entry.unlink()
        except OSError as exc:
            logger.warning("日志 retention 删除 %s 失败：%s", entry, exc)
            return False
        deleted.append(entry)
        return True

    survivors: list[tuple[str, Path, int]] = []
    if retention_days > 0:
        cutoff = datetime.now().date()
        for file_date, entry, size in dated_files:
            try:
                parsed = datetime.strptime(file_date, "%Y%m%d").date()
            except ValueError:
                survivors.append((file_date, entry, size))
                continue
            if (cutoff - parsed).days > retention_days:
                if not _delete(entry):
                    survivors.append((file_date, entry, size))
            else:
                survivors.append((file_date, entry, size))
    else:
        survivors = dated_files

    if max_total_bytes > 0:
        survivors.sort(key=lambda item: item[0])  # 最旧日期在前
        total = sum(size for _, _, size in survivors)
        for _, entry, size in survivors:
            if total <= max_total_bytes:
                break
            if _delete(entry):
                total -= size

    if deleted:
        logger.info(
            "日志 retention 清理了 %d 个历史日志文件（天数上限 %d，体积上限 %d 字节）",
            len(deleted), retention_days, max_total_bytes,
        )
    return deleted


def setup_logging(
    log_prefix: str = "app",
    log_dir: str = "./logs",
    console_level: Optional[int] = None,
    debug: bool = False,
    extra_quiet_loggers: Optional[List[str]] = None,
) -> None:
    """
    统一的日志系统初始化

    配置三层日志输出：
    1. 控制台：根据 debug 参数或 console_level 设置级别
    2. 常规日志文件：INFO 级别，10MB 轮转，保留 5 个备份
    3. 调试日志文件：DEBUG 级别，50MB 轮转，保留 3 个备份

    Args:
        log_prefix: 日志文件名前缀（如 "api_server" -> api_server_20240101.log）
        log_dir: 日志文件目录，默认 ./logs
        console_level: 控制台日志级别（可选，优先于 debug 参数）
        debug: 是否启用调试模式（控制台输出 DEBUG 级别）
        extra_quiet_loggers: 额外需要降低日志级别的第三方库列表
    """
    # 确定控制台日志级别
    if console_level is not None:
        level = console_level
    else:
        level = logging.DEBUG if debug else logging.INFO

    # 创建日志目录
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 跨日期 retention：默认关闭（0），由 LOG_RETENTION_DAYS /
    # LOG_RETENTION_MAX_TOTAL_MB 显式开启；只清理本前缀的按日期文件。
    cleanup_old_logs(
        log_dir,
        log_prefix,
        retention_days=_env_non_negative_int("LOG_RETENTION_DAYS"),
        max_total_bytes=_env_non_negative_int("LOG_RETENTION_MAX_TOTAL_MB") * 1024 * 1024,
    )

    # 日志文件路径（按日期分文件）
    today_str = datetime.now().strftime('%Y%m%d')
    log_file = log_path / f"{log_prefix}_{today_str}.log"
    debug_log_file = log_path / f"{log_prefix}_debug_{today_str}.log"

    # 配置根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 设为 DEBUG，由 handler 控制输出级别

    # 清除已有 handler，避免重复添加
    if root_logger.handlers:
        root_logger.handlers.clear()
    # 创建相对路径 Formatter（相对于项目根目录）
    project_root = Path.cwd()
    rel_formatter = RelativePathFormatter(
        LOG_FORMAT, LOG_DATE_FORMAT, relative_to=project_root
    )
    # Handler 1: 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(rel_formatter)
    root_logger.addHandler(console_handler)

    # Handler 2: 常规日志文件（INFO 级别，10MB 轮转）
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(rel_formatter)
    root_logger.addHandler(file_handler)

    # Handler 3: 调试日志文件（DEBUG 级别，包含所有详细信息）
    debug_handler = RotatingFileHandler(
        debug_log_file,
        maxBytes=50 * 1024 * 1024,  # 50MB
        backupCount=3,
        encoding='utf-8'
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(rel_formatter)
    root_logger.addHandler(debug_handler)

    # 降低第三方库的日志级别
    quiet_loggers = DEFAULT_QUIET_LOGGERS.copy()
    if extra_quiet_loggers:
        quiet_loggers.extend(extra_quiet_loggers)

    for logger_name in quiet_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    # yfinance logs each empty/fallback response as its own ERROR before the
    # application can classify it. DataFetcherManager already emits one
    # bounded final summary, so suppress the duplicate third-party stack.
    logging.getLogger('yfinance').setLevel(logging.CRITICAL)

    # 输出初始化完成信息（使用相对路径）
    try:
        rel_log_path = log_path.resolve().relative_to(project_root)
    except ValueError:
        rel_log_path = log_path

    try:
        rel_log_file = log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_log_file = log_file

    try:
        rel_debug_log_file = debug_log_file.resolve().relative_to(project_root)
    except ValueError:
        rel_debug_log_file = debug_log_file

    logging.info(f"日志系统初始化完成，日志目录: {rel_log_path}")
    logging.info(f"常规日志: {rel_log_file}")
    logging.info(f"调试日志: {rel_debug_log_file}")
