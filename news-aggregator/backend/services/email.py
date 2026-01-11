"""Email service for sending briefings via local sendmail."""

import subprocess
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Sequence

from pydantic import BaseModel, EmailStr

from models import Item, Priority

logger = logging.getLogger(__name__)


class EmailConfig(BaseModel):
    """Email configuration."""
    recipients: list[EmailStr]
    subject_prefix: str = "[Liga News]"
    include_summary: bool = True
    include_content: bool = False
    min_priority: Priority = Priority.NONE


class BriefingEmail:
    """Generates and sends briefing emails."""

    PRIORITY_LABELS = {
        Priority.HIGH: "🔴 WICHTIG",
        Priority.MEDIUM: "🟠 BEOBACHTEN",
        Priority.LOW: "🟡 NIEDRIG",
        Priority.NONE: "🔵 INFORMATION",
    }

    PRIORITY_ORDER = [Priority.HIGH, Priority.MEDIUM, Priority.LOW, Priority.NONE]

    def __init__(self, config: EmailConfig):
        self.config = config

    def _group_by_priority(self, items: Sequence[Item]) -> dict[Priority, list[Item]]:
        """Group items by priority level."""
        grouped: dict[Priority, list[Item]] = {p: [] for p in self.PRIORITY_ORDER}
        for item in items:
            if item.priority.value >= self.config.min_priority.value:
                grouped[item.priority].append(item)
        return grouped

    def _format_item_text(self, item: Item) -> str:
        """Format a single item for plain text email."""
        lines = [f"• {item.title}"]
        if item.source:
            lines.append(f"  Quelle: {item.source.name}")
        if self.config.include_summary and item.summary:
            lines.append(f"  {item.summary}")
        if item.url:
            lines.append(f"  Link: {item.url}")
        return "\n".join(lines)

    def _format_item_html(self, item: Item) -> str:
        """Format a single item for HTML email."""
        html = f'<li style="margin-bottom: 12px;">'
        html += f'<strong><a href="{item.url}" style="color: #0369a1;">{item.title}</a></strong>'
        if item.source:
            html += f'<br><span style="color: #6b7280; font-size: 12px;">{item.source.name}</span>'
        if self.config.include_summary and item.summary:
            html += f'<br><span style="color: #374151;">{item.summary}</span>'
        html += '</li>'
        return html

    def generate_text_body(self, items: Sequence[Item], date: datetime) -> str:
        """Generate plain text email body."""
        grouped = self._group_by_priority(items)

        lines = [
            f"Daily Briefing - {date.strftime('%d.%m.%Y')}",
            "=" * 50,
            "",
        ]

        total_count = sum(len(items) for items in grouped.values())
        if total_count == 0:
            lines.append("Keine neuen Meldungen für heute.")
            return "\n".join(lines)

        # Summary counts
        lines.append("Zusammenfassung:")
        for priority in self.PRIORITY_ORDER:
            count = len(grouped[priority])
            if count > 0:
                lines.append(f"  {self.PRIORITY_LABELS[priority]}: {count}")
        lines.append("")

        # Items by priority
        for priority in self.PRIORITY_ORDER:
            priority_items = grouped[priority]
            if not priority_items:
                continue

            lines.append(f"\n{self.PRIORITY_LABELS[priority]}")
            lines.append("-" * 40)
            for item in priority_items:
                lines.append(self._format_item_text(item))
                lines.append("")

        lines.append("-" * 50)
        lines.append("Liga der Freien Wohlfahrtspflege Hessen")
        lines.append("Automatisch generiertes Briefing")

        return "\n".join(lines)

    def generate_html_body(self, items: Sequence[Item], date: datetime) -> str:
        """Generate HTML email body."""
        grouped = self._group_by_priority(items)

        priority_colors = {
            Priority.HIGH: "#dc2626",      # red
            Priority.MEDIUM: "#ea580c",    # orange
            Priority.LOW: "#ca8a04",       # yellow
            Priority.NONE: "#2563eb",      # blue
        }

        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.5; color: #1f2937; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: #0369a1; color: white; padding: 20px; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9fafb; padding: 20px; border: 1px solid #e5e7eb; }}
        .section {{ margin-bottom: 24px; }}
        .section-title {{ font-size: 14px; font-weight: 600; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px; }}
        .footer {{ background: #f3f4f6; padding: 16px; text-align: center; font-size: 12px; color: #6b7280; border-radius: 0 0 8px 8px; }}
        ul {{ list-style: none; padding: 0; margin: 0; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1 style="margin: 0; font-size: 24px;">Daily Briefing</h1>
            <p style="margin: 8px 0 0 0; opacity: 0.9;">{date.strftime('%d. %B %Y')}</p>
        </div>
        <div class="content">
'''

        total_count = sum(len(items) for items in grouped.values())
        if total_count == 0:
            html += '<p>Keine neuen Meldungen für heute.</p>'
        else:
            # Summary
            html += '<div class="section"><h2 style="margin: 0 0 12px 0; font-size: 16px;">Zusammenfassung</h2>'
            html += '<table style="width: 100%;">'
            for priority in self.PRIORITY_ORDER:
                count = len(grouped[priority])
                if count > 0:
                    color = priority_colors[priority]
                    html += f'<tr><td style="padding: 4px 0;"><span style="display: inline-block; width: 12px; height: 12px; background: {color}; border-radius: 50%; margin-right: 8px;"></span>{self.PRIORITY_LABELS[priority]}</td><td style="text-align: right; font-weight: 600;">{count}</td></tr>'
            html += '</table></div>'

            # Items by priority
            for priority in self.PRIORITY_ORDER:
                priority_items = grouped[priority]
                if not priority_items:
                    continue

                color = priority_colors[priority]
                html += f'''
            <div class="section">
                <div class="section-title" style="background: {color}20; color: {color};">{self.PRIORITY_LABELS[priority]}</div>
                <ul>
'''
                for item in priority_items:
                    html += self._format_item_html(item)
                html += '</ul></div>'

        html += '''
        </div>
        <div class="footer">
            <p style="margin: 0;">Liga der Freien Wohlfahrtspflege Hessen</p>
            <p style="margin: 4px 0 0 0;">Automatisch generiertes Briefing</p>
        </div>
    </div>
</body>
</html>'''

        return html

    def send(self, items: Sequence[Item], date: datetime | None = None) -> tuple[bool, str]:
        """Send briefing email to configured recipients."""
        if not self.config.recipients:
            return False, "Keine Empfänger konfiguriert"

        if date is None:
            date = datetime.now()

        subject = f"{self.config.subject_prefix} Briefing {date.strftime('%d.%m.%Y')}"

        # Create multipart message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = "Liga News <noreply@liga-hessen.de>"
        msg["To"] = ", ".join(self.config.recipients)

        # Attach both plain text and HTML versions
        text_body = self.generate_text_body(items, date)
        html_body = self.generate_html_body(items, date)

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            # Send via sendmail
            process = subprocess.run(
                ["/usr/sbin/sendmail", "-t"],
                input=msg.as_string(),
                capture_output=True,
                text=True,
                timeout=30,
            )

            if process.returncode != 0:
                logger.error(f"sendmail failed: {process.stderr}")
                return False, f"Sendmail Fehler: {process.stderr}"

            logger.info(f"Briefing sent to {len(self.config.recipients)} recipients")
            return True, f"E-Mail an {len(self.config.recipients)} Empfänger gesendet"

        except subprocess.TimeoutExpired:
            logger.error("sendmail timeout")
            return False, "Sendmail Timeout"
        except FileNotFoundError:
            logger.error("sendmail not found")
            return False, "Sendmail nicht gefunden"
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False, f"Fehler: {str(e)}"


async def send_daily_briefing(
    items: Sequence[Item],
    recipients: list[str],
    min_priority: Priority = Priority.LOW,
) -> tuple[bool, str]:
    """Convenience function to send daily briefing."""
    config = EmailConfig(
        recipients=recipients,
        min_priority=min_priority,
    )
    briefing = BriefingEmail(config)
    return briefing.send(items)
