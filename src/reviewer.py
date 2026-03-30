import subprocess
import logging
import os
import time
from config import PROJECT_DIR, CLAUDE_BINARY
from prompt_template import build_review_prompt, build_batch_review_prompt, build_codebase_review_prompt

logger = logging.getLogger("pr-review")

MCP_CONFIG_PATH = os.path.join(PROJECT_DIR, ".mcp.json")


def run_review(job: dict) -> dict:
    """Run a Claude review for a PR. Returns {"success": bool, "duration": float, "error": str|None}."""
    start = time.time()
    pr_id = f"{job['repo']}#{job['number']}"
    logger.info(f"[{pr_id}] Building review prompt...")
    prompt = build_review_prompt(job)
    logger.debug(f"[{pr_id}] System prompt:\n{prompt[:1000]}...")

    # Only allow read + review tools. Blocks all write tools that could
    # modify code: create_or_update_file, delete_file, create_branch,
    # pull_request_write, issue_write, create_tag, create_release, etc.
    allowed_tools = ",".join([
        # Read PR data
        "mcp__gitea__pull_request_read",
        # Read file contents
        "mcp__gitea__get_file_contents",
        "mcp__gitea__get_dir_contents",
        # Read repo metadata
        "mcp__gitea__list_my_repos",
        "mcp__gitea__list_pull_requests",
        "mcp__gitea__list_commits",
        "mcp__gitea__list_branches",
        "mcp__gitea__search_repos",
        # Post review comments (the only write action allowed)
        "mcp__gitea__pull_request_review_write",
    ])

    logger.info(f"[{pr_id}] Allowed tools: {allowed_tools}")

    cmd = [
        CLAUDE_BINARY,
        "--print",
        "--no-session-persistence",
        "--mcp-config", MCP_CONFIG_PATH,
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--allowed-tools", allowed_tools,
        "--output-format", "text",
        "--disable-slash-commands",
        "--system-prompt", prompt,
        f"Review PR #{job['number']} on {job['owner']}/{job['repo_name']} now.",
    ]

    # Build env for Claude CLI subprocess
    env = os.environ.copy()
    env["HOME"] = os.path.expanduser("~")
    # Remove ANTHROPIC_API_KEY — the sk-ant-oat01- token is OAuth, not an API key
    env.pop("ANTHROPIC_API_KEY", None)

    logger.info(f"[{pr_id}] Spawning Claude CLI...")
    logger.debug(f"[{pr_id}] Command: {' '.join(cmd[:8])}... (truncated)")
    logger.debug(f"[{pr_id}] Working directory: {PROJECT_DIR}")
    logger.debug(f"[{pr_id}] MCP config: {MCP_CONFIG_PATH}")
    logger.debug(f"[{pr_id}] HOME={env['HOME']}")
    creds_path = os.path.join(env["HOME"], ".claude", ".credentials.json")
    creds_exists = os.path.exists(creds_path)
    logger.debug(f"[{pr_id}] Credentials file exists: {creds_exists} ({creds_path})")
    if creds_exists:
        try:
            with open(creds_path) as f:
                import json
                creds = json.load(f)
                has_token = bool(creds.get("claudeAiOauth", {}).get("accessToken"))
                has_expiry = creds.get("claudeAiOauth", {}).get("expiresAt", "none")
                logger.debug(f"[{pr_id}] Credentials has accessToken: {has_token}, expiresAt: {has_expiry}")
        except Exception as e:
            logger.warning(f"[{pr_id}] Could not read credentials: {e}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
            cwd=PROJECT_DIR,
            env=env,
        )

        duration = round(time.time() - start, 1)

        if result.returncode != 0:
            logger.error(
                f"[{pr_id}] Claude CLI exited with code {result.returncode}"
            )
            if result.stderr:
                logger.error(f"[{pr_id}] STDERR:\n{result.stderr[:3000]}")
            if result.stdout:
                logger.warning(f"[{pr_id}] STDOUT (on failure):\n{result.stdout[:3000]}")
            return {"success": False, "duration": duration, "error": f"Exit code {result.returncode}"}
        else:
            logger.info(f"[{pr_id}] Claude CLI completed successfully")
            logger.info(f"[{pr_id}] Claude output:\n{result.stdout}")
            if result.stderr:
                logger.debug(f"[{pr_id}] STDERR (non-fatal):\n{result.stderr[:2000]}")
            return {"success": True, "duration": duration, "error": None}

    except subprocess.TimeoutExpired:
        logger.error(f"[{pr_id}] Claude CLI timed out after 600s")
        return {"success": False, "duration": round(time.time() - start, 1), "error": "Timed out after 600s"}
    except FileNotFoundError:
        logger.error(f"[{pr_id}] Claude binary not found at: {CLAUDE_BINARY}")
        return {"success": False, "duration": round(time.time() - start, 1), "error": f"Binary not found: {CLAUDE_BINARY}"}
    except Exception as e:
        logger.error(f"[{pr_id}] Failed to spawn Claude CLI: {e}", exc_info=True)
        return {"success": False, "duration": round(time.time() - start, 1), "error": str(e)}


