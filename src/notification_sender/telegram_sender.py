# -*- coding: utf-8 -*-
"""
Telegram 发送提醒服务

职责：
1. 通过 Telegram Bot API 发送 文本消息
2. 通过 Telegram Bot API 发送 图片消息
"""
import json
import logging
import re
import socket
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests
import urllib3

from src.config import Config


logger = logging.getLogger(__name__)


_TELEGRAM_API_HOST = "api.telegram.org"


class _TelegramDeliveryIndeterminate(RuntimeError):
    """The request may have reached Telegram but no response was observed."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _seconds_before(deadline: Optional[datetime], cap: float) -> float:
    """Return a bounded timeout, or zero once an absolute deadline passed."""

    cutoff = _as_utc(deadline)
    if cutoff is None:
        return float(cap)
    return max(0.0, min(float(cap), (cutoff - _utc_now()).total_seconds()))


def _definite_pre_send_transport_failure(exc: BaseException) -> bool:
    """Whether retrying cannot duplicate a request accepted by Telegram.

    Connect timeouts and DNS/new-connection failures happen before an HTTP
    request can be accepted.  Read timeouts and opaque connection failures are
    deliberately *not* classified as definite: the response may simply have
    been lost after Telegram accepted the message.
    """

    definite_types = (
        requests.exceptions.ConnectTimeout,
        socket.gaierror,
        urllib3.exceptions.NameResolutionError,
        urllib3.exceptions.NewConnectionError,
        urllib3.exceptions.ConnectTimeoutError,
    )
    indeterminate_types = (
        requests.exceptions.ReadTimeout,
        requests.exceptions.ChunkedEncodingError,
        urllib3.exceptions.ReadTimeoutError,
        urllib3.exceptions.ProtocolError,
    )
    pending: list[object] = [exc]
    visited: set[int] = set()
    definite = False
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, indeterminate_types):
            return False
        if isinstance(current, definite_types):
            definite = True
        if isinstance(current, BaseException):
            if current.__cause__ is not None:
                pending.append(current.__cause__)
            # Implicit context may belong to an earlier HTTP attempt (e.g.
            # primary DNS failure followed by a cached-IP read timeout).
            # Only explicit wrappers describe the current request reliably.
            if isinstance(current, urllib3.exceptions.MaxRetryError):
                pending.append(current.reason)
            pending.extend(current.args)
        elif isinstance(current, (tuple, list)):
            pending.extend(current)
    return definite


class _DirectHTTPResponse:
    """Small requests-compatible view over an urllib3 response."""

    def __init__(self, response) -> None:
        self.status_code = int(response.status)
        self.headers = response.headers
        self.text = bytes(response.data or b"").decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class TelegramSender:
    
    def __init__(self, config: Config):
        """
        初始化 Telegram 配置

        Args:
            config: 配置对象
        """
        self._telegram_config = {
            'bot_token': getattr(config, 'telegram_bot_token', None),
            'chat_id': getattr(config, 'telegram_chat_id', None),
            'message_thread_id': getattr(config, 'telegram_message_thread_id', None),
        }
        # Keep one DNS-derived IPv4 address available for a narrow transport
        # fallback.  macOS can temporarily lose DNS while an already-running
        # intraday process still has working outbound connectivity.  The
        # fallback below connects to this address directly but continues to
        # use api.telegram.org for SNI, certificate hostname validation and
        # the HTTP Host header.
        self._telegram_ipv4: Optional[str] = None
        if self._is_telegram_configured():
            self._refresh_telegram_ipv4_cache()

    def _refresh_telegram_ipv4_cache(self) -> None:
        """Refresh the cached Telegram IPv4 without erasing a good value."""

        try:
            addresses = socket.getaddrinfo(
                _TELEGRAM_API_HOST,
                443,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        except OSError:
            return
        for address in addresses:
            try:
                ipv4 = str(address[4][0]).strip()
            except (IndexError, TypeError):
                continue
            if ipv4:
                self._telegram_ipv4 = ipv4
                return

    def _post_via_cached_ipv4(
        self,
        api_url: str,
        payload: dict,
        *,
        timeout: float,
    ) -> Optional[_DirectHTTPResponse]:
        """POST through the cached IP while preserving Telegram TLS identity.

        This is deliberately used only after ``requests`` raises a connection
        error or timeout.  HTTP/API errors stay on the ordinary path, avoiding
        a duplicate send for deterministic non-transport failures.
        """

        cached_ipv4 = self._telegram_ipv4
        if not cached_ipv4:
            return None
        parsed = urlsplit(api_url)
        if parsed.hostname != _TELEGRAM_API_HOST or parsed.scheme != "https":
            return None
        request_target = parsed.path or "/"
        if parsed.query:
            request_target = f"{request_target}?{parsed.query}"
        pool = urllib3.HTTPSConnectionPool(
            host=cached_ipv4,
            port=443,
            timeout=urllib3.Timeout(connect=timeout, read=timeout),
            retries=False,
            cert_reqs="CERT_REQUIRED",
            assert_hostname=_TELEGRAM_API_HOST,
            server_hostname=_TELEGRAM_API_HOST,
        )
        try:
            response = pool.urlopen(
                "POST",
                request_target,
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={
                    "Host": _TELEGRAM_API_HOST,
                    "Content-Type": "application/json",
                },
                retries=False,
                redirect=False,
            )
            return _DirectHTTPResponse(response)
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            # The request target contains the bot token.  Log only the type,
            # never the exception string or URL.
            logger.warning(
                "Telegram cached-IPv4 fallback failed: error_type=%s",
                type(exc).__name__,
            )
            if not _definite_pre_send_transport_failure(exc):
                # The direct POST may already have reached Telegram before a
                # read/protocol failure.  Propagate an indeterminate outcome
                # so neither the inner sender nor the process outbox retries
                # the same logical phone message.
                raise _TelegramDeliveryIndeterminate from None
            return None
        finally:
            pool.close()

    def _post_telegram(
        self,
        api_url: str,
        payload: dict,
        *,
        timeout: float,
        delivery_deadline: Optional[datetime] = None,
    ):
        """Use requests first, then the cached-IP path for transport errors."""

        try:
            response = requests.post(api_url, json=payload, timeout=timeout)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            if not _definite_pre_send_transport_failure(exc):
                # A read timeout or opaque connection loss can happen after
                # Telegram accepted the request.  Retrying would risk a second
                # phone ring because sendMessage has no idempotency key.
                raise _TelegramDeliveryIndeterminate from None
            fallback_timeout = timeout
            if self._telegram_ipv4:
                # The primary connect attempt may have consumed the remainder
                # of an opening checkpoint's exact minute.  Re-check the
                # absolute deadline before starting a *new* cached-IP POST.
                fallback_timeout = _seconds_before(
                    delivery_deadline, timeout
                )
                if fallback_timeout <= 0.0:
                    raise
            fallback = self._post_via_cached_ipv4(
                api_url,
                payload,
                timeout=fallback_timeout,
            )
            if fallback is not None:
                return fallback
            raise
        # A completed HTTPS exchange proves DNS is currently usable.  Refresh
        # the emergency address after the exchange rather than before it.
        self._refresh_telegram_ipv4_cache()
        return response
    
    def _is_telegram_configured(self) -> bool:
        """检查 Telegram 配置是否完整"""
        return bool(self._telegram_config['bot_token'] and self._telegram_config['chat_id'])
   
    def send_to_telegram(
        self,
        content: str,
        *,
        disable_notification: bool = False,
        delivery_deadline: Optional[datetime] = None,
    ) -> Optional[bool]:
        """
        推送消息到 Telegram 机器人

        Telegram Bot API 格式：
        POST https://api.telegram.org/bot<token>/sendMessage
        {
            "chat_id": "xxx",
            "text": "消息内容",
            "parse_mode": "Markdown"
        }

        Args:
            content: 消息内容（Markdown 格式）
            disable_notification: True 时静默投递（Telegram 官方
                ``disable_notification``：消息到达但不响铃/震动）。
                P1 预备级推送（蓝图 17 §2.2）使用；默认 False 保持既有行为。
            delivery_deadline: 绝对 UTC/aware 截止时间。到期后不会开始新的
                HTTP 尝试，供逐分钟 opening 提醒阻止跨分钟补发。

        Returns:
            是否发送成功
        """
        if not self._is_telegram_configured():
            logger.warning("Telegram 配置不完整，跳过推送")
            return False

        bot_token = self._telegram_config['bot_token']
        chat_id = self._telegram_config['chat_id']
        message_thread_id = self._telegram_config.get('message_thread_id')

        try:
            # Telegram API 端点
            api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

            # Telegram 消息最大长度 4096 字符
            max_length = 4096

            if len(content) <= max_length:
                # 单条消息发送
                return self._send_telegram_message(
                    api_url, chat_id, content, message_thread_id,
                    disable_notification=disable_notification,
                    delivery_deadline=delivery_deadline,
                )
            else:
                # 分段发送长消息
                return self._send_telegram_chunked(
                    api_url, chat_id, content, max_length, message_thread_id,
                    disable_notification=disable_notification,
                    delivery_deadline=delivery_deadline,
                )

        except Exception as e:
            # requests 异常会把完整 URL 带进字符串，而 Telegram
            # token 正在 URL path 中；日志只记异常类型，绝不记 e/URL。
            logger.error(
                "发送 Telegram 消息失败: error_type=%s",
                type(e).__name__,
            )
            return False

    def _send_telegram_message(
        self,
        api_url: str,
        chat_id: str,
        text: str,
        message_thread_id: Optional[str] = None,
        *,
        disable_notification: bool = False,
        delivery_deadline: Optional[datetime] = None,
    ) -> Optional[bool]:
        """Send a single Telegram message with exponential backoff retry (Fixes #287)"""
        # Convert Markdown to Telegram-compatible format
        telegram_text = self._convert_to_telegram_markdown(text)

        payload = {
            "chat_id": chat_id,
            "text": telegram_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }

        if disable_notification:
            # 静默投递：纯 additive；纯文本回退复制同一 payload，自动保留。
            payload['disable_notification'] = True

        if message_thread_id:
            payload['message_thread_id'] = message_thread_id

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            request_timeout = _seconds_before(delivery_deadline, 10.0)
            if request_timeout <= 0.0:
                logger.warning(
                    "Telegram delivery deadline expired before attempt %s",
                    attempt,
                )
                return False
            try:
                response = self._post_telegram(
                    api_url,
                    payload,
                    timeout=request_timeout,
                    delivery_deadline=delivery_deadline,
                )
            except _TelegramDeliveryIndeterminate:
                logger.warning(
                    "Telegram delivery outcome indeterminate; automatic "
                    "retry suppressed to avoid a duplicate phone ring"
                )
                return None
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                if attempt < max_retries:
                    delay = 2 ** attempt  # 2s, 4s
                    if _seconds_before(delivery_deadline, delay) < delay:
                        logger.warning(
                            "Telegram delivery deadline expires before retry"
                        )
                        return False
                    logger.warning(
                        "Telegram request failed (attempt %s/%s): "
                        "error_type=%s; retrying in %ss...",
                        attempt,
                        max_retries,
                        type(e).__name__,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        "Telegram request failed after %s attempts: error_type=%s",
                        max_retries,
                        type(e).__name__,
                    )
                    return False
        
            if response.status_code == 200:
                try:
                    result = response.json()
                except ValueError:
                    # A malformed success response does not prove Telegram
                    # rejected the already-sent POST.  Treat it as unknown so
                    # the durable outbox cannot duplicate the phone message.
                    logger.warning(
                        "Telegram HTTP 200 response was not valid JSON; "
                        "delivery outcome indeterminate"
                    )
                    return None
                if result.get('ok'):
                    logger.info("Telegram 消息发送成功")
                    return True
                else:
                    error_desc = result.get('description', '未知错误')
                    logger.error(f"Telegram 返回错误: {error_desc}")
                    
                    # If Markdown parsing failed, fall back to plain text
                    if self._should_fallback_to_plain_text(error_desc=error_desc):
                        fallback = self._send_plain_text_fallback(
                            api_url,
                            payload,
                            text,
                            delivery_deadline=delivery_deadline,
                        )
                        if fallback is None:
                            return None
                        if fallback:
                            return True
                    
                    return False
            elif response.status_code == 429:
                # Rate limited — respect Retry-After header
                retry_after = int(response.headers.get('Retry-After', 2 ** attempt))
                if attempt < max_retries:
                    if (
                        _seconds_before(delivery_deadline, retry_after)
                        < retry_after
                    ):
                        logger.warning(
                            "Telegram delivery deadline expires before "
                            "rate-limit retry"
                        )
                        return False
                    logger.warning(f"Telegram rate limited, retrying in {retry_after}s "
                                   f"(attempt {attempt}/{max_retries})...")
                    time.sleep(retry_after)
                    continue
                else:
                    logger.error(f"Telegram rate limited after {max_retries} attempts")
                    return False
            else:
                if attempt < max_retries and response.status_code >= 500:
                    delay = 2 ** attempt
                    if _seconds_before(delivery_deadline, delay) < delay:
                        logger.warning(
                            "Telegram delivery deadline expires before retry"
                        )
                        return False
                    logger.warning(f"Telegram server error HTTP {response.status_code} "
                                   f"(attempt {attempt}/{max_retries}), retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                if self._should_fallback_to_plain_text(response_text=response.text):
                    fallback = self._send_plain_text_fallback(
                        api_url,
                        payload,
                        text,
                        delivery_deadline=delivery_deadline,
                    )
                    if fallback is None:
                        return None
                    if fallback:
                        return True
                logger.error(f"Telegram 请求失败: HTTP {response.status_code}")
                logger.error(f"响应内容: {response.text}")
                return False

        return False

    @staticmethod
    def _should_fallback_to_plain_text(error_desc: str = "", response_text: str = "") -> bool:
        """Detect Telegram Markdown parsing failures that should retry as plain text."""
        haystack = f"{error_desc}\n{response_text}".lower()
        markers = (
            "can't parse entities",
            "can't parse entity",
            "can't find end of the entity",
            "parse entities",
            "parse_mode",
            "markdown",
        )
        return any(marker in haystack for marker in markers)

    def _send_plain_text_fallback(
        self,
        api_url: str,
        payload: dict,
        text: str,
        *,
        delivery_deadline: Optional[datetime] = None,
    ) -> Optional[bool]:
        """Retry Telegram send without parse_mode when Markdown parsing fails."""
        logger.info("Telegram Markdown 解析失败，尝试使用纯文本格式重新发送...")
        plain_payload = dict(payload)
        plain_payload.pop('parse_mode', None)
        plain_payload['text'] = text

        request_timeout = _seconds_before(delivery_deadline, 10.0)
        if request_timeout <= 0.0:
            logger.warning(
                "Telegram delivery deadline expired before plain-text fallback"
            )
            return False
        try:
            response = self._post_telegram(
                api_url,
                plain_payload,
                timeout=request_timeout,
                delivery_deadline=delivery_deadline,
            )
        except _TelegramDeliveryIndeterminate:
            logger.warning(
                "Telegram plain-text delivery outcome indeterminate; "
                "automatic retry suppressed"
            )
            return None
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            logger.error(
                "Telegram plain-text fallback failed: error_type=%s",
                type(e).__name__,
            )
            return False

        if response.status_code == 200:
            try:
                result = response.json()
            except ValueError:
                # The plain-text POST may already be visible on the phone.
                # Retrying the logical message through the durable outbox
                # would risk a duplicate ring, exactly as on the primary
                # Markdown path.
                logger.warning(
                    "Telegram plain-text HTTP 200 response was not valid "
                    "JSON; delivery outcome indeterminate"
                )
                return None

            if result.get('ok'):
                logger.info("Telegram 消息发送成功（纯文本）")
                return True

            logger.error("Telegram 纯文本回退失败: Telegram API 返回 ok=false")
            logger.error(f"响应内容: {response.text}")
            return False

        logger.error(f"Telegram 纯文本回退失败: HTTP {response.status_code}")
        logger.error(f"响应内容: {response.text}")
        return False
    
    def _send_telegram_chunked(
        self,
        api_url: str,
        chat_id: str,
        content: str,
        max_length: int,
        message_thread_id: Optional[str] = None,
        *,
        disable_notification: bool = False,
        delivery_deadline: Optional[datetime] = None,
    ) -> Optional[bool]:
        """分段发送长 Telegram 消息"""
        # 按段落分割
        sections = content.split("\n---\n")
        
        current_chunk = []
        current_length = 0
        all_success = True
        any_success = False
        chunk_index = 1
        
        for section in sections:
            section_length = len(section) + 5  # +5 for "\n---\n"
            
            if current_length + section_length > max_length:
                # 发送当前块
                if current_chunk:
                    chunk_content = "\n---\n".join(current_chunk)
                    logger.info(f"发送 Telegram 消息块 {chunk_index}...")
                    outcome = self._send_telegram_message(
                        api_url,
                        chat_id,
                        chunk_content,
                        message_thread_id,
                        disable_notification=disable_notification,
                        delivery_deadline=delivery_deadline,
                    )
                    if outcome is None:
                        return None
                    if outcome:
                        any_success = True
                    else:
                        all_success = False
                    chunk_index += 1
                
                # 重置
                current_chunk = [section]
                current_length = section_length
            else:
                current_chunk.append(section)
                current_length += section_length
        
        # 发送最后一块
        if current_chunk:
            chunk_content = "\n---\n".join(current_chunk)
            logger.info(f"发送 Telegram 消息块 {chunk_index}...")
            outcome = self._send_telegram_message(
                api_url,
                chat_id,
                chunk_content,
                message_thread_id,
                disable_notification=disable_notification,
                delivery_deadline=delivery_deadline,
            )
            if outcome is None:
                return None
            if outcome:
                any_success = True
            else:
                all_success = False

        if any_success and not all_success:
            # Some chunks may already be on the phone.  Retrying the full
            # logical message would duplicate them, so surface an unknown
            # aggregate outcome and stop application-level retry.
            return None
        return all_success

    def _send_telegram_photo(self, image_bytes: bytes) -> bool:
        """Send image via Telegram sendPhoto API (Issue #289)."""
        if not self._is_telegram_configured():
            return False
        bot_token = self._telegram_config['bot_token']
        chat_id = self._telegram_config['chat_id']
        message_thread_id = self._telegram_config.get('message_thread_id')
        api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        try:
            data = {"chat_id": chat_id}
            if message_thread_id:
                data['message_thread_id'] = message_thread_id
            files = {"photo": ("report.png", image_bytes, "image/png")}
            response = requests.post(api_url, data=data, files=files, timeout=30)
            if response.status_code == 200 and response.json().get('ok'):
                logger.info("Telegram 图片发送成功")
                return True
            logger.error("Telegram 图片发送失败: %s", response.text[:200])
            return False
        except Exception as e:
            logger.error(
                "Telegram 图片发送异常: error_type=%s",
                type(e).__name__,
            )
            return False

    def _convert_to_telegram_markdown(self, text: str) -> str:
        """
        将标准 Markdown 转换为 Telegram 支持的格式
        
        Telegram Markdown 限制：
        - 不支持 # 标题
        - 使用 *bold* 而非 **bold**
        - 使用 _italic_ 
        """
        result = text
        
        # 移除 # 标题标记（Telegram 不支持）
        result = re.sub(r'^#{1,6}\s+', '', result, flags=re.MULTILINE)
        
        # 转换 **bold** 为 *bold*
        result = re.sub(r'\*\*(.+?)\*\*', r'*\1*', result)
        
        # Escape special characters for Telegram Markdown, but preserve link syntax [text](url)
        # Step 1: temporarily protect markdown links
        import uuid as _uuid
        _link_placeholder = f"__LINK_{_uuid.uuid4().hex[:8]}__"
        _links = []
        def _save_link(m):
            _links.append(m.group(0))
            return f"{_link_placeholder}{len(_links) - 1}"
        result = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _save_link, result)

        # Step 2: escape remaining special chars
        for char in ['[', ']', '(', ')']:
            result = result.replace(char, f'\\{char}')

        # Step 3: restore links
        for i, link in enumerate(_links):
            result = result.replace(f"{_link_placeholder}{i}", link)

        return result
    
