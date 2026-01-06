"""Utility helpers for sending admin notifications via email, SMS, or WhatsApp.

The helpers intentionally avoid introducing third-party SDKs so that the
application can talk to providers such as Twilio or SendGrid using plain HTTP
requests.  Credentials are pulled from environment variables so that Railway
can inject them securely at deploy time.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

import requests


@dataclass
class ChannelResult:
    """Outcome for a single notification channel."""

    channel: str
    ok: bool
    detail: Optional[str] = None


class NotificationService:
    """Best-effort helper that can fan a message out to multiple channels."""

    DEFAULT_TIMEOUT = 10  # seconds

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)

        # Admin contact preferences
        self.admin_email = os.environ.get("ADMIN_EMAIL")
        self.admin_sms_number = os.environ.get("ADMIN_SMS_NUMBER")
        self.admin_whatsapp_number = os.environ.get("ADMIN_WHATSAPP")

        # SendGrid (email)
        self.sendgrid_api_key = os.environ.get("SENDGRID_API_KEY")
        self.sendgrid_from_email = os.environ.get("SENDGRID_FROM_EMAIL")

        # Twilio (SMS / WhatsApp)
        self.twilio_account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
        self.twilio_auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
        self.twilio_sms_from = os.environ.get("TWILIO_SMS_FROM")
        self.twilio_whatsapp_from = os.environ.get("TWILIO_WHATSAPP_FROM")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def send_admin_alert(self, subject: str, body: str) -> List[ChannelResult]:
        """Send an alert message to every configured channel.

        Returns a list with the outcome for each attempted channel.  The method
        never raises; it logs and records failures so the caller can inspect the
        returned results if desired.
        """

        full_message = self._build_body(subject, body)
        results: List[ChannelResult] = []

        email_result = self._send_email(subject, full_message)
        if email_result:
            results.append(email_result)

        sms_result = self._send_twilio_sms(full_message)
        if sms_result:
            results.append(sms_result)

        whatsapp_result = self._send_twilio_whatsapp(full_message)
        if whatsapp_result:
            results.append(whatsapp_result)

        if not results:
            self.logger.info(
                "Admin alert skipped; no notification channels are configured."
            )

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_body(subject: str, body: str) -> str:
        subject = subject.strip() if subject else ""
        body = body.strip() if body else ""
        return f"{subject}\n\n{body}".strip()

    def _send_email(self, subject: str, body: str) -> Optional[ChannelResult]:
        if not (self.sendgrid_api_key and self.sendgrid_from_email and self.admin_email):
            return None

        payload = {
            "personalizations": [
                {
                    "to": [{"email": self.admin_email}],
                }
            ],
            "from": {"email": self.sendgrid_from_email},
            "subject": subject,
            "content": [
                {
                    "type": "text/plain",
                    "value": body,
                }
            ],
        }

        try:
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.DEFAULT_TIMEOUT,
            )
            if response.status_code >= 400:
                detail = f"SendGrid error {response.status_code}: {response.text[:200]}"
                self.logger.warning(detail)
                return ChannelResult(channel="email", ok=False, detail=detail)
            return ChannelResult(channel="email", ok=True, detail="sent via SendGrid")
        except Exception as exc:  # pragma: no cover - network failure path
            detail = f"SendGrid request failed: {exc}"
            self.logger.warning(detail)
            return ChannelResult(channel="email", ok=False, detail=detail)

    def _send_twilio_sms(self, body: str) -> Optional[ChannelResult]:
        if not (
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_sms_from
            and self.admin_sms_number
        ):
            return None

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"
        data = {
            "To": self._ensure_e164(self.admin_sms_number),
            "From": self._ensure_e164(self.twilio_sms_from),
            "Body": body,
        }

        try:
            response = requests.post(
                url,
                data=data,
                auth=(self.twilio_account_sid, self.twilio_auth_token),
                timeout=self.DEFAULT_TIMEOUT,
            )
            if response.status_code >= 400:
                detail = f"Twilio SMS error {response.status_code}: {response.text[:200]}"
                self.logger.warning(detail)
                return ChannelResult(channel="sms", ok=False, detail=detail)
            return ChannelResult(channel="sms", ok=True, detail="sent via Twilio SMS")
        except Exception as exc:  # pragma: no cover - network failure path
            detail = f"Twilio SMS request failed: {exc}"
            self.logger.warning(detail)
            return ChannelResult(channel="sms", ok=False, detail=detail)

    def _send_twilio_whatsapp(self, body: str) -> Optional[ChannelResult]:
        if not (
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_whatsapp_from
            and self.admin_whatsapp_number
        ):
            return None

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.twilio_account_sid}/Messages.json"
        data = {
            "To": self._ensure_whatsapp(self.admin_whatsapp_number),
            "From": self._ensure_whatsapp(self.twilio_whatsapp_from),
            "Body": body,
        }

        try:
            response = requests.post(
                url,
                data=data,
                auth=(self.twilio_account_sid, self.twilio_auth_token),
                timeout=self.DEFAULT_TIMEOUT,
            )
            if response.status_code >= 400:
                detail = f"Twilio WhatsApp error {response.status_code}: {response.text[:200]}"
                self.logger.warning(detail)
                return ChannelResult(channel="whatsapp", ok=False, detail=detail)
            return ChannelResult(channel="whatsapp", ok=True, detail="sent via Twilio WhatsApp")
        except Exception as exc:  # pragma: no cover - network failure path
            detail = f"Twilio WhatsApp request failed: {exc}"
            self.logger.warning(detail)
            return ChannelResult(channel="whatsapp", ok=False, detail=detail)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_e164(number: str) -> str:
        number = number.strip()
        if not number.startswith("+"):
            number = "+" + number.lstrip("+")
        return number

    @staticmethod
    def _ensure_whatsapp(number: str) -> str:
        number = NotificationService._ensure_e164(number)
        if not number.startswith("whatsapp:"):
            return f"whatsapp:{number}"
        return number


# Convenience singleton used by views
notification_service = NotificationService()
