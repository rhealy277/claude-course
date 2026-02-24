import json
import logging

import anthropic

from config import Config

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a sales professional writing a brief follow-up email to a prospect.

Rules:
- Keep the email to 3-5 sentences maximum (excluding the time slots).
- Be warm and professional. Not pushy or salesy.
- If meeting notes are provided, reference a specific detail from the conversation.
  Do NOT fabricate details. Only reference what's actually in the notes.
- If no meeting notes are available, write a warm, genuine check-in.
- Include exactly 3 proposed meeting times as a numbered list at the end.
- End with a soft call to action like "Would any of these work?" or "Let me know if any of these times work for you."
- Sign off with just a first name.
- Do NOT include a subject line in the email body.

Return your response as a JSON object with two keys:
  "subject": a short, casual email subject line (under 60 characters, not salesy)
  "body": the full email body text

Return ONLY the JSON object, no other text."""


def generate_follow_up_email(follow_up):
    """Generate a personalized follow-up email using Claude."""
    prospect = follow_up.prospect
    slots = follow_up.available_slots
    notes = follow_up.meeting_notes

    # Build context
    context_parts = [
        f"Prospect: {prospect.name}",
        f"Company: {prospect.company}" if prospect.company else "",
        f"Pipeline stage: {prospect.stage}",
    ]

    if follow_up.email_thread:
        thread = follow_up.email_thread
        context_parts.append(
            f"Last email was from: {'us' if thread.last_from == 'me' else 'them'}"
        )
        context_parts.append(f"Subject: {thread.subject}")
        context_parts.append(
            f"Business days since their last reply: {thread.days_since_reply}"
        )
    else:
        context_parts.append("No previous email history found.")

    if notes and notes.raw_text.strip():
        # Truncate to avoid excessive token usage
        truncated_notes = notes.raw_text[:2000]
        context_parts.append(
            f"\nMost recent meeting notes (from '{notes.title}'):\n{truncated_notes}"
        )
    else:
        context_parts.append(
            "\nNo meeting notes available. Write a warm, generic check-in."
        )

    slot_lines = [f"  {i + 1}. {slot.formatted}" for i, slot in enumerate(slots)]
    if slot_lines:
        context_parts.append(
            "\nAvailable time slots to propose:\n" + "\n".join(slot_lines)
        )
    else:
        context_parts.append(
            "\nNo specific time slots available. Ask them to suggest a time."
        )

    user_prompt = "\n".join(p for p in context_parts if p)

    try:
        client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = response.content[0].text.strip()

        # Parse JSON response
        try:
            parsed = json.loads(text)
            subject = parsed.get("subject", "Following up")
            body = parsed.get("body", text)
        except json.JSONDecodeError:
            # If Claude didn't return valid JSON, use the raw text
            logger.warning("Claude returned non-JSON response, using raw text")
            subject = "Following up"
            body = text

        logger.info("Generated follow-up email for %s", prospect.name)
        return subject, body

    except Exception as e:
        logger.error("Failed to generate email for %s: %s", prospect.name, e)
        # Fallback template
        slot_text = "\n".join(f"  {i + 1}. {s.formatted}" for i, s in enumerate(slots))
        body = (
            f"Hi {prospect.name.split()[0]},\n\n"
            f"I wanted to follow up and see if you had a chance to review my last message. "
            f"Would you be open to a quick call this week?\n\n"
            f"Here are a few times that work on my end:\n{slot_text}\n\n"
            f"Let me know what works best for you.\n\nBest"
        )
        return "Quick follow-up", body
