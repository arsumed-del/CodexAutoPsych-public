#!/bin/bash
# Stop the CodexAutoPsych timer. Queue, outputs and history stay in .lab/.
set -euo pipefail
PLIST="$HOME/Library/LaunchAgents/com.codexautopsych.scheduler.plist"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
printf 'Timer removed. Lab state was left untouched.\n'
