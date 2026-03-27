#!/bin/bash
set -e

# --- Validate env vars ---
if [ -z "$GITEA_TOKEN" ]; then
    echo "ERROR: GITEA_TOKEN env var is required"
    exit 1
fi

if [ -z "$CLAUDE_TOKEN" ]; then
    echo "ERROR: CLAUDE_TOKEN env var is required"
    echo "  Get it from: cat ~/.claude/.credentials.json"
    exit 1
fi

if [ -z "$CLAUDE_REFRESH_TOKEN" ]; then
    echo "ERROR: CLAUDE_REFRESH_TOKEN env var is required"
    echo "  Get it from: cat ~/.claude/.credentials.json"
    exit 1
fi

GITEA_HOST="${GITEA_HOST:-http://localhost:3000}"

# --- Generate .mcp.json with actual values ---
sed "s|__GITEA_HOST__|$GITEA_HOST|g; s|__GITEA_TOKEN__|$GITEA_TOKEN|g" \
    /app/.mcp.json.template > /app/.mcp.json

# --- Write Claude credentials for non-root user ---
mkdir -p /home/reviewer/.claude
cat > /home/reviewer/.claude/.credentials.json << EOF
{
  "claudeAiOauth": {
    "accessToken": "$CLAUDE_TOKEN",
    "refreshToken": "$CLAUDE_REFRESH_TOKEN",
    "expiresAt": 4102444800000,
    "scopes": [
      "user:file_upload",
      "user:inference",
      "user:mcp_servers",
      "user:profile",
      "user:sessions:claude_code"
    ],
    "subscriptionType": "max",
    "rateLimitTier": "default_claude_max_5x"
  }
}
EOF
chmod 600 /home/reviewer/.claude/.credentials.json
chown -R reviewer:reviewer /home/reviewer/.claude
chown -R reviewer:reviewer /app

# --- Diagnostics ---
echo "=== Claude PR Reviewer ==="
echo "  Gitea: $GITEA_HOST"
echo "  Poll interval: ${POLL_INTERVAL:-10}s"
echo "  Max concurrent: ${MAX_CONCURRENT_REVIEWS:-2}"
echo ""
echo "--- Auth diagnostics ---"
echo "  Credentials file: /home/reviewer/.claude/.credentials.json"
ls -la /home/reviewer/.claude/.credentials.json
echo "  Contents (redacted):"
cat /home/reviewer/.claude/.credentials.json | sed 's/sk-ant-[^"]*/.../g'
echo ""
echo "  Testing Claude CLI as reviewer user..."
su -s /bin/bash reviewer -c "HOME=/home/reviewer PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin claude --version" || echo "  WARNING: claude --version failed"
echo "---"
echo ""

cd /app
exec su -s /bin/bash reviewer -c "\
  export HOME=/home/reviewer && \
  export PATH=/usr/local/go/bin:/home/reviewer/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin && \
  export GOPATH=/home/reviewer/go && \
  export GOMODCACHE=/home/reviewer/go/pkg/mod && \
  export CLAUDE_TOKEN='$CLAUDE_TOKEN' && \
  export GITEA_TOKEN='$GITEA_TOKEN' && \
  export GITEA_HOST='$GITEA_HOST' && \
  export POLL_INTERVAL='${POLL_INTERVAL:-10}' && \
  export MAX_CONCURRENT_REVIEWS='${MAX_CONCURRENT_REVIEWS:-2}' && \
  export DASHBOARD_PORT='${DASHBOARD_PORT:-8000}' && \
  python3 src/app.py"
