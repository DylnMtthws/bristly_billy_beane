#!/bin/bash
# Install launchd plists for Sabermetrics scheduled refresh jobs (D8.6).
#
# Usage: bash scripts/install_launchd.sh
#
# This script:
# 1. Resolves the project directory and venv Python path
# 2. Substitutes placeholders in plist templates
# 3. Copies plists to ~/Library/LaunchAgents/
# 4. Loads them via launchctl
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.sabermetrics.*.plist
#   rm ~/Library/LaunchAgents/com.sabermetrics.*.plist

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python3"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
PLIST_DIR="$PROJECT_DIR/launchd"
LOG_DIR="$PROJECT_DIR/data/logs"

echo "Sabermetrics launchd installer"
echo "=============================="
echo "Project: $PROJECT_DIR"
echo "Python:  $VENV_PYTHON"
echo ""

# Verify venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv Python not found at $VENV_PYTHON"
    echo "Create it with: python3 -m venv .venv && .venv/bin/pip install -e ."
    exit 1
fi

# Create log directory
mkdir -p "$LOG_DIR"

# Create LaunchAgents directory if needed
mkdir -p "$LAUNCH_AGENTS"

# Process each plist
PLISTS=(
    "com.sabermetrics.nightly.plist"
    "com.sabermetrics.weekly.plist"
    "com.sabermetrics.monthly.plist"
    "com.sabermetrics.quarterly.plist"
)

for PLIST in "${PLISTS[@]}"; do
    SRC="$PLIST_DIR/$PLIST"
    DEST="$LAUNCH_AGENTS/$PLIST"

    if [ ! -f "$SRC" ]; then
        echo "SKIP: $PLIST (not found in $PLIST_DIR)"
        continue
    fi

    # Unload existing if present
    if launchctl list | grep -q "${PLIST%.plist}" 2>/dev/null; then
        echo "Unloading existing: $PLIST"
        launchctl unload "$DEST" 2>/dev/null || true
    fi

    # Substitute placeholders and install
    echo "Installing: $PLIST"
    sed \
        -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
        "$SRC" > "$DEST"

    # Load the plist
    launchctl load "$DEST"
    echo "  Loaded: $PLIST"
done

echo ""
echo "Done. Verify with: launchctl list | grep sabermetrics"
echo ""
echo "Schedule:"
echo "  nightly    - Daily at 2:00 AM"
echo "  weekly     - Sunday at 3:00 AM"
echo "  monthly    - 1st of month at 4:00 AM"
echo "  quarterly  - Manual trigger (launchctl start com.sabermetrics.quarterly)"
echo ""
echo "Logs: $LOG_DIR/"
echo ""
echo "To uninstall:"
echo "  launchctl unload ~/Library/LaunchAgents/com.sabermetrics.*.plist"
echo "  rm ~/Library/LaunchAgents/com.sabermetrics.*.plist"
