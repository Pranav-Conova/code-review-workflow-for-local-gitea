# Claude Code Review with Gitea

Automated PR code review powered by Claude Code and a self-hosted Gitea instance. Claude acts as a senior code reviewer — reads the full codebase context, identifies issues, and posts inline comments and a summary directly on the PR.

## Prerequisites

- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- [Go](https://go.dev/dl/) installed and in PATH
- A Gitea instance with an access token

## Quick Start

### Manual Mode (interactive)

```bash
# Set up the project
export GITEA_TOKEN='your-gitea-access-token'
bash setup.sh

# Start reviewing
claude
# Then type: /review-pr
```

### Automatic Mode (polls for new PRs)

```bash
# Set up the project first
export GITEA_TOKEN='your-gitea-access-token'
bash setup.sh

# Start the auto-reviewer
python3 src/poller.py
```

The poller checks all your repos every 10 seconds. When a new open PR is found, Claude automatically reviews it and posts comments to Gitea.

## How Automatic Mode Works

```
src/poller.py (every 10s)
  │  fetches all repos → fetches open PRs in each
  │  compares against data/reviewed.json
  ▼
New PR detected
  │  adds "WIP: " prefix to PR title
  │  queues review job
  ▼
Worker thread
  │  spawns: claude --print --dangerously-skip-permissions ...
  ▼
Claude CLI (headless)
  │  reads diff + full files via Gitea MCP
  │  posts inline comments + summary to Gitea PR
  ▼
Done — removes "WIP: " prefix, PR key saved to data/reviewed.json
```

No webhook setup, no admin access, no inbound ports needed. Just polls the Gitea API.

## Configuration

### setup.sh (project setup)

| Variable | Default | Description |
|---|---|---|
| `GITEA_TOKEN` | *(required)* | Gitea personal access token |
| `GITEA_HOST` | `http://localhost:3000` | Gitea instance URL |
| `GO_BINARY` | auto-detected via `which go` | Path to Go binary |
| `GOPATH_DIR` | `$HOME/go` | Go workspace path |

### poller.py (auto-reviewer)

| Variable | Default | Description |
|---|---|---|
| `GITEA_TOKEN` | *(required)* | Gitea personal access token |
| `GITEA_HOST` | `http://localhost:3000` | Gitea instance URL |
| `POLL_INTERVAL` | `10` | Seconds between poll cycles |
| `MAX_CONCURRENT_REVIEWS` | `2` | Max parallel Claude review sessions |

Example:

```bash
GITEA_HOST='https://gitea.example.com' \
GITEA_TOKEN='abc123' \
POLL_INTERVAL=30 \
MAX_CONCURRENT_REVIEWS=3 \
python3 src/poller.py
```

## Project Structure

```
.
├── src/                               # Application source
│   ├── config.py                      # Configuration (env vars)
│   ├── poller.py                      # Main polling loop
│   ├── reviewer.py                    # Claude CLI invocation
│   └── prompt_template.py             # Review prompt builder
├── docker/                            # Docker deployment
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── entrypoint.sh
├── .claude/                           # Claude CLI config
│   ├── settings.local.json            # Tool permissions (manual mode)
│   └── skills/review-pr/SKILL.md      # /review-pr slash command
├── data/                              # Runtime data (auto-created)
│   ├── review.log
│   └── reviewed.json
├── .mcp.json                          # Gitea MCP server connection
├── setup.sh                           # First-time setup script
└── README.md
```

## Docker Deployment

### 1. Get your Claude OAuth credentials

Open your Claude credentials file:

```bash
cat ~/.claude/.credentials.json
```

You need two values from the `claudeAiOauth` object:
- `accessToken` — the OAuth access token (starts with `sk-ant-oat01-...`)
- `refreshToken` — the OAuth refresh token

### 2. Get your Gitea access token

See [Generating a Gitea Access Token](#generating-a-gitea-access-token) below.

### 3. Create a `.env` file

In the project root, create a `.env` file with your credentials:

```bash
# Claude OAuth (from ~/.claude/.credentials.json)
CLAUDE_TOKEN=sk-ant-oat01-your-access-token-here
CLAUDE_REFRESH_TOKEN=your-refresh-token-here

# Gitea
GITEA_TOKEN=your-gitea-access-token
GITEA_HOST=http://localhost:3000

# Optional
POLL_INTERVAL=10
MAX_CONCURRENT_REVIEWS=2
```

### 4. Build and run

```bash
# From the project root
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
```

### 5. Check logs

```bash
docker logs -f claude-pr-reviewer
```

### Stopping / restarting

```bash
# Stop
docker compose -f docker/docker-compose.yml down

# Restart (picks up .env changes)
docker compose -f docker/docker-compose.yml up -d
```

### Re-reviewing PRs in Docker

```bash
# Exec into the container and clear the reviewed list
docker exec claude-pr-reviewer rm /app/data/reviewed.json
```

---

## Generating a Gitea Access Token

1. Log in to your Gitea instance
2. Go to **Settings > Applications**
3. Under **Manage Access Tokens**, enter a token name
4. Select scopes: `repo` (read/write) at minimum
5. Click **Generate Token** and copy it

## Manual Mode Usage

`cd` into the project directory, run `claude`, then type `/review-pr`:

1. Select a repository from the list
2. Select an open PR to review
3. Claude reviews the code and posts comments directly to Gitea

## Re-reviewing a PR

The poller tracks reviewed PRs in `data/reviewed.json`. To re-review a PR:

```bash
# Edit reviewed.json and remove the entry, e.g. "owner/repo#5"
# The poller will pick it up on the next cycle
```

To reset and re-review everything:

```bash
rm data/reviewed.json
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `ERROR: Go not found` | Install Go from https://go.dev/dl/ or set `GO_BINARY` |
| `ERROR: Set GITEA_TOKEN` | Export your token: `export GITEA_TOKEN='...'` |
| MCP server fails to start | Ensure your Gitea host is reachable and the token is valid |
| Permission prompts in Claude | Re-run `setup.sh` to regenerate `settings.local.json` |
| `go run` is slow on first use | Normal — Go downloads the Gitea MCP module on first run. Subsequent runs are cached |
| Reviews not posting to Gitea | Verify the Gitea token has write access to the repo |
| Poller not finding PRs | Check `data/review.log` for API errors. Verify token has repo access. |
| Want to re-review a PR | Remove the PR key from `data/reviewed.json` |
