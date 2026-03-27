#!/bin/bash
# Setup script for Claude Code Review with Gitea MCP
# Run this in the directory where you want the code review project

set -e

# --- Configuration (edit these for the target machine) ---
GITEA_HOST="${GITEA_HOST:-http://localhost:3000}"
GITEA_TOKEN="${GITEA_TOKEN:-}"
GO_BINARY="${GO_BINARY:-$(which go 2>/dev/null || echo "")}"
GOPATH_DIR="${GOPATH_DIR:-$HOME/go}"

# --- Validate ---
if [ -z "$GITEA_TOKEN" ]; then
  echo "ERROR: Set GITEA_TOKEN before running."
  echo "  export GITEA_TOKEN='your-gitea-access-token'"
  echo "  bash setup.sh"
  exit 1
fi

if [ -z "$GO_BINARY" ]; then
  echo "ERROR: Go not found. Install Go or set GO_BINARY to the path of the go binary."
  exit 1
fi

echo "Setting up Claude Code Review..."
echo "  Gitea:  $GITEA_HOST"
echo "  Go:     $GO_BINARY"
echo "  GOPATH: $GOPATH_DIR"

# --- 1. Create .mcp.json (Gitea MCP server) ---
cat > .mcp.json << MCPEOF
{
  "mcpServers": {
    "gitea": {
      "command": "$GO_BINARY",
      "args": ["run", "gitea.com/gitea/gitea-mcp@latest", "-t", "stdio"],
      "env": {
        "GITEA_HOST": "$GITEA_HOST",
        "GITEA_ACCESS_TOKEN": "$GITEA_TOKEN",
        "GOPATH": "$GOPATH_DIR",
        "GOMODCACHE": "$GOPATH_DIR/pkg/mod"
      }
    }
  }
}
MCPEOF
echo "  Created .mcp.json"

# --- 2. Create .claude/settings.local.json (permissions) ---
mkdir -p .claude
cat > .claude/settings.local.json << 'SETTINGSEOF'
{
  "permissions": {
    "allow": [
      "mcp__gitea__list_my_repos",
      "mcp__gitea__list_pull_requests",
      "mcp__gitea__pull_request_read",
      "mcp__gitea__get_file_contents",
      "mcp__gitea__get_dir_contents",
      "mcp__gitea__list_commits",
      "mcp__gitea__list_branches",
      "mcp__gitea__search_repos",
      "mcp__gitea__pull_request_review_write",
      "Bash(python:*)",
      "Bash(python3:*)",
      "Bash(grep:*)",
      "Bash(jq:*)",
      "Bash(cd:*)",
      "Bash(cat:*)",
      "Bash(head:*)",
      "Bash(tail:*)",
      "Bash(wc:*)",
      "Bash(sed:*)",
      "Bash(awk:*)"
    ]
  },
  "enabledMcpjsonServers": [
    "gitea"
  ]
}
SETTINGSEOF
echo "  Created .claude/settings.local.json"

# --- 3. Create the review-pr skill ---
mkdir -p .claude/skills/review-pr
cat > .claude/skills/review-pr/SKILL.md << 'SKILLEOF'
---
name: review-pr
description: Review pull requests from the local Gitea instance. Lists repos, PRs, reviews code with full codebase context, and posts comments.
disable-model-invocation: true
---

## Gitea PR Code Review

You are a senior code reviewer. Review pull requests thoroughly using the full codebase as context.

Tools are from the official Gitea MCP server, prefixed with `mcp__gitea__`.

### Step 1: Pick a Repository

Call `mcp__gitea__list_my_repos` with `page: 1, perPage: 50`.

Present repos as a numbered list:
```
1. owner/repo-name — description
2. owner/other-repo — description
...
```

Ask: **"Which repo do you want to review?"**

Wait for the user to pick one before continuing.

### Step 2: Pick a Pull Request

Call `mcp__gitea__list_pull_requests` with the selected `owner`, `repo`, and `state: "open"`.

If no open PRs, tell the user and stop.

Present PRs as a numbered list:
```
1. #12 — Fix login bug (by alice) [main ← feature/login-fix]
2. #15 — Add dashboard (by bob) [main ← feature/dashboard]
...
```

Ask: **"Which PR do you want to review?"**

Wait for the user to pick one before continuing.

### Step 3: Fetch the PR

Call these in parallel:
- `mcp__gitea__pull_request_read` with `method: "get"`, `owner`, `repo`, `index` — gets PR title, body, metadata
- `mcp__gitea__pull_request_read` with `method: "get_diff"`, `owner`, `repo`, `index` — gets the full diff

### Step 4: Read Full Codebase Context

From the diff, identify every file that was changed.

For each changed file, call `mcp__gitea__get_file_contents` with `owner`, `repo`, `ref` (the PR's **head branch**), `filePath`, and `withLines: true` to read the full file after the PR's changes.

Also read the base branch version of modified files (using `ref` = base branch) to understand what changed.

This gives you the complete picture — not just the diff, but the full files.

### Step 5: Review the Code

Analyze every change in the diff against the full codebase context. Look for:

- **Bugs** — wrong logic, off-by-one, null handling, race conditions
- **Security** — injection, auth issues, secrets in code, missing validation
- **Code Quality** — bad naming, duplication, dead code, complexity
- **Edge Cases** — missing error handling, boundary conditions
- **Performance** — unnecessary work, N+1 queries, memory issues
- **Missing Tests** — new behavior without test coverage

For each issue, note the **file**, **line number**, **severity** (critical/warning/suggestion/nitpick), and a clear explanation with fix suggestion.

### Step 6: Present Review and Post to Gitea

Show the full review to the user.

Then **automatically post both inline comments AND a summary comment** — do not ask the user how to post.

**Inline comments (post first):**
Call `mcp__gitea__pull_request_review_write` with:
- `method: "create"`
- `owner`, `repo`, `index`
- `state: "COMMENT"`
- `commit_id`: the head commit SHA from the PR
- `body`: short summary (e.g., "Code review — found X issues")
- `comments`: array where each item has:
  - `path`: file path
  - `new_line_num`: line number in the new file
  - `body`: the review comment for that line

**Summary comment (post second):**
Call `mcp__gitea__pull_request_review_write` with:
- `method: "create"`
- `owner`, `repo`, `index`
- `state: "COMMENT"`
- `commit_id`: the head commit SHA from the PR
- `body`: the full review markdown (use format below)

### Review Format

```markdown
## Code Review — PR #{{number}}: {{title}}

### Summary
{{1-2 sentence overall assessment}}

### Issues

#### Critical
- **{{file}}:{{line}}** — {{description}}

#### Warnings
- **{{file}}:{{line}}** — {{description}}

#### Suggestions
- **{{file}}:{{line}}** — {{description}}

### What Looks Good
{{positive observations}}

---
*Reviewed by Claude Code*
```
SKILLEOF
echo "  Created .claude/skills/review-pr/SKILL.md"

echo ""
echo "Done! To use:"
echo "  1. cd into this directory"
echo "  2. Run: claude"
echo "  3. Type: /review-pr"