def _build_claude_env():
    """Build common environment for Claude CLI subprocess."""
    env = os.environ.copy()
    env["HOME"] = os.path.expanduser("~")
    env.pop("ANTHROPIC_API_KEY", None)
    return env


# Read-only tools (no write access)
READ_ONLY_TOOLS = [
    "mcp__gitea__pull_request_read",
    "mcp__gitea__get_file_contents",
    "mcp__gitea__get_dir_contents",
    "mcp__gitea__list_my_repos",
    "mcp__gitea__list_pull_requests",
    "mcp__gitea__list_commits",
    "mcp__gitea__list_branches",
    "mcp__gitea__search_repos",
]

# Read + review write tools
READ_REVIEW_TOOLS = READ_ONLY_TOOLS + ["mcp__gitea__pull_request_review_write"]


def run_batch_review(jobs: list) -> dict:
    """Review multiple PRs together for integration issues. Posts to Gitea."""
    start = time.time()
    pr_ids = [f"{j['repo']}#{j['number']}" for j in jobs]
    label = " + ".join(pr_ids)
    logger.info(f"[Batch] Starting integration review: {label}")

    prompt = build_batch_review_prompt(jobs)
    allowed_tools = ",".join(READ_REVIEW_TOOLS)

    cmd = [
        CLAUDE_BINARY,
        "--print",
        "--no-session-persistence",
        "--mcp-config", MCP_CONFIG_PATH,
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--allowed-tools", allowed_tools,
        "--output-format", "text",
        "--disable-slash-commands",
        "--system-prompt", prompt,
        f"Review these PRs together for integration issues: {label}",
    ]

    env = _build_claude_env()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=900, cwd=PROJECT_DIR, env=env,
        )

        duration = round(time.time() - start, 1)

        if result.returncode != 0:
            logger.error(f"[Batch] Claude CLI failed for {label}: exit code {result.returncode}")
            if result.stderr:
                logger.error(f"[Batch] STDERR:\n{result.stderr[:3000]}")
            return {"success": False, "duration": duration, "output": result.stdout, "error": f"Exit code {result.returncode}"}

        logger.info(f"[Batch] Completed integration review: {label}")
        return {"success": True, "duration": duration, "output": result.stdout, "error": None}

    except subprocess.TimeoutExpired:
        logger.error(f"[Batch] Timed out after 900s: {label}")
        return {"success": False, "duration": round(time.time() - start, 1), "output": "", "error": "Timed out after 900s"}
    except Exception as e:
        logger.error(f"[Batch] Failed: {label}: {e}", exc_info=True)
        return {"success": False, "duration": round(time.time() - start, 1), "output": "", "error": str(e)}


def run_codebase_review(repos: list) -> dict:
    """Review full codebases together. Returns output text — does NOT post to Gitea."""
    start = time.time()
    label = " + ".join(f"{r['owner']}/{r['repo']}" for r in repos)
    logger.info(f"[Codebase] Starting codebase review: {label}")

    prompt = build_codebase_review_prompt(repos)
    # Read-only tools only — NO write access
    allowed_tools = ",".join(READ_ONLY_TOOLS)

    cmd = [
        CLAUDE_BINARY,
        "--print",
        "--no-session-persistence",
        "--mcp-config", MCP_CONFIG_PATH,
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--allowed-tools", allowed_tools,
        "--output-format", "text",
        "--disable-slash-commands",
        "--system-prompt", prompt,
        f"Analyze these codebases together and output a full report: {label}",
    ]

    env = _build_claude_env()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=1200, cwd=PROJECT_DIR, env=env,
        )

        duration = round(time.time() - start, 1)

        if result.returncode != 0:
            logger.error(f"[Codebase] Claude CLI failed for {label}: exit code {result.returncode}")
            if result.stderr:
                logger.error(f"[Codebase] STDERR:\n{result.stderr[:3000]}")
            return {"success": False, "duration": duration, "output": result.stdout or "", "error": f"Exit code {result.returncode}"}

        logger.info(f"[Codebase] Completed codebase review: {label} ({duration}s)")
        return {"success": True, "duration": duration, "output": result.stdout, "error": None}

    except subprocess.TimeoutExpired:
        logger.error(f"[Codebase] Timed out after 1200s: {label}")
        return {"success": False, "duration": round(time.time() - start, 1), "output": "", "error": "Timed out after 1200s"}
    except Exception as e:
        logger.error(f"[Codebase] Failed: {label}: {e}", exc_info=True)
        return {"success": False, "duration": round(time.time() - start, 1), "output": "", "error": str(e)}
