import json
import logging
import os
import tempfile
from datetime import datetime, timezone

import requests

from config import Config
from models import MeetingNotes

logger = logging.getLogger(__name__)

GRANOLA_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Granola/5.354.0",
    "X-Client-Version": "5.354.0",
}


class GranolaClient:
    """Client for the Granola meeting notes API with token rotation."""

    def __init__(self):
        self.access_token = None
        self.refresh_token = None
        self.client_id = Config.GRANOLA_CLIENT_ID
        self.token_path = Config.GRANOLA_TOKEN_PATH
        self._load_token()

    def _load_token(self):
        """Load the refresh token from file or env var."""
        if os.path.exists(self.token_path):
            with open(self.token_path) as f:
                data = json.load(f)
                self.refresh_token = data.get("refresh_token", "")
                self.access_token = data.get("access_token", "")
        elif Config.GRANOLA_REFRESH_TOKEN:
            self.refresh_token = Config.GRANOLA_REFRESH_TOKEN
        else:
            logger.warning("No Granola refresh token available")

    def _save_token(self):
        """Atomically save the current tokens to disk."""
        data = {
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
        }
        # Atomic write: write to temp file, then rename
        dir_name = os.path.dirname(os.path.abspath(self.token_path))
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.token_path)
        except Exception:
            os.unlink(tmp_path)
            raise

    def _refresh_access_token(self):
        """Exchange the refresh token for a new access token. Tokens rotate."""
        if not self.refresh_token:
            raise RuntimeError("No Granola refresh token available")
        if not self.client_id:
            raise RuntimeError("No Granola client_id configured")

        resp = requests.post(
            Config.GRANOLA_AUTH_URL,
            json={
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]  # Rotated! Must save
        self._save_token()
        logger.info("Granola access token refreshed successfully")

    def _ensure_auth(self):
        """Make sure we have a valid access token."""
        if not self.access_token:
            self._refresh_access_token()

    def _request(self, method, path, json_body=None):
        """Make an authenticated Granola API request."""
        self._ensure_auth()
        url = f"{Config.GRANOLA_API_URL}{path}"
        headers = {
            **GRANOLA_HEADERS,
            "Authorization": f"Bearer {self.access_token}",
        }
        resp = requests.request(
            method, url, headers=headers, json=json_body, timeout=30
        )

        # If unauthorized, try refreshing once
        if resp.status_code == 401:
            self._refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            resp = requests.request(
                method, url, headers=headers, json=json_body, timeout=30
            )

        resp.raise_for_status()
        return resp.json()

    def get_recent_documents(self, limit=100):
        """Fetch recent documents from Granola."""
        data = self._request("POST", "/v2/get-documents", {
            "limit": limit,
            "offset": 0,
            "include_last_viewed_panel": True,
        })
        return data if isinstance(data, list) else data.get("data", data.get("documents", []))

    def search_notes_for_prospect(self, prospect):
        """Search Granola documents by prospect name and company."""
        documents = self.get_recent_documents(limit=100)

        # Search keywords in order of specificity
        keywords = [prospect.name]
        if prospect.company:
            keywords.append(prospect.company)
        # Also try last name only
        name_parts = prospect.name.split()
        if len(name_parts) > 1:
            keywords.append(name_parts[-1])

        for keyword in keywords:
            keyword_lower = keyword.lower()
            matches = [
                doc for doc in documents
                if keyword_lower in (doc.get("title", "") or "").lower()
            ]
            if matches:
                # Return the most recent match
                matches.sort(
                    key=lambda d: d.get("updated_at", d.get("created_at", "")),
                    reverse=True,
                )
                return self._doc_to_meeting_notes(matches[0])

        logger.info("No Granola notes found for %s", prospect.name)
        return None

    def _doc_to_meeting_notes(self, doc):
        """Convert a Granola document to a MeetingNotes object."""
        title = doc.get("title", "Untitled")
        created = doc.get("created_at", "")
        try:
            date = datetime.fromisoformat(created.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            date = datetime.now(timezone.utc)

        # Convert ProseMirror content to plain text
        panel = doc.get("last_viewed_panel", {})
        content = panel.get("content", panel) if isinstance(panel, dict) else {}
        raw_text = prosemirror_to_text(content)

        if not raw_text.strip():
            # Try getting the notes directly if available
            raw_text = doc.get("notes", "") or doc.get("content_text", "") or ""

        return MeetingNotes(
            title=title,
            date=date,
            key_points=[],  # Will be populated by email_generator
            raw_text=raw_text,
        )


def prosemirror_to_text(node):
    """Recursively convert ProseMirror JSON to plain text."""
    if not node or not isinstance(node, dict):
        return ""

    node_type = node.get("type", "")

    # Leaf text node
    if node_type == "text":
        return node.get("text", "")

    # Hard break
    if node_type == "hard_break":
        return "\n"

    # Recurse into children
    children = node.get("content", [])
    parts = [prosemirror_to_text(child) for child in children]
    result = "".join(parts)

    # Block-level formatting
    if node_type in ("paragraph", "heading"):
        result = result.strip() + "\n\n"
    elif node_type == "list_item":
        result = "- " + result.strip() + "\n"
    elif node_type == "blockquote":
        lines = result.strip().split("\n")
        result = "\n".join(f"> {line}" for line in lines) + "\n\n"

    return result
