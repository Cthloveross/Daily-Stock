# -*- coding: utf-8 -*-
"""
Unit tests for src.notification_sender module.

Tests sender classes in isolation (config, request shape, error handling).
Does not duplicate test_notification.py which tests NotificationService.send() flow.
"""
import base64
import hashlib
import hmac
import os
import socket
import sys
import unittest
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Optional
from unittest import mock

import requests
import urllib3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import Config
from src.notification_sender import (
    AstrbotSender,
    CustomWebhookSender,
    DiscordSender,
    EmailSender,
    FeishuSender,
    PushoverSender,
    PushplusSender,
    Serverchan3Sender,
    SlackSender,
    TelegramSender,
    WechatSender,
    WECHAT_IMAGE_MAX_BYTES,
)
from src.notification_sender.telegram_sender import (
    _definite_pre_send_transport_failure,
)


def _config(**overrides):
    """Minimal Config for sender tests."""
    return Config(stock_list=[], **overrides)


def _response(status_code: int, json_body: Optional[dict] = None):
    resp = mock.MagicMock()
    resp.status_code = status_code
    if status_code == 200:
        resp.text = "ok"
    else:
        resp.text = "error"
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


class TestDiscordSender(unittest.TestCase):
    """Unit tests for DiscordSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("hello")
        self.assertFalse(result)

    def test_is_discord_configured_webhook_only(self):
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        self.assertTrue(sender._is_discord_configured())

    def test_is_discord_configured_bot_only(self):
        cfg = _config(discord_bot_token="T", discord_main_channel_id="123")
        sender = DiscordSender(cfg)
        self.assertTrue(sender._is_discord_configured())

    def test_is_discord_configured_neither(self):
        cfg = _config()
        sender = DiscordSender(cfg)
        self.assertFalse(sender._is_discord_configured())

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_success_builds_correct_payload(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertTrue(result)
        mock_post.assert_called_once()
        call_kw = mock_post.call_args[1]
        self.assertEqual(call_kw["json"]["content"], "content")
        self.assertIn("username", call_kw["json"])

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_webhook_http_error_returns_false(self, mock_post):
        mock_post.return_value = _response(400)
        cfg = _config(discord_webhook_url="https://discord.com/webhook/1")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.discord_sender.requests.post")
    def test_send_bot_success_uses_channel_url(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(discord_bot_token="TOKEN", discord_main_channel_id="CH123")
        sender = DiscordSender(cfg)
        result = sender.send_to_discord("content")
        self.assertTrue(result)
        self.assertIn("discord.com/api/v10/channels/CH123/messages", mock_post.call_args[0][0])
        call_kw = mock_post.call_args[1]
        self.assertEqual(call_kw["headers"]["Authorization"], "Bot TOKEN")


class TestWechatSender(unittest.TestCase):
    """Unit tests for WechatSender."""

    def test_send_returns_false_when_no_webhook_url(self):
        cfg = _config()
        sender = WechatSender(cfg)
        result = sender.send_to_wechat("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.wechat_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"errcode": 0})
        cfg = _config(wechat_webhook_url="https://wechat.example/hook")
        sender = WechatSender(cfg)
        result = sender.send_to_wechat("hello")
        self.assertTrue(result)

    def test_gen_wechat_payload_markdown(self):
        cfg = _config(wechat_webhook_url="u", wechat_msg_type="markdown")
        sender = WechatSender(cfg)
        payload = sender._gen_wechat_payload("## title\nbody")
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertEqual(payload["markdown"]["content"], "## title\nbody")

    def test_gen_wechat_payload_text(self):
        cfg = _config(wechat_webhook_url="u", wechat_msg_type="text")
        sender = WechatSender(cfg)
        payload = sender._gen_wechat_payload("plain")
        self.assertEqual(payload["msgtype"], "text")
        self.assertEqual(payload["text"]["content"], "plain")

    @mock.patch("src.notification_sender.wechat_sender.requests.post")
    def test_send_wechat_image_over_limit_returns_false(self, mock_post):
        cfg = _config(wechat_webhook_url="https://wechat.example/hook")
        sender = WechatSender(cfg)
        big = b"x" * (WECHAT_IMAGE_MAX_BYTES + 1)
        result = sender._send_wechat_image(big)
        self.assertFalse(result)
        mock_post.assert_not_called()


class TestFeishuSender(unittest.TestCase):
    """Unit tests for FeishuSender."""

    def test_send_returns_false_when_no_webhook_url(self):
        cfg = _config()
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertTrue(result)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_http_error_returns_false(self, mock_post):
        mock_post.return_value = _response(400)
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)
        result = sender.send_to_feishu("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.feishu_sender.time.time", return_value=1700000000)
    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_with_secret_and_keyword_builds_signed_payload(self, mock_post, _mock_time):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_webhook_secret="secret-token",
            feishu_webhook_keyword="股票日报",
        )
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("hello")

        self.assertTrue(result)
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["timestamp"], "1700000000")
        expected_sign = base64.b64encode(
            hmac.new(
                b"1700000000\nsecret-token",
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        self.assertEqual(payload["sign"], expected_sign)
        self.assertEqual(
            payload["card"]["elements"][0]["text"]["content"],
            "股票日报\nhello",
        )

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_error_response_returns_false(self, mock_post):
        mock_post.return_value = _response(200, {"code": 19024, "msg": "keyword not found"})
        cfg = _config(feishu_webhook_url="https://feishu.example/hook")
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("hello")

        self.assertFalse(result)
        self.assertEqual(mock_post.call_count, 2)

    @mock.patch("src.notification_sender.feishu_sender.requests.post")
    def test_send_with_keyword_that_leaves_too_little_chunk_budget_returns_false(self, mock_post):
        cfg = _config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_webhook_keyword="abcd",
            feishu_max_bytes=60,
        )
        sender = FeishuSender(cfg)

        result = sender.send_to_feishu("x" * 100)

        self.assertFalse(result)
        mock_post.assert_not_called()


class TestEmailSender(unittest.TestCase):
    """Unit tests for EmailSender (config and receiver logic; send path covered via service)."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = EmailSender(cfg)
        result = sender.send_to_email("body")
        self.assertFalse(result)

    def test_get_receivers_for_stocks_no_groups_returns_default(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com", "c@qq.com"],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["000001"]),
            ["b@qq.com", "c@qq.com"],
        )

    def test_get_receivers_for_stocks_with_matching_group(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[(["000001", "600519"], ["group1@qq.com"])],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["000001"]),
            ["group1@qq.com"],
        )

    def test_get_receivers_for_stocks_no_match_falls_back_to_default(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[(["000001"], ["group@qq.com"])],
        )
        sender = EmailSender(cfg)
        self.assertEqual(
            sender.get_receivers_for_stocks(["999999"]),
            ["default@qq.com"],
        )

    def test_get_all_email_receivers_returns_union(self):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["default@qq.com"],
            stock_email_groups=[
                (["000001"], ["g1@qq.com"]),
                (["600519"], ["g2@qq.com"]),
            ],
        )
        sender = EmailSender(cfg)
        receivers = sender.get_all_email_receivers()
        self.assertIn("g1@qq.com", receivers)
        self.assertIn("g2@qq.com", receivers)
        self.assertIn("default@qq.com", receivers)

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = sender.send_to_email("body", subject="测试主题")

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()

    @mock.patch("smtplib.SMTP_SSL")
    def test_send_image_email_encodes_non_ascii_sender_name(self, mock_smtp_ssl):
        cfg = _config(
            email_sender="a@qq.com",
            email_password="p",
            email_receivers=["b@qq.com"],
            email_sender_name="daily_stock_analysis股票分析助手",
        )
        sender = EmailSender(cfg)

        result = sender._send_email_with_inline_image(b"PNG_BYTES", receivers=["b@qq.com"])

        self.assertTrue(result)
        server = mock_smtp_ssl.return_value
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        realname, addr = parseaddr(msg["From"])
        self.assertEqual(addr, "a@qq.com")
        self.assertEqual(
            str(make_header(decode_header(realname))),
            "daily_stock_analysis股票分析助手",
        )
        server.quit.assert_called_once()


