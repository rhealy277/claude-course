#!/bin/bash
# ==============================================
# Install cron job for Prospect Follow-Up Tracker
# Runs daily at 9:00 AM Eastern, Monday-Friday
# ==============================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LOG_FILE="${LOG_FILE:-/tmp/prospect-tracker.log}"

# Verify python is available
if ! command -v "$PYTHON_BIN" &> /dev/null; then
    echo "ERROR: $PYTHON_BIN not found. Set PYTHON_BIN to your Python path."
    exit 1
fi

# Check for virtual environment
if [ -d "$SCRIPT_DIR/venv" ]; then
    PYTHON_BIN="$SCRIPT_DIR/venv/bin/python3"
    echo "Found virtual environment, using: $PYTHON_BIN"
fi

# Build the cron command
# 9 AM ET = cron handles via TZ variable
CRON_CMD="0 9 * * 1-5 cd $SCRIPT_DIR && TZ=America/New_York $PYTHON_BIN agent.py >> $LOG_FILE 2>&1"

# Check if cron job already exists
if crontab -l 2>/dev/null | grep -q "prospect-tracker\|agent.py.*prospect"; then
    echo "A prospect tracker cron job already exists:"
    crontab -l | grep "prospect-tracker\|agent.py"
    echo ""
    read -p "Replace it? (y/N): " response
    if [[ "$response" != "y" && "$response" != "Y" ]]; then
        echo "Keeping existing cron job."
        exit 0
    fi
    # Remove existing entry
    crontab -l 2>/dev/null | grep -v "prospect-tracker\|agent.py" | crontab -
fi

# Install the cron job
(crontab -l 2>/dev/null; echo "# prospect-tracker: daily follow-up check"; echo "$CRON_CMD") | crontab -

echo "Cron job installed successfully!"
echo ""
echo "Schedule: Monday-Friday at 9:00 AM Eastern"
echo "Command:  $PYTHON_BIN agent.py"
echo "Log file: $LOG_FILE"
echo ""
echo "Verify with: crontab -l"
echo "Remove with: crontab -l | grep -v prospect-tracker | crontab -"
