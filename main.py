#!/usr/bin/env python3
"""
Prospect Follow-Up Tracker

Checks Attio CRM Active Pipeline, cross-references Google Calendar and Gmail,
and generates personalized follow-up emails for prospects who haven't replied
in 2+ business days.
"""

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from config import Config, validate_config
from models import FollowUp

logger = logging.getLogger("prospect_tracker")


def setup_logging(level=logging.INFO):
    """Configure logging for console output."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run(dry_run=False):
    """Main orchestration: pull prospects, check activity, generate follow-ups."""
    from attio_client import get_prospects
    from email_generator import generate_follow_up_email
    from google_client import (
        find_future_meetings,
        get_available_slots,
        get_google_services,
        get_last_email_exchange,
    )
    from notifier import (
        print_console_summary,
        send_email_summary,
        send_slack_notification,
    )

    tz = ZoneInfo(Config.TIMEZONE)
    now = datetime.now(tz)
    logger.info("Starting prospect follow-up check at %s", now.strftime("%Y-%m-%d %H:%M %Z"))

    # --- Phase 1: Initialize services ---
    logger.info("Initializing Google services...")
    try:
        calendar_service, gmail_service = get_google_services()
    except Exception as e:
        logger.error("Failed to initialize Google services: %s", e)
        logger.error("Run 'python setup_google.py' to set up Google OAuth.")
        sys.exit(1)

    # Granola is optional — initialize only if configured
    granola = None
    if Config.GRANOLA_REFRESH_TOKEN or Config.GRANOLA_CLIENT_ID:
        try:
            from granola_client import GranolaClient
            granola = GranolaClient()
            logger.info("Granola client initialized")
        except Exception as e:
            logger.warning("Granola initialization failed (continuing without): %s", e)

    # --- Phase 2: Fetch prospects from Attio ---
    logger.info("Fetching active prospects from Attio...")
    try:
        prospects = get_prospects()
    except Exception as e:
        logger.error("Failed to fetch prospects from Attio: %s", e)
        sys.exit(1)

    if not prospects:
        logger.info("No active prospects found in pipeline.")
        print_console_summary([], 0)
        return

    logger.info("Found %d active prospects", len(prospects))

    # --- Phase 3: Check email and calendar for each prospect ---
    needs_follow_up = []

    for prospect in prospects:
        logger.info("Checking %s (%s)...", prospect.name, prospect.company)

        # Check Gmail for last email exchange
        email_thread = None
        try:
            email_thread = get_last_email_exchange(
                gmail_service, prospect, Config.MY_EMAIL
            )
        except Exception as e:
            logger.warning("Gmail check failed for %s: %s", prospect.name, e)

        # Check Google Calendar for future meetings
        calendar_events = []
        try:
            calendar_events = find_future_meetings(calendar_service, prospect)
        except Exception as e:
            logger.warning("Calendar check failed for %s: %s", prospect.name, e)

        # Determine if follow-up is needed
        has_future_meeting = len(calendar_events) > 0
        awaiting_reply = False
        reason = ""

        if email_thread:
            if email_thread.last_from == "me" and email_thread.days_since_reply >= Config.BUSINESS_DAYS_THRESHOLD:
                awaiting_reply = True
                reason = (
                    f"No reply in {email_thread.days_since_reply} business days"
                )
            elif email_thread.last_from == "them":
                # They replied — no follow-up needed
                logger.info(
                    "  %s replied %d business days ago — no follow-up needed",
                    prospect.name,
                    email_thread.days_since_reply,
                )
                continue
        else:
            # No email history — might need an initial outreach
            awaiting_reply = True
            reason = "No email history found"

        if has_future_meeting and not awaiting_reply:
            logger.info(
                "  %s has a future meeting — no follow-up needed", prospect.name
            )
            continue

        if awaiting_reply:
            if has_future_meeting:
                reason += " (but has a future meeting scheduled)"

            needs_follow_up.append(
                {
                    "prospect": prospect,
                    "email_thread": email_thread,
                    "calendar_events": calendar_events,
                    "reason": reason,
                }
            )
            logger.info("  %s needs follow-up: %s", prospect.name, reason)

    logger.info(
        "%d of %d prospects need follow-up",
        len(needs_follow_up),
        len(prospects),
    )

    # --- Phase 4: Get available meeting slots (shared for all) ---
    available_slots = []
    if needs_follow_up:
        try:
            available_slots = get_available_slots(calendar_service, num_slots=3)
            logger.info(
                "Found %d available meeting slots: %s",
                len(available_slots),
                [s.formatted for s in available_slots],
            )
        except Exception as e:
            logger.warning("Failed to get available slots: %s", e)

    # --- Phase 5: Search Granola and generate emails ---
    follow_ups = []

    for item in needs_follow_up:
        prospect = item["prospect"]

        # Search Granola for meeting notes
        meeting_notes = None
        if granola:
            try:
                meeting_notes = granola.search_notes_for_prospect(prospect)
                if meeting_notes:
                    logger.info(
                        "  Found Granola notes: '%s'", meeting_notes.title
                    )
            except Exception as e:
                logger.warning(
                    "  Granola search failed for %s: %s", prospect.name, e
                )

        # Build a temporary FollowUp for the generator
        fu = FollowUp(
            prospect=prospect,
            email_thread=item["email_thread"],
            calendar_events=item["calendar_events"],
            meeting_notes=meeting_notes,
            available_slots=available_slots,
            suggested_email="",
            reason=item["reason"],
        )

        # Generate follow-up email via Claude
        try:
            subject, body = generate_follow_up_email(fu)
            fu.suggested_email = f"Subject: {subject}\n\n{body}"
        except Exception as e:
            logger.error("Email generation failed for %s: %s", prospect.name, e)
            fu.suggested_email = "(Email generation failed)"

        follow_ups.append(fu)

    # --- Phase 6: Send notifications ---
    total = len(prospects)

    # Always print to console
    print_console_summary(follow_ups, total)

    if not dry_run:
        # Send Slack notification
        send_slack_notification(follow_ups, total)

        # Send email summary
        try:
            send_email_summary(gmail_service, follow_ups, total)
        except Exception as e:
            logger.warning("Email summary failed: %s", e)

    logger.info("Done! Processed %d prospects, generated %d follow-ups.", total, len(follow_ups))


def main():
    parser = argparse.ArgumentParser(
        description="Prospect Follow-Up Tracker"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print to console only, skip Slack and email notifications",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)
    validate_config()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
