import os
import sys
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Attio CRM
    ATTIO_API_KEY = os.getenv("ATTIO_API_KEY", "")
    ATTIO_BASE_URL = "https://api.attio.com/v2"

    # Google OAuth
    GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "./credentials.json")
    GOOGLE_TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "./token.json")
    MY_EMAIL = os.getenv("MY_EMAIL", "")
    GOOGLE_SCOPES = [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]

    # Granola
    GRANOLA_REFRESH_TOKEN = os.getenv("GRANOLA_REFRESH_TOKEN", "")
    GRANOLA_CLIENT_ID = os.getenv("GRANOLA_CLIENT_ID", "")
    GRANOLA_TOKEN_PATH = os.getenv("GRANOLA_TOKEN_PATH", "./granola_token.json")
    GRANOLA_API_URL = "https://api.granola.ai"
    GRANOLA_AUTH_URL = "https://api.workos.com/user_management/authenticate"

    # Claude AI
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    # Slack
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

    # Schedule / preferences
    TIMEZONE = os.getenv("TIMEZONE", "America/New_York")
    MEETING_DURATION_MINUTES = int(os.getenv("MEETING_DURATION_MINUTES", "30"))
    AVAILABILITY_WINDOW_START = os.getenv("AVAILABILITY_WINDOW_START", "09:00")
    AVAILABILITY_WINDOW_END = os.getenv("AVAILABILITY_WINDOW_END", "17:00")
    BUSINESS_DAYS_THRESHOLD = int(os.getenv("BUSINESS_DAYS_THRESHOLD", "2"))


def validate_config():
    """Check that all required environment variables are set."""
    required = {
        "ATTIO_API_KEY": Config.ATTIO_API_KEY,
        "MY_EMAIL": Config.MY_EMAIL,
        "ANTHROPIC_API_KEY": Config.ANTHROPIC_API_KEY,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    # Warn about optional integrations
    if not Config.SLACK_WEBHOOK_URL:
        print("WARNING: SLACK_WEBHOOK_URL not set — Slack notifications disabled.")
    if not Config.GRANOLA_REFRESH_TOKEN:
        print("WARNING: GRANOLA_REFRESH_TOKEN not set — Granola notes disabled.")
