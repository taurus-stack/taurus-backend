"""taurus_ee.services.ops_notification_service — M2.6 Ops 通知分发 Service (EE 专属).

提供 4 条通道的统一通知接口：
  · Email    → Django send_mail (若 EMAIL_BACKEND 已配置)
  · Webhook  → POST JSON 到业务回调 URL
  · DingTalk → 钉钉群机器人 Markdown 消息
  · Lark     → 飞书 Webhook

所有通道都容忍失败（记录 warning 但不中断主执行流），因此调用者无需 try/except 包裹。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OpsNotificationService:
    """统一的运维通知分发器（纯静态类，无状态）."""

    CHANNELS = ("email", "webhook", "dingtalk", "lark")

    @staticmethod
    def send_email(
        recipients: List[str],
        subject: str,
        body: str,
        *,
        html_body: Optional[str] = None,
        from_email: Optional[str] = None,
    ) -> bool:
        """发送邮件通知。若无 Django email backend 配置则静默跳过，返回 False."""
        if not recipients:
            return False
        try:
            from django.conf import settings
            from django.core.mail import send_mail as _send

            sender = from_email or getattr(settings, "DEFAULT_FROM_EMAIL", None)
            if not sender:
                logger.warning("[OpsNotify] email 未配置 DEFAULT_FROM_EMAIL，跳过")
                return False
            _send(
                subject=subject,
                message=body,
                html_message=html_body,
                from_email=sender,
                recipient_list=list(recipients),
                fail_silently=True,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[OpsNotify] send_email 失败: %s", exc)
            return False

    @staticmethod
    def send_webhook(url: str, payload: Dict[str, Any], *, timeout: int = 5) -> bool:
        """POST JSON 到任意 Webhook URL。"""
        if not url:
            return False
        try:
            import urllib.request
            import urllib.error

            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url=url, data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as _resp:
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[OpsNotify] send_webhook %s 失败: %s", url, exc)
            return False

    @staticmethod
    def send_dingtalk(webhook: str, title: str, content: str) -> bool:
        """钉钉群机器人 Markdown 消息。"""
        if not webhook:
            return False
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": f"### {title}\n\n{content}"},
        }
        return OpsNotificationService.send_webhook(webhook, payload)

    @staticmethod
    def send_lark(webhook: str, title: str, content: str) -> bool:
        """飞书 Webhook 富文本消息（interactive card text 简化版）。"""
        if not webhook:
            return False
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": content}}],
            },
        }
        return OpsNotificationService.send_webhook(webhook, payload)

    @staticmethod
    def dispatch(
        *,
        channel: str,
        recipients: Optional[List[str]] = None,
        url: Optional[str] = None,
        title: str = "",
        body: str = "",
        payload: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """统一分发入口，根据 channel 路由到对应通道."""
        ch = (channel or "").lower().strip()
        if ch not in OpsNotificationService.CHANNELS:
            logger.warning("[OpsNotify] 未知 channel=%s", ch)
            return False
        if ch == "email":
            return OpsNotificationService.send_email(recipients or [], title, body)
        if ch == "webhook":
            return OpsNotificationService.send_webhook(url or "", payload or {"title": title, "body": body})
        if ch == "dingtalk":
            return OpsNotificationService.send_dingtalk(url or "", title, body)
        if ch == "lark":
            return OpsNotificationService.send_lark(url or "", title, body)
        return False
