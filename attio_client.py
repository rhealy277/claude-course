import logging
import time

import requests

from config import Config
from models import Prospect

logger = logging.getLogger(__name__)

ATTIO_HEADERS = {
    "Authorization": f"Bearer {Config.ATTIO_API_KEY}",
    "Content-Type": "application/json",
}


def _attio_request(method, path, json=None, params=None):
    """Make an Attio API request with retry on rate limit."""
    url = f"{Config.ATTIO_BASE_URL}{path}"
    for attempt in range(3):
        resp = requests.request(
            method, url, headers=ATTIO_HEADERS, json=json, params=params, timeout=30
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
            logger.warning("Attio rate limited, waiting %ds", wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Attio API rate limit exceeded after retries")


def get_active_pipeline_list_id():
    """Find the list named 'Active Pipeline' and return its ID."""
    data = _attio_request("GET", "/lists")
    for lst in data.get("data", []):
        name = lst.get("name", "") or lst.get("title", "")
        if name.lower() == "active pipeline":
            return lst["id"]["list_id"]
    raise ValueError(
        "Could not find a list named 'Active Pipeline' in Attio. "
        "Available lists: "
        + ", ".join(
            (l.get("name", "") or l.get("title", "")) for l in data.get("data", [])
        )
    )


def get_pipeline_entries(list_id):
    """Fetch all entries from the Active Pipeline, excluding Won/Lost."""
    entries = []
    offset = 0
    limit = 500

    while True:
        body = {
            "filter": {
                "$not": {
                    "$or": [
                        {"stage": "Won"},
                        {"stage": "Lost"},
                        {"stage": "Closed Won"},
                        {"stage": "Closed Lost"},
                    ]
                }
            },
            "limit": limit,
            "offset": offset,
        }
        data = _attio_request("POST", f"/lists/{list_id}/entries/query", json=body)
        batch = data.get("data", [])
        entries.extend(batch)

        if len(batch) < limit:
            break
        offset += limit

    logger.info("Fetched %d active pipeline entries from Attio", len(entries))
    return entries


def _extract_attribute_value(record_values, attr_name):
    """Extract the first value for a given attribute from record_values."""
    values = record_values.get(attr_name, [])
    if not values:
        return None
    val = values[0]
    # Handle different attribute types
    if isinstance(val, dict):
        # Email addresses
        if "email_address" in val:
            return val["email_address"]
        # Name fields (first_name + last_name)
        if "first_name" in val:
            parts = [val.get("first_name", ""), val.get("last_name", "")]
            return " ".join(p for p in parts if p).strip()
        # Domain or simple value
        if "value" in val:
            return val["value"]
        if "domain" in val:
            return val["domain"]
        # Text/number
        for key in ("text", "number", "title", "name"):
            if key in val:
                return val[key]
    return str(val) if val else None


def _extract_entry_stage(entry):
    """Extract the current stage/status from an entry's values."""
    entry_values = entry.get("entry_values", {})
    for attr_name in ("stage", "status", "pipeline_stage"):
        values = entry_values.get(attr_name, [])
        if values:
            val = values[0]
            if isinstance(val, dict):
                # Status attributes have a status object with title
                status = val.get("status", {})
                if isinstance(status, dict):
                    return status.get("title", "Unknown")
                return val.get("title", val.get("value", "Unknown"))
            return str(val)
    return "Unknown"


def get_record_details(object_type, record_id):
    """Fetch a record's details (name, email, company) from Attio."""
    data = _attio_request("GET", f"/objects/{object_type}/records/{record_id}")
    record = data.get("data", {})
    record_values = record.get("values", {})

    name = (
        _extract_attribute_value(record_values, "name")
        or _extract_attribute_value(record_values, "full_name")
        or _extract_attribute_value(record_values, "first_name")
        or ""
    )
    email = (
        _extract_attribute_value(record_values, "email_addresses")
        or _extract_attribute_value(record_values, "primary_email_address")
        or ""
    )
    company = (
        _extract_attribute_value(record_values, "company")
        or _extract_attribute_value(record_values, "company_name")
        or _extract_attribute_value(record_values, "organization")
        or ""
    )

    # For company records, the name IS the company
    if object_type in ("companies", "workspaces") and not company:
        company = name

    return name, email, company


def get_prospects():
    """Main entry point: get all active prospects from the pipeline."""
    list_id = get_active_pipeline_list_id()
    entries = get_pipeline_entries(list_id)

    prospects = []
    for entry in entries:
        entry_id = entry.get("id", {}).get("entry_id", "")
        parent_object = entry.get("parent_object", "people")
        parent_record_id = entry.get("parent_record_id", "")

        if not parent_record_id:
            logger.warning("Entry %s has no parent record, skipping", entry_id)
            continue

        try:
            name, email, company = get_record_details(parent_object, parent_record_id)
        except Exception as e:
            logger.warning("Failed to get record details for %s: %s", entry_id, e)
            continue

        if not email:
            logger.warning("No email found for %s (%s), skipping", name, entry_id)
            continue

        stage = _extract_entry_stage(entry)

        prospects.append(
            Prospect(
                name=name,
                email=email,
                company=company,
                attio_entry_id=entry_id,
                attio_record_id=parent_record_id,
                stage=stage,
            )
        )

    logger.info("Found %d prospects with email addresses", len(prospects))
    return prospects
