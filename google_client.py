import base64
import logging
import os
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config import Config
from models import CalendarEvent, EmailThread, MeetingSlot

logger = logging.getLogger(__name__)


def get_google_credentials():
    """Load or refresh Google OAuth credentials."""
    creds = None
    if os.path.exists(Config.GOOGLE_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(
            Config.GOOGLE_TOKEN_PATH, Config.GOOGLE_SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(Config.GOOGLE_CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Google credentials file not found at {Config.GOOGLE_CREDENTIALS_PATH}. "
                    "Download it from Google Cloud Console."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                Config.GOOGLE_CREDENTIALS_PATH, Config.GOOGLE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(Config.GOOGLE_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds


def get_google_services():
    """Build and return Google Calendar and Gmail service objects."""
    creds = get_google_credentials()
    calendar = build("calendar", "v3", credentials=creds)
    gmail = build("gmail", "v1", credentials=creds)
    return calendar, gmail


# ---------------------------------------------------------------------------
# Calendar functions
# ---------------------------------------------------------------------------


def find_future_meetings(calendar_service, prospect):
    """Search for future calendar events involving a prospect."""
    tz = ZoneInfo(Config.TIMEZONE)
    now = datetime.now(tz).isoformat()
    events = []

    # Search by prospect name and company
    queries = [prospect.name]
    if prospect.company:
        queries.append(prospect.company)

    seen_ids = set()
    for query in queries:
        try:
            result = (
                calendar_service.events()
                .list(
                    calendarId="primary",
                    q=query,
                    timeMin=now,
                    maxResults=10,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for item in result.get("items", []):
                event_id = item["id"]
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                start_str = item["start"].get("dateTime", item["start"].get("date"))
                end_str = item["end"].get("dateTime", item["end"].get("date"))
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str)

                events.append(
                    CalendarEvent(
                        summary=item.get("summary", ""),
                        start=start,
                        end=end,
                        is_future=True,
                    )
                )
        except Exception as e:
            logger.warning("Calendar search for '%s' failed: %s", query, e)

    # Also search by attendee email
    try:
        result = (
            calendar_service.events()
            .list(
                calendarId="primary",
                q=prospect.email,
                timeMin=now,
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        for item in result.get("items", []):
            event_id = item["id"]
            if event_id in seen_ids:
                continue
            # Also check attendee list directly
            attendees = item.get("attendees", [])
            if any(a.get("email", "").lower() == prospect.email.lower() for a in attendees):
                seen_ids.add(event_id)
                start_str = item["start"].get("dateTime", item["start"].get("date"))
                end_str = item["end"].get("dateTime", item["end"].get("date"))
                events.append(
                    CalendarEvent(
                        summary=item.get("summary", ""),
                        start=datetime.fromisoformat(start_str),
                        end=datetime.fromisoformat(end_str),
                        is_future=True,
                    )
                )
    except Exception as e:
        logger.warning("Calendar attendee search for '%s' failed: %s", prospect.email, e)

    logger.info(
        "Found %d future meetings for %s", len(events), prospect.name
    )
    return events


def _get_business_days_range(num_days=7):
    """Get start and end datetimes covering the next N business days."""
    tz = ZoneInfo(Config.TIMEZONE)
    today = datetime.now(tz).date()
    biz_days_found = 0
    current = today + timedelta(days=1)  # start tomorrow

    while biz_days_found < num_days:
        if current.weekday() < 5:  # Mon-Fri
            biz_days_found += 1
            end_date = current
        current += timedelta(days=1)

    start_hour, start_min = map(int, Config.AVAILABILITY_WINDOW_START.split(":"))
    start_dt = datetime.combine(today + timedelta(days=1), datetime.min.time().replace(
        hour=start_hour, minute=start_min
    ), tzinfo=tz)

    end_hour, end_min = map(int, Config.AVAILABILITY_WINDOW_END.split(":"))
    end_dt = datetime.combine(end_date, datetime.min.time().replace(
        hour=end_hour, minute=end_min
    ), tzinfo=tz)

    return start_dt, end_dt


def get_available_slots(calendar_service, num_slots=3):
    """Find available meeting slots in the next 5-7 business days."""
    tz = ZoneInfo(Config.TIMEZONE)
    start_dt, end_dt = _get_business_days_range(7)
    duration = timedelta(minutes=Config.MEETING_DURATION_MINUTES)

    # Get busy times from Google Calendar
    body = {
        "timeMin": start_dt.isoformat(),
        "timeMax": end_dt.isoformat(),
        "items": [{"id": "primary"}],
    }
    try:
        result = calendar_service.freebusy().query(body=body).execute()
        busy_periods = result["calendars"]["primary"]["busy"]
    except Exception as e:
        logger.warning("Freebusy query failed: %s — using empty busy list", e)
        busy_periods = []

    busy_times = []
    for period in busy_periods:
        busy_start = datetime.fromisoformat(period["start"])
        busy_end = datetime.fromisoformat(period["end"])
        busy_times.append((busy_start, busy_end))

    # Generate candidate slots during business hours on business days
    start_hour, start_min = map(int, Config.AVAILABILITY_WINDOW_START.split(":"))
    end_hour, end_min = map(int, Config.AVAILABILITY_WINDOW_END.split(":"))

    candidates = []
    current_date = start_dt.date()
    while current_date <= end_dt.date():
        if current_date.weekday() >= 5:  # skip weekends
            current_date += timedelta(days=1)
            continue

        slot_start = datetime.combine(
            current_date,
            datetime.min.time().replace(hour=start_hour, minute=start_min),
            tzinfo=tz,
        )
        day_end = datetime.combine(
            current_date,
            datetime.min.time().replace(hour=end_hour, minute=end_min),
            tzinfo=tz,
        )

        while slot_start + duration <= day_end:
            slot_end = slot_start + duration
            # Check if this slot conflicts with any busy period
            is_free = True
            for busy_start, busy_end in busy_times:
                if slot_start < busy_end and slot_end > busy_start:
                    is_free = False
                    # Jump past the busy period
                    slot_start = busy_end
                    break
            if is_free:
                candidates.append((slot_start, slot_end))
                slot_start += timedelta(minutes=30)  # move to next 30-min boundary
            # if not free, slot_start was already advanced past busy period

        current_date += timedelta(days=1)

    # Pick slots spread across different days when possible
    slots = []
    used_dates = set()
    # First pass: one slot per day
    for start, end in candidates:
        if start.date() not in used_dates:
            slots.append(_make_slot(start, end, tz))
            used_dates.add(start.date())
            if len(slots) >= num_slots:
                break
    # Second pass: fill remaining from any day
    if len(slots) < num_slots:
        for start, end in candidates:
            if len(slots) >= num_slots:
                break
            if not any(s.start == start for s in slots):
                slots.append(_make_slot(start, end, tz))

    logger.info("Found %d available meeting slots", len(slots))
    return slots[:num_slots]


def _make_slot(start, end, tz):
    """Create a MeetingSlot with a nicely formatted string."""
    formatted = start.strftime("%A, %B %-d at %-I:%M %p") + " ET"
    return MeetingSlot(start=start, end=end, formatted=formatted)


# ---------------------------------------------------------------------------
# Gmail functions
# ---------------------------------------------------------------------------


def _count_business_days(from_date, to_date):
    """Count the number of business days between two dates (exclusive of from_date)."""
    if from_date.date() == to_date.date():
        return 0
    count = 0
    current = from_date.date() + timedelta(days=1)
    target = to_date.date()
    while current <= target:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def get_last_email_exchange(gmail_service, prospect, my_email):
    """Find the most recent email exchange with a prospect."""
    query = f"from:{prospect.email} OR to:{prospect.email}"
    try:
        result = (
            gmail_service.users()
            .messages()
            .list(userId="me", q=query, maxResults=5)
            .execute()
        )
    except Exception as e:
        logger.warning("Gmail search for %s failed: %s", prospect.email, e)
        return None

    messages = result.get("messages", [])
    if not messages:
        logger.info("No emails found with %s", prospect.email)
        return None

    # Get the most recent message
    msg_id = messages[0]["id"]
    try:
        msg = (
            gmail_service.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata", metadataHeaders=["From", "Date", "Subject"])
            .execute()
        )
    except Exception as e:
        logger.warning("Failed to get message %s: %s", msg_id, e)
        return None

    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    from_header = headers.get("From", "")
    subject = headers.get("Subject", "")
    date_str = headers.get("Date", "")
    snippet = msg.get("snippet", "")

    # Determine if the last email was from me or them
    from_lower = from_header.lower()
    if my_email.lower() in from_lower:
        last_from = "me"
    else:
        last_from = "them"

    # Parse the date
    try:
        from email.utils import parsedate_to_datetime
        last_date = parsedate_to_datetime(date_str)
    except Exception:
        last_date = datetime.now(ZoneInfo(Config.TIMEZONE))

    tz = ZoneInfo(Config.TIMEZONE)
    now = datetime.now(tz)
    if last_date.tzinfo is None:
        last_date = last_date.replace(tzinfo=tz)

    # Calculate business days since their last reply
    # If last email was from me, look for the most recent email FROM them
    if last_from == "me":
        # Search specifically for their last email to us
        their_query = f"from:{prospect.email}"
        try:
            their_result = (
                gmail_service.users()
                .messages()
                .list(userId="me", q=their_query, maxResults=1)
                .execute()
            )
            their_messages = their_result.get("messages", [])
            if their_messages:
                their_msg = (
                    gmail_service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=their_messages[0]["id"],
                        format="metadata",
                        metadataHeaders=["Date"],
                    )
                    .execute()
                )
                their_headers = {
                    h["name"]: h["value"]
                    for h in their_msg.get("payload", {}).get("headers", [])
                }
                their_date_str = their_headers.get("Date", "")
                their_date = parsedate_to_datetime(their_date_str)
                if their_date.tzinfo is None:
                    their_date = their_date.replace(tzinfo=tz)
                days_since = _count_business_days(their_date, now)
            else:
                # They've never emailed us
                days_since = 999
        except Exception:
            days_since = _count_business_days(last_date, now)
    else:
        # Last email was from them
        days_since = _count_business_days(last_date, now)

    return EmailThread(
        last_date=last_date,
        last_from=last_from,
        subject=subject,
        snippet=snippet,
        days_since_reply=days_since,
    )


def send_email(gmail_service, to, subject, body_html):
    """Send an email via Gmail API."""
    message = MIMEText(body_html, "html")
    message["to"] = to
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    try:
        gmail_service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        logger.info("Email sent to %s", to)
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to, e)
