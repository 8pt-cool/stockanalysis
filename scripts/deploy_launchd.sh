#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
APP_SUPPORT_DIR="$HOME/Library/Application Support/StockAnalysis"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_NAME="com.charleszhang.stockanalysis.plist"
PLIST_PATH="$LAUNCH_AGENTS_DIR/$PLIST_NAME"
LABEL="com.charleszhang.stockanalysis"
USER_ID="$(id -u)"

echo "Deploying app to: $APP_SUPPORT_DIR"
mkdir -p "$APP_SUPPORT_DIR"
mkdir -p "$APP_SUPPORT_DIR/data/logs"
mkdir -p "$LAUNCH_AGENTS_DIR"

rsync -a --delete \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '.env' \
  --exclude 'data' \
  "$PROJECT_DIR/" \
  "$APP_SUPPORT_DIR/"

if [[ ! -f "$APP_SUPPORT_DIR/.env" && -f "$PROJECT_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env" "$APP_SUPPORT_DIR/.env"
fi

cp "$PROJECT_DIR/scripts/$PLIST_NAME" "$PLIST_PATH"

if launchctl print "gui/$USER_ID/$LABEL" >/dev/null 2>&1; then
  echo "Reloading existing launchd job: $LABEL"
  launchctl bootout "gui/$USER_ID" "$PLIST_PATH" || true
fi

echo "Loading launchd job: $LABEL"
launchctl bootstrap "gui/$USER_ID" "$PLIST_PATH"
launchctl kickstart -k "gui/$USER_ID/$LABEL"

echo "Health check:"
sleep 1
curl -s "http://127.0.0.1:8765/api/health" || true
echo
echo "Done."
