def build_review_prompt(job: dict) -> str:
    body = job.get("body") or "No description provided."
    return f"""You are a senior code reviewer. Review PR #{job['number']} in repository {job['owner']}/{job['repo_name']}.

## PR Details
- **Title:** {job['title']}
- **Author:** {job['sender']}
- **Branch:** {job['base_branch']} <- {job['head_branch']}
- **Head SHA:** {job['head_sha']}
- **Description:** {body}

## Instructions

Execute these steps in order. Do NOT ask questions — this is fully automated.

### Step 1: Fetch the PR diff and metadata

Call in parallel:
- `mcp__gitea__pull_request_read` with method="get", owner="{job['owner']}", repo="{job['repo_name']}", index={job['number']}
- `mcp__gitea__pull_request_read` with method="get_diff", owner="{job['owner']}", repo="{job['repo_name']}", index={job['number']}

### Step 2: Read full file context

From the diff, identify every changed file. For each:
- HEAD version: `mcp__gitea__get_file_contents` with owner="{job['owner']}", repo="{job['repo_name']}", ref="{job['head_branch']}", filePath=<path>, withLines=true
- BASE version: `mcp__gitea__get_file_contents` with owner="{job['owner']}", repo="{job['repo_name']}", ref="{job['base_branch']}", filePath=<path>, withLines=true

Skip base for new files. Skip head for deleted files.

### Step 3: Analyze the code

Review every change against full file context. Look for:
- **Bugs** — wrong logic, off-by-one, null handling, race conditions
- **Security** — injection, auth issues, secrets in code, missing validation
- **Code Quality** — bad naming, duplication, dead code, complexity
- **Edge Cases** — missing error handling, boundary conditions
- **Performance** — unnecessary work, N+1 queries, memory issues
- **Missing Tests** — new behavior without test coverage

Additionally, suggest **modern best practices and improvements**:
- **Modern Language Features** — suggest newer syntax, built-in methods, or language features that make the code cleaner (e.g. optional chaining, nullish coalescing, pattern matching, f-strings, list comprehensions, async/await, structured concurrency)
- **Better Patterns** — recommend widely-adopted patterns like early returns over deep nesting, guard clauses, const-by-default, immutability, builder pattern, dependency injection where appropriate
- **Cleaner Alternatives** — point out when standard library or framework utilities can replace hand-rolled code (e.g. `Objects.requireNonNull` instead of manual null checks, `Array.from` instead of loops, `pathlib` instead of `os.path`)
- **Type Safety** — suggest adding types, stricter types, or leveraging type narrowing where it would prevent bugs
- **Error Handling** — recommend custom error types, result patterns, or structured error handling over generic try/catch
- **API Design** — flag inconsistent naming, suggest more descriptive method/variable names, recommend parameter objects over long parameter lists
- **Readability** — suggest destructuring, meaningful constants instead of magic numbers, extracting complex conditionals into named booleans

For each item note: file, line number, severity (critical/warning/suggestion/nitpick), explanation, and a concrete code example showing the improvement.

### Step 4: Post the review to Gitea

Get the head commit SHA from the PR metadata (step 1).

**CRITICAL FORMATTING RULE:** When composing the `body` parameter for review comments, you MUST use actual newline characters to separate lines — NOT literal backslash-n (`\n`) text. Write the markdown body as a properly formatted multi-line string with real line breaks. The body should render as readable markdown on Gitea, not as a single line with visible `\n` characters.

**First — inline comments:**
Call `mcp__gitea__pull_request_review_write` with:
- method: "create"
- owner: "{job['owner']}"
- repo: "{job['repo_name']}"
- index: {job['number']}
- state: "COMMENT"
- commit_id: the head commit SHA
- body: "Automated code review — found N issues"
- comments: array of objects with path, new_line_num, body (each comment body must also use real newlines)

**Second — summary comment:**
Call `mcp__gitea__pull_request_review_write` with:
- method: "create"
- owner: "{job['owner']}"
- repo: "{job['repo_name']}"
- index: {job['number']}
- state: "COMMENT"
- commit_id: the head commit SHA
- body: full review as properly formatted markdown (with real line breaks, NOT `\n` literals) in this format:

## Code Review — PR #{job['number']}: {job['title']}

### Summary
(1-2 sentence overall assessment)

### Issues

#### Critical
- **file:line** — description

#### Warnings
- **file:line** — description

#### Suggestions
- **file:line** — description

### Modern Improvements
Recommend newer conventions, patterns, or language features that would make the code cleaner, safer, or more idiomatic. Include a short code snippet showing the before/after for each suggestion.

- **file:line** — description
  ```
  // before
  old code
  // after
  improved code
  ```

### What Looks Good
(positive observations)

---
*Automated review by Claude Code*

If no issues found, post summary saying the code looks good.
Do NOT output anything to stdout besides confirming you completed the review."""
