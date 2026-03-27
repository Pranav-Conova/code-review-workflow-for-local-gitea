# Claude PR Reviewer

Automated pull request code review for [Gitea](https://gitea.io), powered by [Claude Code](https://docs.anthropic.com/en/docs/claude-code).

Claude acts as a senior code reviewer — reads diffs with full file context, identifies bugs, security issues, and code quality problems, then posts inline comments and a structured summary directly on the PR.

No webhooks, no admin access, no inbound ports. The service polls the Gitea API and reviews new PRs automatically.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [Docker (recommended)](#docker-recommended)
  - [Local Setup](#local-setup)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Usage](#usage)
  - [Automatic Mode](#automatic-mode)
  - [Interactive Mode](#interactive-mode)
  - [Re-reviewing PRs](#re-reviewing-prs)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Features

- **Automated polling** — continuously monitors all accessible repos for new PRs
- **Full context reviews** — reads entire files (not just diffs) for accurate analysis
- **Inline comments** — posts line-by-line feedback directly on the PR
- **Structured summaries** — adds a formatted review comment with severity levels
- **WIP indicator** — prefixes PR titles with `WIP:` during review, removes it when done
- **Concurrent reviews** — processes multiple PRs in parallel (configurable)
- **Interactive mode** — optional `/review-pr` slash command for manual reviews
- **Docker-ready** — single-command deployment with `docker compose`

## Prerequisites

| Requirement | Purpose |
|---|---|
| [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) | Code analysis engine |
| [Go 1.21+](https://go.dev/dl/) | Runs the Gitea MCP server |
| [Gitea](https://gitea.io) instance | Source code host |
| Gitea access token | API authentication ([how to generate](#generating-a-gitea-access-token)) |

> Docker deployment bundles all dependencies — only Docker is required on the host.

---

## Getting Started

### Docker (recommended)

**1. Get your Claude OAuth credentials**

```bash
cat ~/.claude/.credentials.json
```

Copy `accessToken` and `refreshToken` from the `claudeAiOauth` object.

**2. Generate a Gitea access token**

Navigate to your Gitea instance > **Settings > Applications > Manage Access Tokens**. Create a token with `repo` (read/write) scope.

**3. Create a `.env` file in the project root**

```env
# Claude OAuth (from ~/.claude/.credentials.json)
CLAUDE_TOKEN=sk-ant-oat01-your-access-token
CLAUDE_REFRESH_TOKEN=sk-ant-ort01-your-refresh-token

# Gitea
GITEA_TOKEN=your-gitea-access-token
GITEA_HOST=http://localhost:3000
```

**4. Build and start**

```bash
docker compose -f docker/docker-compose.yml build
docker compose -f docker/docker-compose.yml up -d
```

**5. Verify**

```bash
docker logs -f claude-pr-reviewer
```

You should see the auth diagnostics followed by poll cycle logs.

**Stopping and restarting:**

```bash
docker compose -f docker/docker-compose.yml down
docker compose -f docker/docker-compose.yml up -d   # picks up .env changes
```

---

### Local Setup

```bash
# 1. Configure credentials
export GITEA_TOKEN='your-gitea-access-token'
export GITEA_HOST='http://localhost:3000'   # optional, defaults to localhost:3000

# 2. Run the setup script (generates .mcp.json and Claude permissions)
bash setup.sh

# 3. Create a virtual environment and install dependencies
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt

# 4. Start the auto-reviewer
python3 src/poller.py
```

Alternatively, create a `.env` file (see Docker section) and the poller will load it automatically via `python-dotenv`.

---

## Configuration

All configuration is via environment variables. When running locally, these can be set in a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `GITEA_TOKEN` | *(required)* | Gitea personal access token |
| `GITEA_HOST` | `http://localhost:3000` | Gitea instance URL |
| `CLAUDE_TOKEN` | *(required for Docker)* | Claude OAuth access token |
| `CLAUDE_REFRESH_TOKEN` | *(required for Docker)* | Claude OAuth refresh token |
| `POLL_INTERVAL` | `10` | Seconds between poll cycles |
| `MAX_CONCURRENT_REVIEWS` | `2` | Max parallel review sessions |
| `CLAUDE_BINARY` | `claude` | Path to Claude CLI binary |
| `LOG_LEVEL` | `DEBUG` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `PROJECT_DIR` | auto-detected | Project root directory |
| `DATA_DIR` | `<PROJECT_DIR>/data` | Directory for logs and state |

---

## Architecture

```
Poller (src/poller.py)
 │  polls Gitea API every N seconds
 │  discovers new open PRs across all accessible repos
 │  deduplicates against data/reviewed.json
 ▼
Review Queue
 │  concurrent worker threads pick up jobs
 │  PR title prefixed with "WIP:" during review
 ▼
Claude CLI (headless, spawned per review)
 │  reads PR diff + full file contents via Gitea MCP server
 │  analyzes code for bugs, security, quality, edge cases
 ▼
Gitea PR
    inline comments posted on specific lines
    summary comment posted with structured review
    "WIP:" prefix removed from title
```

The Gitea MCP server (`gitea-mcp`) runs as a subprocess, providing Claude with read access to repositories and write access limited to posting review comments.

---

## Project Structure

```
.
├── src/
│   ├── config.py                # Environment variable loading
│   ├── poller.py                # Main polling loop and Gitea API client
│   ├── reviewer.py              # Claude CLI subprocess management
│   └── prompt_template.py       # Review prompt builder
├── docker/
│   ├── Dockerfile               # Multi-stage build (Node + Python + Go)
│   ├── docker-compose.yml       # Service definition
│   └── entrypoint.sh            # Container init (auth + env setup)
├── .claude/
│   ├── settings.local.json      # Tool permissions (interactive mode)
│   └── skills/review-pr/        # /review-pr slash command definition
├── data/                        # Runtime data (auto-created)
│   ├── review.log               # Application logs
│   └── reviewed.json            # Tracks reviewed PR keys
├── .mcp.json                    # Gitea MCP server configuration
├── .env                         # Credentials (not committed)
├── requirements.txt             # Python dependencies
├── setup.sh                     # Local setup script
└── README.md
```

---

## Usage

### Automatic Mode

The poller runs continuously, reviewing every new PR it finds:

```bash
# Local
python3 src/poller.py

# Docker
docker compose -f docker/docker-compose.yml up -d
```

Reviews are posted automatically. Check `data/review.log` or `docker logs` for progress.

### Interactive Mode

For on-demand reviews using the Claude CLI directly:

```bash
cd /path/to/project
claude
# Type: /review-pr
```

Claude will prompt you to select a repository and PR, then perform the review interactively.

### Re-reviewing PRs

The poller tracks reviewed PRs in `data/reviewed.json` using keys like `owner/repo#number`.

```bash
# Re-review a specific PR: edit data/reviewed.json and remove its entry

# Re-review all PRs
rm data/reviewed.json

# In Docker
docker exec claude-pr-reviewer rm /app/data/reviewed.json
```

The poller picks up changes on the next cycle.

---

## Generating a Gitea Access Token

1. Log in to your Gitea instance
2. Navigate to **Settings > Applications**
3. Under **Manage Access Tokens**, enter a token name
4. Select scopes: **`repo`** (read/write) at minimum
5. Click **Generate Token** and copy the value immediately

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ERROR: Go not found` | Install Go from [go.dev/dl](https://go.dev/dl/) or set `GO_BINARY` |
| `ERROR: Set GITEA_TOKEN` | Set the env var or add it to `.env` |
| `ModuleNotFoundError: dotenv` | Run `pip install -r requirements.txt` inside a venv |
| MCP server fails to start | Verify Gitea host is reachable and token is valid |
| `go run` slow on first use | Normal — Go downloads the Gitea MCP module on first run |
| Reviews not posting | Verify the Gitea token has write access to the repository |
| Poller not finding PRs | Check `data/review.log` for API errors |
| Permission prompts in Claude | Re-run `bash setup.sh` to regenerate permissions |

---

## License

MIT
