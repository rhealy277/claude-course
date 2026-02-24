import logging

import requests

from config import Config

logger = logging.getLogger(__name__)


def send_slack_notification(follow_ups, total_prospects):
    """Send a summary of follow-ups to Slack via webhook."""
    if not Config.SLACK_WEBHOOK_URL:
        logger.info("Slack webhook not configured, skipping")
        return

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Prospect Follow-Up Report",
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Active Prospects:* {total_prospects}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Need Follow-Up:* {len(follow_ups)}",
                },
            ],
        },
        {"type": "divider"},
    ]

    if not follow_ups:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "All caught up! No follow-ups needed today.",
                },
            }
        )
    else:
        for fu in follow_ups:
            p = fu.prospect
            notes_tag = "Notes referenced" if fu.meeting_notes else "No notes"

            days_text = ""
            if fu.email_thread:
                days_text = f" | {fu.email_thread.days_since_reply} biz days since reply"

            future_mtg = ""
            if fu.calendar_events:
                next_mtg = fu.calendar_events[0]
                future_mtg = f" | Next meeting: {next_mtg.start.strftime('%b %d')}"

            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{p.name}* ({p.company})\n"
                            f"Stage: {p.stage}{days_text}{future_mtg}\n"
                            f"_{notes_tag}_"
                        ),
                    },
                }
            )

            # Show the suggested email
            email_preview = fu.suggested_email[:500]
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```{email_preview}```",
                    },
                }
            )
            blocks.append({"type": "divider"})

    payload = {"blocks": blocks}
    try:
        resp = requests.post(Config.SLACK_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Slack notification sent successfully")
    except Exception as e:
        logger.error("Failed to send Slack notification: %s", e)


def send_email_summary(gmail_service, follow_ups, total_prospects):
    """Send an HTML email summary via Gmail."""
    if not Config.MY_EMAIL:
        logger.info("MY_EMAIL not configured, skipping email summary")
        return

    from google_client import send_email

    subject = f"Prospect Follow-Up Report — {len(follow_ups)} follow-ups needed"

    html_parts = [
        "<html><body>",
        "<h2>Prospect Follow-Up Report</h2>",
        f"<p><strong>Active Prospects:</strong> {total_prospects} | "
        f"<strong>Need Follow-Up:</strong> {len(follow_ups)}</p>",
        "<hr>",
    ]

    if not follow_ups:
        html_parts.append("<p>All caught up! No follow-ups needed today.</p>")
    else:
        for fu in follow_ups:
            p = fu.prospect
            notes_tag = "Yes" if fu.meeting_notes else "No"

            html_parts.append(f"<h3>{p.name} ({p.company})</h3>")
            html_parts.append(f"<p><strong>Stage:</strong> {p.stage}")

            if fu.email_thread:
                html_parts.append(
                    f" | <strong>Days since reply:</strong> {fu.email_thread.days_since_reply}"
                )
            html_parts.append(f" | <strong>Meeting notes:</strong> {notes_tag}</p>")

            # Suggested email
            email_escaped = (
                fu.suggested_email.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace("\n", "<br>")
            )
            html_parts.append(
                f"<div style='background:#f5f5f5;padding:12px;border-radius:4px;"
                f"font-family:monospace;white-space:pre-wrap;'>{email_escaped}</div>"
            )
            html_parts.append("<hr>")

    html_parts.append("</body></html>")
    html_body = "\n".join(html_parts)

    send_email(gmail_service, Config.MY_EMAIL, subject, html_body)


def print_console_summary(follow_ups, total_prospects):
    """Pretty-print the follow-up summary to console."""
    print("\n" + "=" * 60)
    print("  PROSPECT FOLLOW-UP REPORT")
    print("=" * 60)
    print(f"  Active Prospects: {total_prospects}")
    print(f"  Need Follow-Up:   {len(follow_ups)}")
    print("-" * 60)

    if not follow_ups:
        print("  All caught up! No follow-ups needed today.")
    else:
        for i, fu in enumerate(follow_ups, 1):
            p = fu.prospect
            print(f"\n  [{i}] {p.name} ({p.company})")
            print(f"      Stage: {p.stage}")

            if fu.email_thread:
                print(
                    f"      Last email: from {'us' if fu.email_thread.last_from == 'me' else 'them'}"
                    f" — {fu.email_thread.days_since_reply} business days ago"
                )
            else:
                print("      No email history found")

            if fu.calendar_events:
                next_mtg = fu.calendar_events[0]
                print(f"      Next meeting: {next_mtg.summary} on {next_mtg.start.strftime('%b %d at %I:%M %p')}")
            else:
                print("      No future meetings scheduled")

            notes_status = f"Yes — '{fu.meeting_notes.title}'" if fu.meeting_notes else "No"
            print(f"      Meeting notes: {notes_status}")
            print(f"      Reason: {fu.reason}")
            print()
            print("      --- Suggested Email ---")
            for line in fu.suggested_email.split("\n"):
                print(f"      {line}")
            print("      " + "-" * 25)

    print("\n" + "=" * 60)
