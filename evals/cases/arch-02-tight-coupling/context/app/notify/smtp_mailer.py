import os
import smtplib
from email.message import EmailMessage


class SmtpMailer:
    def __init__(self, host, port):
        self.host, self.port = host, port

    @classmethod
    def from_env(cls):
        return cls(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "25")))

    def send(self, recipients, subject, body):
        msg = EmailMessage()
        msg["To"], msg["Subject"] = ", ".join(recipients), subject
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port) as smtp:
            smtp.send_message(msg)
