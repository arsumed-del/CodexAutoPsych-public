#!/bin/bash
# Install the CodexAutoPsych timer on macOS (launchd, built in, free).
#
# Running this script is the owner's authorization to let routine tasks use
# the existing ChatGPT/Codex subscription and to let review checkpoints use the
# existing Claude subscription. It never adds a paid service, API key, or
# fallback. Remove everything with scripts/uninstall_mac_scheduler.sh.
#
# Usage: bash scripts/install_mac_scheduler.sh [--every MINUTES] [--dry-run]
set -euo pipefail
shopt -s nullglob

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LAB="$REPO/.lab/research"
EVERY=30
DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --every) EVERY="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "Unknown option $1"; exit 2 ;;
  esac
done
# A dry run checks everything against a throwaway lab and changes nothing real.
(( DRY )) && LAB="$(mktemp -d)/research"
LABEL="com.codexautopsych.scheduler"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf '• %s\n' "$*"; }

# 1. A Python that is 3.11 or newer.
PY=""
for c in "${PYTHON:-}" /opt/homebrew/bin/python3 /usr/local/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.1[1-9]/bin/python3 \
         "$HOME"/llm-env/bin/python3 "$HOME"/mlx-env/bin/python3 "$(command -v python3 || true)"; do
  [[ -n "$c" && -x "$c" ]] || continue
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [[ -z "$PY" ]]; then
  say "No Python 3.11+ found. Install one (python.org installer or Homebrew), then rerun."
  exit 1
fi
say "Python: $PY ($("$PY" --version 2>&1))"

# 2. Tests must pass on this Mac before anything is scheduled.
cd "$REPO"
"$PY" -m unittest discover -s tests >/tmp/codexautopsych-tests.log 2>&1 || {
  say "Tests failed on this Mac; nothing installed. See /tmp/codexautopsych-tests.log"; exit 1; }
say "Tests passed on this Mac."

# 3. Find the CLIs with the login shell's PATH (launchd has a minimal PATH).
find_cli() {
  local found
  found="$(/bin/zsh -lic "command -v $1" 2>/dev/null | tail -1 || true)"
  [[ -x "$found" ]] || found="$(command -v "$1" || true)"
  for guess in "$HOME/.local/bin/$1" "/opt/homebrew/bin/$1" "/usr/local/bin/$1" "$HOME/.npm-global/bin/$1"; do
    [[ -x "$found" ]] && break
    found="$guess"
  done
  [[ -x "$found" ]] && printf '%s' "$found" || true
}
CODEX="$(find_cli codex)"
CLAUDE="$(find_cli claude)"
lab() { "$PY" -m codexautopsych --lab "$LAB" "$@"; }
lab init >/dev/null
if [[ -x "$CODEX" ]]; then
  lab config codex.binary "\"$CODEX\"" >/dev/null
  lab config codex.enabled true >/dev/null
  say "Codex: $CODEX — routine work enabled (ChatGPT subscription only)."
else
  lab config codex.enabled false >/dev/null
  say "Codex CLI not found — routine work will wait until it is installed and signed in."
fi
if [[ -x "$CLAUDE" ]]; then
  lab config claude.binary "\"$CLAUDE\"" >/dev/null
  METHOD="$("$CLAUDE" auth status 2>/dev/null | "$PY" -c 'import json,sys; print(json.load(sys.stdin).get("authMethod",""))' 2>/dev/null || true)"
  if [[ "$METHOD" == "claude.ai" || "$METHOD" == "oauth_token" ]]; then
    lab config claude.mode '"cli"' >/dev/null
    say "Claude Code: $CLAUDE — checkpoints reviewed automatically on your Claude subscription."
  else
    lab config claude.mode '"manual"' >/dev/null
    say "Claude Code is not signed in with a subscription (auth: ${METHOD:-none}) — checkpoints will wait for you."
  fi
else
  lab config claude.mode '"manual"' >/dev/null
  say "Claude Code not found — checkpoints will write review packets for you to give Claude in the app."
fi
lab doctor > "$LAB/doctor.json" 2>&1 || true
say "Diagnostics saved: $LAB/doctor.json"

# 4. The launchd job: one bounded pass every $EVERY minutes; overlapping passes skip.
PATHS="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"
[[ -n "$CODEX" ]] && PATHS="$(dirname "$CODEX"):$PATHS"
[[ -n "$CLAUDE" ]] && PATHS="$(dirname "$CLAUDE"):$PATHS"
mkdir -p "$LAB/logs"
PLIST_BODY="<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>-m</string><string>codexautopsych</string>
    <string>--lab</string><string>$LAB</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$PATHS</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
  <key>StartInterval</key><integer>$((EVERY * 60))</integer>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LAB/logs/run.log</string>
  <key>StandardErrorPath</key><string>$LAB/logs/run.err</string>
</dict></plist>"

if (( DRY )); then
  say "Dry run — would write $PLIST:"; printf '%s\n' "$PLIST_BODY"; exit 0
fi
mkdir -p "$HOME/Library/LaunchAgents"
printf '%s\n' "$PLIST_BODY" > "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
say "Scheduled: a pass every $EVERY minutes (also once now)."
say "Lab: $LAB — open ATTENTION.md there to see anything that needs you."
