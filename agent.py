#!/usr/bin/env python3
"""
Prospect Follow-Up Agent

A Claude-powered agent that reviews your Attio pipeline, checks email/calendar
activity, and drafts personalized follow-up emails — using its own judgment
rather than rigid if/else logic.

Usage:
    python agent.py              # Full run: analyze + Slack + email
    python agent.py --dry-run    # Analyze + console only, no notifications
    python agent.py --debug      # Verbose logging
"""

import argparse
import logging
import sys

import anthropic

from config import Config, validate_config
from tools import TOOL_DEFINITIONS, execute_tool

logger = logging.getLogger("prospect_agent")

SYSTEM_PROMPT = """\
You are my daily sales follow-up assistant. Every morning you review my active \
sales pipeline and help me stay on top of prospect communication.

## Your Workflow

1. **Pull prospects**: Use `get_active_prospects` to get all deals from my Attio \
"Active Pipeline" (already excludes Won/Lost).

2. **Check each prospect**: For every prospect that has an email address:
   - Use `check_email_history` to see our last email exchange — who sent it, when, \
and how many business days since they replied.
   - Use `check_calendar` to see if we have a future meeting scheduled with them.

3. **Identify who needs follow-up**: A prospect needs follow-up if:
   - The last email was FROM ME (I sent last) and it's been 2+ business days \
with no reply from them.
   - OR there's no email history at all and no future meeting.
   - If they replied recently or we have a meeting coming up, they're fine — skip them.
   - Use your judgment for edge cases.

4. **For prospects needing follow-up**:
   - Use `search_meeting_notes` to find Granola notes from our conversations. \
Search by their name first, then by company if nothing found.
   - Use `get_available_slots` to find 3 open times on my calendar.

5. **Draft follow-up emails**: For each prospect needing follow-up, write a short \
personalized email:
   - Reference something SPECIFIC from the meeting notes (if available). \
Do not fabricate details.
   - 3-5 sentences max, warm and professional, never pushy or salesy.
   - End with 3 proposed meeting times as a numbered list.
   - Sign off with just a first name.
   - If no meeting notes exist, write a warm genuine check-in instead.

6. **Deliver results**:
   - First, print a clear console summary for me showing all prospects checked \
and all drafted emails.
   - Then use `send_slack_message` to post a formatted summary to Slack with:
     - Header with today's date
     - Overview stats (prospects checked, follow-ups needed)
     - For each follow-up: prospect name, company, why they need follow-up, \
and the full drafted email
   - Then use `send_email_to_self` to send an HTML email summary to my inbox.

## Important Guidelines
- Be thorough — check EVERY prospect, don't skip any.
- Be efficient with tool calls — batch when possible (e.g. you can check email \
history for multiple prospects in the same response).
- If a tool fails, note the error and continue with other prospects.
- If Granola notes aren't available, that's fine — draft emails without them.
- Today's emails should feel like they're written by a real human, not a template.
- Keep the console output clean and scannable.
"""

DRY_RUN_ADDENDUM = """

NOTE: This is a DRY RUN. Do NOT call `send_slack_message` or `send_email_to_self`. \
Only print results to the console. Still do all the analysis and draft all emails.
"""


def run_agent(dry_run=False):
    """Run the prospect follow-up agent."""
    client = anthropic.Anthropic(api_key=Config.ANTHROPIC_API_KEY)

    system = SYSTEM_PROMPT
    if dry_run:
        system += DRY_RUN_ADDENDUM

    messages = [
        {
            "role": "user",
            "content": (
                "Good morning! Please run my daily prospect follow-up check. "
                "Review all active prospects, check email and calendar activity, "
                "and draft follow-up emails for anyone who needs one."
            ),
        }
    ]

    # Agent loop: keep going until Claude is done
    turn = 0
    max_turns = 30  # Safety limit

    while turn < max_turns:
        turn += 1
        logger.info("Agent turn %d", turn)

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            system=system,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        logger.debug("Stop reason: %s", response.stop_reason)

        # Process the response
        assistant_content = response.content
        has_tool_use = any(block.type == "tool_use" for block in assistant_content)

        # Print any text blocks to console
        for block in assistant_content:
            if block.type == "text" and block.text.strip():
                print(block.text)

        # If Claude is done (no more tool calls), we're finished
        if response.stop_reason == "end_turn" and not has_tool_use:
            logger.info("Agent completed in %d turns", turn)
            break

        # Execute tool calls and send results back
        if has_tool_use:
            # Add assistant message to history
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute all tool calls (supports parallel tool use)
            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    logger.info("Tool call: %s(%s)", block.name, block.input)
                    result = execute_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            # Send tool results back to Claude
            messages.append({"role": "user", "content": tool_results})
        else:
            # stop_reason might be "max_tokens" — ask Claude to continue
            if response.stop_reason == "max_tokens":
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": "Please continue."})
            else:
                break

    if turn >= max_turns:
        logger.warning("Agent hit max turns limit (%d)", max_turns)


def main():
    parser = argparse.ArgumentParser(description="Prospect Follow-Up Agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze and draft emails but skip Slack/email delivery",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    validate_config()

    print("=" * 60)
    print("  PROSPECT FOLLOW-UP AGENT")
    print("=" * 60)
    if args.dry_run:
        print("  Mode: DRY RUN (no Slack or email notifications)")
    print()

    try:
        run_agent(dry_run=args.dry_run)
    except anthropic.AuthenticationError:
        logger.error("Invalid Anthropic API key. Check ANTHROPIC_API_KEY in .env")
        sys.exit(1)
    except Exception as e:
        logger.error("Agent failed: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
