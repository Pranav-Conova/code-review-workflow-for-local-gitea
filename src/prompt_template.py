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


def build_batch_review_prompt(jobs: list) -> str:
    """Build a prompt to review multiple PRs together for integration issues."""
    pr_details = ""
    for i, job in enumerate(jobs, 1):
        body = job.get("body") or "No description."
        pr_details += f"""
### PR {i}: {job['owner']}/{job['repo_name']}#{job['number']}
- **Title:** {job['title']}
- **Author:** {job['sender']}
- **Branch:** {job['base_branch']} <- {job['head_branch']}
- **Head SHA:** {job['head_sha']}
- **Description:** {body}
"""

    fetch_steps = ""
    read_steps = ""
    for i, job in enumerate(jobs, 1):
        fetch_steps += f"""
- `mcp__gitea__pull_request_read` with method="get", owner="{job['owner']}", repo="{job['repo_name']}", index={job['number']}
- `mcp__gitea__pull_request_read` with method="get_diff", owner="{job['owner']}", repo="{job['repo_name']}", index={job['number']}"""
        read_steps += f"""
For PR {i} ({job['owner']}/{job['repo_name']}#{job['number']}):
- HEAD version: `mcp__gitea__get_file_contents` with owner="{job['owner']}", repo="{job['repo_name']}", ref="{job['head_branch']}", filePath=<path>
- BASE version: `mcp__gitea__get_file_contents` with owner="{job['owner']}", repo="{job['repo_name']}", ref="{job['base_branch']}", filePath=<path>"""

    pr_list = ", ".join(f"{j['owner']}/{j['repo_name']}#{j['number']}" for j in jobs)

    return f"""You are a senior code reviewer specializing in cross-repository integration analysis. You are reviewing MULTIPLE pull requests together to find integration issues, conflicts, and cross-cutting concerns.

## PRs Under Review
{pr_details}

## Instructions

Execute these steps in order. Do NOT ask questions — this is fully automated.

### Step 1: Fetch all PR diffs and metadata

Call in parallel:
{fetch_steps}

### Step 2: Read full file context for all PRs

From each diff, identify every changed file and read the full context:
{read_steps}

### Step 3: Cross-PR Integration Analysis

Analyze ALL the PRs together. Focus specifically on:

- **API Contract Mismatches** — Does one PR change an API that another PR consumes? Mismatched request/response schemas, renamed endpoints, changed auth requirements
- **Shared Dependency Conflicts** — Both PRs modifying the same dependency versions, conflicting package updates
- **Data Model Inconsistencies** — One PR changes a DB schema/model that another PR reads from
- **Configuration Conflicts** — Environment variables, feature flags, config files modified in conflicting ways
- **Shared Utility Changes** — Both PRs modifying or depending on the same utility functions/classes
- **Deployment Order Issues** — Would deploying these in the wrong order break things?
- **Type/Interface Mismatches** — Changed types in one repo that are used in another
- **Business Logic Contradictions** — Changes that implement conflicting business rules

Also perform standard per-PR code review (bugs, security, quality).

### Step 4: Post the review to Gitea

Get the head commit SHA from each PR's metadata.

**CRITICAL FORMATTING RULE:** Use actual newline characters in body text, NOT literal `\\n`.

For EACH PR, post a review comment using `mcp__gitea__pull_request_review_write` with:
- method: "create"
- owner, repo, index for that specific PR
- state: "COMMENT"
- commit_id: that PR's head commit SHA
- body: A markdown report including both:
  1. Integration issues found across all PRs
  2. Issues specific to this PR

Format the body as:

## Cross-PR Integration Review

### PRs Analyzed Together
{pr_list}

### Integration Issues Found
(List cross-cutting issues with severity)

### Per-PR Issues
(Standard code review findings for this specific PR)

### Deployment Notes
(Any ordering or coordination needed)

---
*Automated cross-PR integration review by Claude Code*

Do NOT output anything to stdout besides confirming you completed the review."""


def build_codebase_review_prompt(repos: list) -> str:
    """Build a prompt to review full codebases together. Results returned as text, NOT posted to Gitea."""
    repo_details = ""
    for i, repo in enumerate(repos, 1):
        repo_details += f"- **Repo {i}:** {repo['owner']}/{repo['repo']}\n"

    read_steps = ""
    for repo in repos:
        read_steps += f"""
For {repo['owner']}/{repo['repo']}:
1. `mcp__gitea__get_dir_contents` with owner="{repo['owner']}", repo="{repo['repo']}", path="" to get root listing
2. Read key directories (src/, lib/, app/, etc.) to understand structure
3. Read important files: package.json, requirements.txt, go.mod, Dockerfile, config files, main entry points
4. Read core source files to understand architecture, patterns, and business logic"""

    return f"""You are a senior software architect performing a comprehensive cross-repository codebase analysis. You are reviewing MULTIPLE codebases together to find systemic issues, architectural problems, and integration concerns.

## Repositories Under Review
{repo_details}

## Instructions

Execute these steps in order. Do NOT ask questions — this is fully automated.

### Step 1: Explore repository structures

For each repository, understand the codebase layout:
{read_steps}

### Step 2: Deep code analysis

Read through the core source files of each repository. Understand:
- Architecture and design patterns used
- API endpoints and contracts
- Data models and database schemas
- Shared types, interfaces, and DTOs
- Authentication and authorization flow
- Error handling patterns
- Configuration management

### Step 3: Cross-Repository Analysis

Analyze both codebases together. Report on:

**Architecture & Design:**
- Architectural consistency across repos (or lack thereof)
- Shared patterns vs divergent approaches
- Dependency management consistency

**Integration Points:**
- API contract alignment (request/response schemas match between consumer and provider)
- Shared data models — are they consistent?
- Authentication/authorization flow across services
- Event/message contracts if applicable

**Code Quality Issues:**
- Security vulnerabilities (injection, auth bypass, secrets in code, missing validation)
- Performance concerns (N+1 queries, missing pagination, unbounded operations)
- Error handling gaps
- Missing input validation at boundaries
- Dead code or unused dependencies
- Code duplication across repos

**Technical Debt:**
- Outdated dependencies
- Deprecated API usage
- Missing tests for critical paths
- Hard-coded values that should be configurable
- Missing documentation for complex flows

**Recommendations:**
- Priority-ordered list of improvements
- Quick wins vs long-term refactors
- Suggested shared libraries or patterns

### Step 4: Output the full report

Output your complete analysis as a well-structured markdown report to stdout. This is CRITICAL — your entire analysis must be printed to stdout as the output.

**IMPORTANT: Do NOT call any write tools. Do NOT post any comments to Gitea. Do NOT create any issues, PRs, or reviews. Your job is ONLY to analyze and output the report to stdout.**

Format the report as:

# Cross-Repository Codebase Review

## Repositories
(list repos analyzed)

## Executive Summary
(2-3 sentence high-level assessment)

## Architecture Overview
(per-repo architecture + how they connect)

## Integration Issues
### Critical
### Warnings
### Suggestions

## Per-Repository Issues
### Repo 1: owner/name
#### Security
#### Performance
#### Code Quality
#### Technical Debt

### Repo 2: owner/name
(same structure)

## Recommendations
(priority-ordered action items)

---
*Automated codebase review by Claude Code*"""
