"""Email delivery for auth flows (currently just MFA codes)."""

import os
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Protocol


log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send_mfa_code(self, to_email: str, code: str, ttl: tuple[int, str]) -> None: ...


class SMTPMailer:
    """Production SMTP mailer. Credentials read from env vars."""

    def __init__(
        self,
        host: str = "smtp.zoho.com",
        port: int = 587,
        username: str | None = None,
        password: str | None = None,
        from_address: str | None = None,
    ):
        self.host = host
        self.port = port
        self.username = username or os.environ.get("PD_SMTP_USER", "pdsupport@pixelateddream.net")
        self.password = password or os.environ.get("PD_SMTP_PASS", "")
        self.from_address = from_address or self.username

    def send_mfa_code(self, to_email: str, code: str, ttl: tuple[int, str]) -> None:
        amount, unit = ttl
        unit = unit if amount != 1 else unit[:-1]
        body = (
            f"Your 2-FA EMail code is: {code}\n"
            f"This code expires in {amount} {unit}.\n\n"
            "If you did not request this code, you can ignore this EMail."
        )

        msg = MIMEMultipart()
        msg["From"] = self.from_address
        msg["To"] = to_email
        msg["Subject"] = "2-Factor Authentication: EMail Code"
        msg.attach(MIMEText(body, "plain"))

        try:
            with smtplib.SMTP(self.host, self.port) as smtp:
                smtp.starttls()
                smtp.login(self.username, self.password)
                smtp.sendmail(msg["From"], msg["To"], msg.as_string())
        except Exception as e:
            log.error("Failed to send MFA code to %s: %s", to_email, e)


class NullMailer:
    """No-op mailer for tests; records the last code sent."""

    def __init__(self):
        self.sent: list[tuple[str, str, tuple[int, str]]] = []

    def send_mfa_code(self, to_email: str, code: str, ttl: tuple[int, str]) -> None:
        self.sent.append((to_email, code, ttl))
        log.info("NullMailer: would send code %s to %s", code, to_email)