class TestAstrbotSender(unittest.TestCase):
    """Unit tests for AstrbotSender."""

    def test_send_returns_false_when_no_url(self):
        cfg = _config()
        sender = AstrbotSender(cfg)
        result = sender.send_to_astrbot("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.astrbot_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(astrbot_url="https://astrbot.example/api")
        sender = AstrbotSender(cfg)
        result = sender.send_to_astrbot("hello")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_args[0][0], "https://astrbot.example/api")


class TestCustomWebhookSender(unittest.TestCase):
    """Unit tests for CustomWebhookSender."""

    def test_send_returns_false_when_no_urls(self):
        cfg = _config()
        sender = CustomWebhookSender(cfg)
        result = sender.send_to_custom("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.custom_webhook_sender.requests.post")
    def test_send_success_payload_has_text_and_content(self, mock_post):
        mock_post.return_value = _response(200)
        cfg = _config(custom_webhook_urls=["https://example.com/webhook"])
        sender = CustomWebhookSender(cfg)
        result = sender.send_to_custom("hello")
        self.assertTrue(result)
        body = mock_post.call_args[1]["data"].decode("utf-8")
        self.assertIn("hello", body)


class TestPushoverSender(unittest.TestCase):
    """Unit tests for PushoverSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = PushoverSender(cfg)
        result = sender.send_to_pushover("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.pushover_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"status": 1})
        cfg = _config(pushover_user_key="U", pushover_api_token="T")
        sender = PushoverSender(cfg)
        result = sender.send_to_pushover("hello")
        self.assertTrue(result)
        call_data = mock_post.call_args[1]["data"]
        self.assertEqual(call_data["user"], "U")
        self.assertEqual(call_data["token"], "T")


class TestPushplusSender(unittest.TestCase):
    """Unit tests for PushplusSender."""

    def test_send_returns_false_when_no_token(self):
        cfg = _config()
        sender = PushplusSender(cfg)
        result = sender.send_to_pushplus("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.pushplus_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"code": 200})
        cfg = _config(pushplus_token="TOKEN")
        sender = PushplusSender(cfg)
        result = sender.send_to_pushplus("hello")
        self.assertTrue(result)

    @mock.patch("src.notification_sender.pushplus_sender.time.sleep")
    @mock.patch("src.notification_sender.pushplus_sender.requests.post")
    def test_send_long_message_chunks_pushplus_requests(self, mock_post, _mock_sleep):
        mock_post.return_value = _response(200, {"code": 200})
        cfg = _config(pushplus_token="TOKEN")
        sender = PushplusSender(cfg)

        result = sender.send_to_pushplus("A" * 25000)

        self.assertTrue(result)
        self.assertGreaterEqual(mock_post.call_count, 2)


class TestServerchan3Sender(unittest.TestCase):
    """Unit tests for Serverchan3Sender."""

    def test_send_returns_false_when_no_sendkey(self):
        cfg = _config()
        sender = Serverchan3Sender(cfg)
        result = sender.send_to_serverchan3("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.serverchan3_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"code": 0})
        cfg = _config(serverchan3_sendkey="SCT123")
        sender = Serverchan3Sender(cfg)
        result = sender.send_to_serverchan3("hello")
        self.assertTrue(result)


class TestSlackSender(unittest.TestCase):
    """Unit tests for SlackSender."""

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    def test_is_slack_configured_webhook_only(self):
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        self.assertTrue(sender._is_slack_configured())

    def test_is_slack_configured_bot_only(self):
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        self.assertTrue(sender._is_slack_configured())

    def test_is_slack_configured_neither(self):
        cfg = _config()
        sender = SlackSender(cfg)
        self.assertFalse(sender._is_slack_configured())

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_webhook_success(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        mock_post.assert_called_once()

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_webhook_http_error_returns_false(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 400
        resp.text = "invalid_payload"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_bot_success(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        self.assertIn("chat.postMessage", mock_post.call_args[0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_bot_error_returns_false(self, mock_post):
        mock_post.return_value = _response(200, {"ok": False, "error": "channel_not_found"})
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertFalse(result)

    def test_build_blocks_splits_long_content(self):
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        content = "A" * 6500  # > 3000 * 2, should produce 3 blocks
        blocks = sender._build_blocks(content)
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]["type"], "section")
        self.assertEqual(blocks[0]["text"]["type"], "mrkdwn")

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_text_prefers_bot_when_both_configured(self, mock_post):
        """When both webhook and bot are configured, text must go via bot
        so it lands in the same channel as images."""
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(
            slack_webhook_url="https://hooks.slack.com/services/T/B/xxx",
            slack_bot_token="xoxb-test",
            slack_channel_id="C123",
        )
        sender = SlackSender(cfg)
        result = sender.send_to_slack("hello")
        self.assertTrue(result)
        self.assertIn("chat.postMessage", mock_post.call_args[0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_image_bot_success(self, mock_post):
        # Mock three sequential calls: getUploadURLExternal, PUT upload, completeUploadExternal
        mock_post.side_effect = [
            _response(200, {"ok": True, "upload_url": "https://files.slack.com/upload/v1/test", "file_id": "F123"}),
            _response(200, {}),
            _response(200, {"ok": True}),
        ]
        cfg = _config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        sender = SlackSender(cfg)
        result = sender._send_slack_image(b"PNG_BYTES")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 3)
        self.assertIn("getUploadURLExternal", mock_post.call_args_list[0][0][0])
        # Step 2: upload must send raw bytes (not multipart) to match declared length
        upload_call_kwargs = mock_post.call_args_list[1][1]
        self.assertEqual(upload_call_kwargs.get("data"), b"PNG_BYTES")
        self.assertNotIn("files", upload_call_kwargs)
        self.assertIn("completeUploadExternal", mock_post.call_args_list[2][0][0])

    @mock.patch("src.notification_sender.slack_sender.requests.post")
    def test_send_image_fallback_to_text_when_no_bot(self, mock_post):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp
        cfg = _config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        sender = SlackSender(cfg)
        result = sender._send_slack_image(b"PNG_BYTES", fallback_content="fallback text")
        self.assertTrue(result)


class TestTelegramSender(unittest.TestCase):
    """Unit tests for TelegramSender."""

    def setUp(self):
        self._dns_patch = mock.patch(
            "src.notification_sender.telegram_sender.socket.getaddrinfo",
            return_value=[
                (
                    2,
                    1,
                    6,
                    "",
                    ("203.0.113.10", 443),
                )
            ],
        )
        self.mock_getaddrinfo = self._dns_patch.start()
        self.addCleanup(self._dns_patch.stop)

    def test_send_returns_false_when_not_configured(self):
        cfg = _config()
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("hello")
        self.assertFalse(result)
        self.mock_getaddrinfo.assert_not_called()

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_success_returns_true(self, mock_post):
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("hello")
        self.assertTrue(result)
        self.assertIn("sendMessage", mock_post.call_args[0][0])
        # One startup lookup and one refresh after a successful HTTPS exchange.
        self.assertEqual(self.mock_getaddrinfo.call_count, 2)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_http_200_invalid_json_is_indeterminate_not_retried(self, mock_post):
        response = _response(200)
        response.json.side_effect = ValueError("malformed body")
        mock_post.return_value = response
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertIsNone(sender.send_to_telegram("one logical message"))

        mock_post.assert_called_once()

    def test_partial_chunk_success_is_indeterminate_not_whole_message_retry(self):
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )
        content = ("A" * 3000) + "\n---\n" + ("B" * 3000)
        with mock.patch.object(
            sender,
            "_send_telegram_message",
            side_effect=[True, False],
        ) as send_chunk:
            outcome = sender.send_to_telegram(content)

        self.assertIsNone(outcome)
        self.assertEqual(send_chunk.call_count, 2)

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_connection_error_uses_cached_ipv4_with_telegram_tls_identity(
        self,
        mock_post,
        mock_pool_cls,
    ):
        mock_post.side_effect = requests.exceptions.ConnectTimeout("dns down")
        direct = mock.MagicMock()
        direct.status = 200
        direct.data = b'{"ok":true}'
        direct.headers = {}
        mock_pool_cls.return_value.urlopen.return_value = direct
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertTrue(sender.send_to_telegram("real signal"))

        mock_post.assert_called_once()
        pool_kwargs = mock_pool_cls.call_args.kwargs
        self.assertEqual(pool_kwargs["host"], "203.0.113.10")
        self.assertEqual(pool_kwargs["assert_hostname"], "api.telegram.org")
        self.assertEqual(pool_kwargs["server_hostname"], "api.telegram.org")
        direct_call = mock_pool_cls.return_value.urlopen.call_args
        self.assertEqual(direct_call.args[0], "POST")
        self.assertEqual(
            direct_call.kwargs["headers"]["Host"],
            "api.telegram.org",
        )
        self.assertIn("/botBOT/sendMessage", direct_call.args[1])
        mock_pool_cls.return_value.close.assert_called_once()

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_http_error_does_not_use_cached_ipv4_or_duplicate_request(
        self,
        mock_post,
        mock_pool_cls,
    ):
        mock_post.return_value = _response(400, {"ok": False})
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertFalse(sender.send_to_telegram("bad request"))

        mock_post.assert_called_once()
        mock_pool_cls.assert_not_called()

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_missing_cached_ipv4_preserves_existing_retry_path(
        self,
        mock_post,
        _mock_sleep,
        mock_pool_cls,
    ):
        mock_post.side_effect = requests.exceptions.ConnectTimeout("timeout")
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )
        sender._telegram_ipv4 = None

        self.assertFalse(sender.send_to_telegram("hello"))

        self.assertEqual(mock_post.call_count, 3)
        mock_pool_cls.assert_not_called()

    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_transport_error_log_never_exposes_bot_token(
        self,
        mock_post,
        _mock_sleep,
    ):
        secret = "123456:VERY_SECRET_BOT_TOKEN"
        mock_post.side_effect = requests.exceptions.ConnectTimeout(
            "failed URL https://api.telegram.org/"
            f"bot{secret}/sendMessage"
        )
        sender = TelegramSender(
            _config(telegram_bot_token=secret, telegram_chat_id="CHAT")
        )
        # This test isolates the ordinary retry/logging path.  Cached-IP
        # behavior is covered separately without making a real connection.
        sender._telegram_ipv4 = None

        with self.assertLogs(
            "src.notification_sender.telegram_sender",
            level="WARNING",
        ) as captured:
            self.assertFalse(sender.send_to_telegram("hello"))

        output = "\n".join(captured.output)
        self.assertIn("error_type=ConnectTimeout", output)
        self.assertNotIn(secret, output)
        self.assertNotIn("/bot", output)

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_read_timeout_is_indeterminate_and_never_retried(
        self,
        mock_post,
        mock_pool_cls,
    ):
        mock_post.side_effect = requests.exceptions.ReadTimeout(
            "response lost"
        )
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertIsNone(sender.send_to_telegram("one logical message"))

        mock_post.assert_called_once()
        mock_pool_cls.assert_not_called()

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_cached_ipv4_read_timeout_is_indeterminate_and_never_retried(
        self,
        mock_post,
        mock_pool_cls,
    ):
        mock_post.side_effect = requests.exceptions.ConnectTimeout(
            "primary connect timeout"
        )
        mock_pool_cls.return_value.urlopen.side_effect = (
            urllib3.exceptions.ReadTimeoutError(
                mock_pool_cls.return_value,
                "/botBOT/sendMessage",
                "response lost",
            )
        )
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertIsNone(sender.send_to_telegram("one logical message"))

        mock_post.assert_called_once()
        mock_pool_cls.return_value.urlopen.assert_called_once()

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_dns_failure_then_cached_ip_read_timeout_does_not_resend(
        self,
        mock_post,
        mock_sleep,
        mock_pool_cls,
    ):
        dns_error = urllib3.exceptions.NameResolutionError(
            "api.telegram.org", None, socket.gaierror(-2, "DNS lookup failed")
        )
        primary_error = requests.exceptions.ConnectionError(dns_error)
        fallback_error = urllib3.exceptions.ReadTimeoutError(
            mock_pool_cls.return_value,
            "/botBOT/sendMessage",
            "response lost",
        )
        mock_post.side_effect = primary_error
        mock_pool_cls.return_value.urlopen.side_effect = fallback_error
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertIsNone(sender.send_to_telegram("one logical message"))

        self.assertIs(fallback_error.__context__, primary_error)
        mock_post.assert_called_once()
        mock_pool_cls.return_value.urlopen.assert_called_once()
        mock_pool_cls.return_value.close.assert_called_once()
        mock_sleep.assert_not_called()

    def test_pre_send_classifier_keeps_explicit_connection_wrappers(self):
        reasons = (
            socket.gaierror(-2, "DNS lookup failed"),
            urllib3.exceptions.NameResolutionError(
                "api.telegram.org", None, socket.gaierror(-2, "DNS failed")
            ),
            urllib3.exceptions.NewConnectionError(None, "connection refused"),
            urllib3.exceptions.ConnectTimeoutError("connect timed out"),
        )
        for reason in reasons:
            with self.subTest(reason=type(reason).__name__):
                self.assertTrue(_definite_pre_send_transport_failure(reason))
                wrapped = requests.exceptions.ConnectionError(
                    urllib3.exceptions.MaxRetryError(None, "/redacted", reason)
                )
                self.assertTrue(_definite_pre_send_transport_failure(wrapped))
                explicit_cause = requests.exceptions.ConnectionError("failed")
                explicit_cause.__cause__ = reason
                self.assertTrue(
                    _definite_pre_send_transport_failure(explicit_cause)
                )

    def test_pre_send_classifier_ignores_previous_request_context(self):
        previous_dns = socket.gaierror(-2, "previous DNS lookup failed")
        uncertain = (
            requests.exceptions.ReadTimeout("response lost"),
            urllib3.exceptions.ReadTimeoutError(None, "/redacted", "lost"),
            urllib3.exceptions.ProtocolError("connection closed after POST"),
            requests.exceptions.ConnectionError("opaque connection loss"),
        )
        for error in uncertain:
            with self.subTest(error=type(error).__name__):
                error.__context__ = previous_dns
                self.assertFalse(_definite_pre_send_transport_failure(error))

    def test_pre_send_classifier_read_or_protocol_failure_overrides_dns(self):
        previous_dns = socket.gaierror(-2, "DNS lookup failed")
        for uncertain in (
            requests.exceptions.ReadTimeout("response lost"),
            urllib3.exceptions.ProtocolError("connection closed after POST"),
        ):
            with self.subTest(error=type(uncertain).__name__):
                # Even an explicit/nested DNS error cannot establish that a
                # read/protocol failure happened before this POST was sent.
                uncertain.__cause__ = previous_dns
                wrapped = requests.exceptions.ConnectionError(uncertain)
                self.assertFalse(_definite_pre_send_transport_failure(wrapped))

    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    @mock.patch("src.notification_sender.telegram_sender._utc_now")
    def test_delivery_deadline_stops_internal_retry_after_opening_minute(
        self,
        mock_now,
        mock_post,
        mock_sleep,
    ):
        before_expiry = datetime(2026, 8, 31, 13, 32, 40, tzinfo=timezone.utc)
        expiry = datetime(2026, 8, 31, 13, 33, 0, tzinfo=timezone.utc)
        mock_now.side_effect = [before_expiry, expiry]
        mock_post.side_effect = [
            requests.exceptions.ConnectTimeout("connect timeout"),
            _response(200, {"ok": True}),
        ]
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )
        sender._telegram_ipv4 = None

        self.assertFalse(
            sender.send_to_telegram(
                "09:32 opening",
                delivery_deadline=expiry,
            )
        )

        mock_post.assert_called_once()
        mock_sleep.assert_not_called()

    @mock.patch(
        "src.notification_sender.telegram_sender.urllib3.HTTPSConnectionPool"
    )
    @mock.patch("src.notification_sender.telegram_sender.time.sleep")
    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    @mock.patch("src.notification_sender.telegram_sender._utc_now")
    def test_delivery_deadline_blocks_cached_ip_post_after_primary_timeout(
        self,
        mock_now,
        mock_post,
        mock_sleep,
        mock_pool_cls,
    ):
        before_expiry = datetime(2026, 8, 31, 13, 32, 59, tzinfo=timezone.utc)
        expiry = datetime(2026, 8, 31, 13, 33, 0, tzinfo=timezone.utc)
        mock_now.side_effect = [before_expiry, expiry, expiry]
        mock_post.side_effect = requests.exceptions.ConnectTimeout(
            "primary consumed the remaining minute"
        )
        sender = TelegramSender(
            _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        )

        self.assertFalse(
            sender.send_to_telegram(
                "09:32 opening",
                delivery_deadline=expiry,
            )
        )

        mock_post.assert_called_once()
        mock_pool_cls.assert_not_called()
        mock_sleep.assert_not_called()

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_retries_plain_text_when_markdown_http_400(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_success = _response(200, {"ok": True})
        mock_post.side_effect = [markdown_error, plain_text_success]

        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("*ST宝实")

        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)
        first_payload = mock_post.call_args_list[0][1]["json"]
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertEqual(first_payload["parse_mode"], "Markdown")
        self.assertNotIn("parse_mode", second_payload)
        self.assertEqual(second_payload["text"], "*ST宝实")

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_send_plain_text_fallback_handles_non_json_200(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_non_json = _response(200)
        plain_text_non_json.text = "upstream proxy error"
        plain_text_non_json.json.side_effect = ValueError("invalid json")
        mock_post.side_effect = [markdown_error, plain_text_non_json]

        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("*ST宝实")

        self.assertIsNone(result)
        self.assertEqual(mock_post.call_count, 2)


    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_disable_notification_flag_is_additive(self, mock_post):
        """P1 静默投递（蓝图 17 §2.2）：flag 传入才出现，默认零改动。"""
        mock_post.return_value = _response(200, {"ok": True})
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        self.assertTrue(
            sender.send_to_telegram("silent", disable_notification=True)
        )
        payload = mock_post.call_args[1]["json"]
        self.assertIs(payload.get("disable_notification"), True)
        # 默认路径与旧行为逐字节一致：payload 里没有这个键。
        self.assertTrue(sender.send_to_telegram("loud"))
        payload = mock_post.call_args[1]["json"]
        self.assertNotIn("disable_notification", payload)

    @mock.patch("src.notification_sender.telegram_sender.requests.post")
    def test_disable_notification_survives_plain_text_fallback(self, mock_post):
        markdown_error = _response(400)
        markdown_error.text = (
            '{"ok":false,"error_code":400,'
            '"description":"Bad Request: can\'t parse entities"}'
        )
        plain_text_success = _response(200, {"ok": True})
        mock_post.side_effect = [markdown_error, plain_text_success]
        cfg = _config(telegram_bot_token="BOT", telegram_chat_id="CHAT")
        sender = TelegramSender(cfg)
        result = sender.send_to_telegram("*ST宝实", disable_notification=True)
        self.assertTrue(result)
        second_payload = mock_post.call_args_list[1][1]["json"]
        self.assertIs(second_payload.get("disable_notification"), True)


if __name__ == "__main__":
    unittest.main()
