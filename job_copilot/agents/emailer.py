"""SMTP email (Gmail App Password works)."""

from __future__ import annotations

import logging
import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

from job_copilot.config import EmailConfig, get_email_config

log = logging.getLogger(__name__)


def send_email(subject: str, body: str, attachments: list[Path] = (), cfg: EmailConfig | None = None,
               to: str | None = None) -> bool:
    cfg = cfg or get_email_config()
    if not cfg.configured:
        log.warning("Email not configured (SMTP_USER/SMTP_PASSWORD/EMAIL_TO); skipping '%s'", subject)
        return False
    msg = EmailMessage()
    msg["From"] = cfg.user
    msg["To"] = to or cfg.to
    msg["Subject"] = subject
    msg.set_content(body)
    for path in attachments:
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    try:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(cfg.user, cfg.password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        # The report is already saved locally; a mail problem must not abort the run.
        log.error("Could not send '%s': %s. Check SMTP_* in .env (Gmail needs an App Password).", subject, e)
        return False
    log.info("Email sent: %s", subject)
    return True
