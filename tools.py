"""
Tool definitions and implementations for the prospect follow-up agent.

Each tool wraps an existing API client and returns JSON-serializable data
that Claude can reason about directly.
"""

import json
import logging
from datetime import datetime

import requests

from config import Config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema definitions (Anthropic tool_use format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "name": "get_active_prospects",
        "description": (
            "Fetch all active prospects from the Attio CRM 'Active Pipeline' list. "
            "Returns prospects that are NOT in Won or Lost status, with their name, "
            "email, company, and pipeline stage."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "check_email_history",
        "description": (
            "Check Gmail for the most recent email exchange with a specific prospect. "
            "Returns who sent the last email (me or them), the date, subject, a snippet, "
            "and how many business days since their last reply to us."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The prospect's email address",
                },
            },
            "required": ["email"],
        },
    },
    {
        "name": "check_calendar",
        "description": (
            "Search Google Calendar for future meetings with a specific prospect. "
            "Searches by name, company, and email in event titles and attendee lists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The prospect's full name",
                },
                "email": {
                    "type": "string",
                    "description": "The prospect's email address",
                },
                "company": {
                    "type": "string",
                    "description": "The prospect's company name",
                },
            },
            "required": ["name", "email"],
        },
    },
    {
        "name": "get_available_slots",
        "description": (
            "Find available 30-minute meeting slots on my Google Calendar in the "
            "next 5-7 business days. Returns 3 slots spread across different days "
            "during business hours."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "num_slots": {
                    "type": "integer",
                    "description": "Number of available slots to return (default 3)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "search_meeting_notes",
        "description": (
            "Search Granola meeting notes for a specific prospect by name or company. "
            "Returns the most recent matching meeting's title, date, and full text content "
            "that you can reference in follow-up emails."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The prospect's name to search for",
                },
                "company": {
                    "type": "string",
                    "description": "The prospect's company name to search for",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_slack_message",
        "description": (
            "Send a formatted message to the configured Slack channel via webhook. "
            "Use Slack mrkdwn formatting (*bold*, _italic_, `code`, ```code block```, etc)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The message text in Slack mrkdwn format",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "send_email_to_self",
        "description": (
            "Send an HTML email summary to myself. Use this to deliver the daily "
            "follow-up report to my inbox."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "Email subject line",
                },
                "body_html": {
                    "type": "string",
                    "description": "Email body in HTML format",
                },
            },
            "required": ["subject", "body_html"],
        },
    },
]

# ---------------------------------------------------------------------------
# Lazy-initialized service clients (created on first use)
# ---------------------------------------------------------------------------

_google_services = None
_granola_client = None


def _get_google_services():
    global _google_services
    if _google_services is None:
        from google_client import get_google_services
        _google_services = get_google_services()
    return _google_services


def _get_granola_client():
    global _granola_client
    if _granola_client is None:
        from granola_client import GranolaClient
        _granola_client = GranolaClient()
    return _granola_client


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _serialize(obj):
    """Convert dataclasses and datetimes to JSON-serializable dicts."""
    if obj is None:
        return None
    if hasattr(obj, "__dataclass_fields__"):
        d = {}
        for field_name in obj.__dataclass_fields__:
            d[field_name] = _serialize(getattr(obj, field_name))
        return d
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_get_active_prospects(_input):
    from attio_client import get_prospects
    prospects = get_prospects()
    return json.dumps([_serialize(p) for p in prospects], indent=2)


def _tool_check_email_history(tool_input):
    from google_client import get_last_email_exchange
    _, gmail = _get_google_services()
    email = tool_input["email"]

    # Create a minimal prospect-like object for the function
    class _Prospect:
        pass
    p = _Prospect()
    p.email = email
    p.name = email  # fallback

    thread = get_last_email_exchange(gmail, p, Config.MY_EMAIL)
    if thread is None:
        return json.dumps({"found": False, "message": f"No email history found with {email}"})

    return json.dumps({
        "found": True,
        "last_date": thread.last_date.isoformat(),
        "last_from": thread.last_from,
        "subject": thread.subject,
        "snippet": thread.snippet,
        "business_days_since_reply": thread.days_since_reply,
    }, indent=2)


def _tool_check_calendar(tool_input):
    from google_client import find_future_meetings
    calendar, _ = _get_google_services()

    class _Prospect:
        pass
    p = _Prospect()
    p.name = tool_input["name"]
    p.email = tool_input["email"]
    p.company = tool_input.get("company", "")

    events = find_future_meetings(calendar, p)
    return json.dumps({
        "future_meetings_count": len(events),
        "meetings": [_serialize(e) for e in events],
    }, indent=2)


def _tool_get_available_slots(tool_input):
    from google_client import get_available_slots
    calendar, _ = _get_google_services()
    num = tool_input.get("num_slots", 3)
    slots = get_available_slots(calendar, num_slots=num)
    return json.dumps([_serialize(s) for s in slots], indent=2)


def _tool_search_meeting_notes(tool_input):
    try:
        granola = _get_granola_client()
    except Exception as e:
        return json.dumps({"found": False, "error": f"Granola not available: {e}"})

    class _Prospect:
        pass
    p = _Prospect()
    p.name = tool_input["name"]
    p.company = tool_input.get("company", "")

    try:
        notes = granola.search_notes_for_prospect(p)
    except Exception as e:
        return json.dumps({"found": False, "error": f"Granola search failed: {e}"})

    if notes is None:
        return json.dumps({"found": False, "message": f"No meeting notes found for {p.name}"})

    return json.dumps({
        "found": True,
        "title": notes.title,
        "date": notes.date.isoformat(),
        "content": notes.raw_text[:3000],  # Limit to avoid huge payloads
    }, indent=2)


def _tool_send_slack_message(tool_input):
    text = tool_input["text"]
    if not Config.SLACK_WEBHOOK_URL:
        return json.dumps({"sent": False, "error": "Slack webhook URL not configured"})

    try:
        resp = requests.post(
            Config.SLACK_WEBHOOK_URL,
            json={"text": text, "mrkdwn": True},
            timeout=10,
        )
        resp.raise_for_status()
        return json.dumps({"sent": True})
    except Exception as e:
        return json.dumps({"sent": False, "error": str(e)})


def _tool_send_email_to_self(tool_input):
    _, gmail = _get_google_services()
    from google_client import send_email
    try:
        send_email(gmail, Config.MY_EMAIL, tool_input["subject"], tool_input["body_html"])
        return json.dumps({"sent": True})
    except Exception as e:
        return json.dumps({"sent": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

_TOOL_HANDLERS = {
    "get_active_prospects": _tool_get_active_prospects,
    "check_email_history": _tool_check_email_history,
    "check_calendar": _tool_check_calendar,
    "get_available_slots": _tool_get_available_slots,
    "search_meeting_notes": _tool_search_meeting_notes,
    "send_slack_message": _tool_send_slack_message,
    "send_email_to_self": _tool_send_email_to_self,
}


def execute_tool(name, tool_input):
    """Execute a tool by name and return the result as a string."""
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        logger.info("Executing tool: %s", name)
        result = handler(tool_input)
        logger.debug("Tool %s result: %s", name, result[:200])
        return result
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return json.dumps({"error": f"Tool {name} failed: {str(e)}"})
