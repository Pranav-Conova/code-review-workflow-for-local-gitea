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

Then **automatically post line-by-line inline comments AND a summary comment** — do not ask the user how to post. **Inline comments are the priority.**

**Line-by-line inline comments (post first):**

Post each inline comment as a **separate** review call — one comment per call. Do NOT batch multiple inline comments into a single API call (Gitea's API may reject batched inline comments).

For each issue found, call `mcp__gitea__pull_request_review_write` with:
- `method: "create"`
- `owner`, `repo`, `index`
- `state: "COMMENT"`
- `commit_id`: the head commit SHA from the PR
- `body`: short label (e.g., "Critical", "Warning", "Suggestion")
- `comments`: array with **exactly one** item:
  - `path`: file path
  - `new_line_num`: line number in the **new file** — **MUST be within a diff hunk** (a line that appears in the diff's `@@` range for that file). If the exact line is outside a hunk, use the nearest changed line within the same hunk.
  - `body`: the review comment — include severity, clear description, and fix suggestion. Keep plain text (avoid markdown code blocks with backtick fences in inline comments as they may cause API issues).

**Important constraints for inline comments:**
- `new_line_num` must fall within a diff hunk range. Lines outside the diff will cause the API to error.
- Post comments in parallel where possible to speed up the process.
- If a comment fails, retry with an adjusted line number (nearest line within the diff hunk).
- Every issue found in the review MUST have a corresponding inline comment — no issue should only appear in the summary.

**Summary comment (post last):**
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
