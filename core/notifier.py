import asyncio
import smtplib
from email.mime.text import MIMEText

from core.config import ALERT_EMAIL_TO, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER

ALERT_SUBJECT = "🚨 Binance Futures Bot 警報"


def _send_email_sync(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = ALERT_EMAIL_TO
    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
    except Exception:
        pass


def notify_email(message: str) -> None:
    """Fire-and-forget 郵件警報；未設定 SMTP 帳密時直接跳過，絕不阻塞或中斷交易流程。"""
    if not (SMTP_USER and SMTP_PASSWORD and ALERT_EMAIL_TO):
        return
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _send_email_sync, ALERT_SUBJECT, message)
    except RuntimeError:
        _send_email_sync(ALERT_SUBJECT, message)
