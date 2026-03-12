"""Render and send digest emails.

Produces Liga-branded HTML + plain text from the structured LLM content JSON.
"""

import logging
import subprocess
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Any

logger = logging.getLogger(__name__)

LIGA_BLUE = "#003399"
URGENT_RED = "#dc2626"
TOP_AMBER = "#b45309"
FURTHER_GRAY = "#4b5563"


def _format_date_de(d: date | datetime) -> str:
    """Format date in German style."""
    months = [
        "", "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day}. {months[d.month]} {d.year}"


def render_digest_html(content: dict[str, Any], digest_date: date, total_items: int) -> str:
    """Render digest content to HTML email."""
    date_str = _format_date_de(digest_date)
    editorial = escape(content.get("editorial_intro", ""))
    urgent = content.get("urgent", [])
    top_stories = content.get("top_stories", [])
    further = content.get("further_news", [])

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #1f2937; margin: 0; padding: 0; background: #f3f4f6; }}
.wrap {{ max-width: 640px; margin: 0 auto; background: #ffffff; }}
.header {{ background: {LIGA_BLUE}; color: #ffffff; padding: 24px 28px; }}
.header h1 {{ margin: 0; font-size: 22px; font-weight: 600; }}
.header .date {{ margin: 6px 0 0; opacity: 0.85; font-size: 14px; }}
.intro {{ padding: 20px 28px 8px; font-size: 15px; color: #374151; font-style: italic; border-bottom: 1px solid #e5e7eb; }}
.section {{ padding: 16px 28px; }}
.section-title {{ font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin: 0 0 12px; padding: 6px 10px; border-radius: 4px; }}
.item {{ margin-bottom: 16px; }}
.item h3 {{ margin: 0 0 4px; font-size: 15px; }}
.item h3 a {{ color: {LIGA_BLUE}; text-decoration: none; }}
.item h3 a:hover {{ text-decoration: underline; }}
.item .context {{ margin: 4px 0 0; font-size: 14px; color: #374151; }}
.item .meta {{ margin: 4px 0 0; font-size: 12px; color: #6b7280; }}
.bullet {{ margin-bottom: 8px; font-size: 14px; }}
.bullet a {{ color: {LIGA_BLUE}; text-decoration: none; }}
.stats {{ padding: 12px 28px; background: #f9fafb; font-size: 13px; color: #6b7280; border-top: 1px solid #e5e7eb; }}
.footer {{ padding: 16px 28px; text-align: center; font-size: 12px; color: #9ca3af; background: #f3f4f6; }}
</style>
</head>
<body>
<div class="wrap">
<div class="header">
  <h1>Tagesüberblick</h1>
  <div class="date">{date_str}</div>
</div>
"""

    if editorial:
        html += f'<div class="intro"><p style="margin:0;">{editorial}</p></div>\n'

    # Urgent section
    if urgent:
        html += f"""<div class="section">
<div class="section-title" style="background: {URGENT_RED}15; color: {URGENT_RED};">🔴 DRINGEND</div>
"""
        for entry in urgent:
            headline = escape(entry.get("headline", ""))
            context = escape(entry.get("context", ""))
            url = entry.get("url", "")
            source = escape(entry.get("source", ""))
            aks = ", ".join(entry.get("assigned_aks", []))
            link = f'<a href="{escape(url)}">{headline}</a>' if url else headline
            html += f"""<div class="item">
  <h3>{link}</h3>
  <div class="context">{context}</div>
  <div class="meta">Quelle: {source}{f' | AK: {aks}' if aks else ''}</div>
</div>
"""
        html += "</div>\n"

    # Top stories
    if top_stories:
        html += f"""<div class="section">
<div class="section-title" style="background: {TOP_AMBER}15; color: {TOP_AMBER};">📌 WICHTIGSTE ENTWICKLUNGEN</div>
"""
        for entry in top_stories:
            headline = escape(entry.get("headline", ""))
            context = escape(entry.get("context", ""))
            url = entry.get("url", "")
            source = escape(entry.get("source", ""))
            aks = ", ".join(entry.get("assigned_aks", []))
            link = f'<a href="{escape(url)}">{headline}</a>' if url else headline
            html += f"""<div class="item">
  <h3>{link}</h3>
  <div class="context">{context}</div>
  <div class="meta">Quelle: {source}{f' | AK: {aks}' if aks else ''}</div>
</div>
"""
        html += "</div>\n"

    # Further news
    if further:
        html += f"""<div class="section">
<div class="section-title" style="background: {FURTHER_GRAY}15; color: {FURTHER_GRAY};">📰 WEITERE MELDUNGEN</div>
"""
        for entry in further:
            headline = escape(entry.get("headline", ""))
            url = entry.get("url", "")
            link = f'<a href="{escape(url)}">{headline}</a>' if url else headline
            html += f'<div class="bullet">• {link}</div>\n'
        html += "</div>\n"

    # Stats
    n_urgent = len(urgent)
    n_top = len(top_stories)
    n_further = len(further)
    html += f"""<div class="stats">
📊 {total_items} Artikel verarbeitet — {n_urgent + n_top + n_further} im Überblick \
({n_urgent} dringend, {n_top} wichtig, {n_further} weitere)
</div>
"""

    html += """<div class="footer">
<p style="margin:0;">Liga der Freien Wohlfahrtspflege in Hessen e.V.</p>
<p style="margin:4px 0 0;">Automatisch generierter Tagesüberblick</p>
</div>
</div>
</body>
</html>"""

    return html


def render_digest_text(content: dict[str, Any], digest_date: date, total_items: int) -> str:
    """Render digest content to plain text email."""
    date_str = _format_date_de(digest_date)
    lines = [
        f"TAGESÜBERBLICK — {date_str}",
        "=" * 50,
        "",
    ]

    editorial = content.get("editorial_intro", "")
    if editorial:
        lines.append(editorial)
        lines.append("")

    urgent = content.get("urgent", [])
    if urgent:
        lines.append("🔴 DRINGEND")
        lines.append("-" * 40)
        for entry in urgent:
            lines.append(f"  • {entry.get('headline', '')}")
            if entry.get("context"):
                lines.append(f"    {entry['context']}")
            meta_parts = []
            if entry.get("source"):
                meta_parts.append(f"Quelle: {entry['source']}")
            if entry.get("url"):
                meta_parts.append(entry["url"])
            if meta_parts:
                lines.append(f"    {' | '.join(meta_parts)}")
            lines.append("")

    top_stories = content.get("top_stories", [])
    if top_stories:
        lines.append("📌 WICHTIGSTE ENTWICKLUNGEN")
        lines.append("-" * 40)
        for entry in top_stories:
            lines.append(f"  • {entry.get('headline', '')}")
            if entry.get("context"):
                lines.append(f"    {entry['context']}")
            meta_parts = []
            if entry.get("source"):
                meta_parts.append(f"Quelle: {entry['source']}")
            if entry.get("url"):
                meta_parts.append(entry["url"])
            if meta_parts:
                lines.append(f"    {' | '.join(meta_parts)}")
            lines.append("")

    further = content.get("further_news", [])
    if further:
        lines.append("📰 WEITERE MELDUNGEN")
        lines.append("-" * 40)
        for entry in further:
            headline = entry.get("headline", "")
            url = entry.get("url", "")
            lines.append(f"  • {headline}")
            if url:
                lines.append(f"    {url}")
        lines.append("")

    lines.append("-" * 50)
    n_total = len(urgent) + len(top_stories) + len(further)
    lines.append(f"📊 {total_items} Artikel verarbeitet, {n_total} im Überblick")
    lines.append("")
    lines.append("Liga der Freien Wohlfahrtspflege in Hessen e.V.")
    lines.append("Automatisch generierter Tagesüberblick")

    return "\n".join(lines)


def send_digest_email(
    html_body: str,
    text_body: str,
    recipients: list[str],
    date: datetime | None = None,
) -> tuple[bool, str]:
    """Send digest email via sendmail.

    Returns (success, message).
    """
    if not recipients:
        return False, "Keine Empfänger konfiguriert"

    date_str = _format_date_de(date) if date else "Heute"
    subject = f"[Liga News] Tagesüberblick {date_str}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Liga News <noreply@liga-hessen.de>"
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
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

        logger.info(f"Digest sent to {len(recipients)} recipients")
        return True, f"E-Mail an {len(recipients)} Empfänger gesendet"

    except subprocess.TimeoutExpired:
        return False, "Sendmail Timeout"
    except FileNotFoundError:
        return False, "Sendmail nicht gefunden"
    except Exception as e:
        logger.error(f"Digest send error: {e}")
        return False, f"Fehler: {str(e)}"
