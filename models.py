from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Prospect:
    name: str
    email: str
    company: str
    attio_entry_id: str
    attio_record_id: str
    stage: str


@dataclass
class EmailThread:
    last_date: datetime
    last_from: str  # "me" or "them"
    subject: str
    snippet: str
    days_since_reply: int  # business days since their last email to us


@dataclass
class CalendarEvent:
    summary: str
    start: datetime
    end: datetime
    is_future: bool


@dataclass
class MeetingSlot:
    start: datetime
    end: datetime
    formatted: str  # e.g. "Tuesday, March 3 at 2:00 PM ET"


@dataclass
class MeetingNotes:
    title: str
    date: datetime
    key_points: list
    raw_text: str


@dataclass
class FollowUp:
    prospect: Prospect
    email_thread: EmailThread | None
    calendar_events: list
    meeting_notes: MeetingNotes | None
    available_slots: list
    suggested_email: str
    reason: str
