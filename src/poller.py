#!/usr/bin/env python3
"""Poll Gitea for new PRs and auto-review them with Claude Code."""

import json
import logging
import os
import sys
import time
import threading
import urllib.request
import urllib.error

from config import (
    GITEA_HOST, GITEA_TOKEN, POLL_INTERVAL,
    MAX_CONCURRENT_REVIEWS, LOG_FILE, DATA_DIR,
)
import state
from reviewer import run_review

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# --- Logging ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()


def setup_logging():
    """Configure logging with file + console handlers."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.DEBUG),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
        ],
    )


logger = logging.getLogger("pr-review")


# --- Gitea API ---
def gitea_api(path):
    url = f"{GITEA_HOST}/api/v1{path}"
    sep = "&" if "?" in path else "?"
    url += f"{sep}token={GITEA_TOKEN}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    logger.debug(f"API request: GET {GITEA_HOST}/api/v1{path}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            logger.debug(f"API response OK for {path} — {type(data).__name__}")
            return data
    except urllib.error.HTTPError as e:
        logger.error(f"API error {e.code} for {path}: {e.read().decode()[:500]}")
        return None
    except Exception as e:
        logger.error(f"API request failed for {path}: {e}")
        return None


def get_all_repos():
    logger.info("Fetching repos accessible to authenticated user (/user/repos)...")
    repos = []
    page = 1
    while True:
        data = gitea_api(f"/user/repos?limit=50&page={page}")
        if not data:
            logger.debug(f"No data on page {page}, stopping pagination")
            break
        batch = data if isinstance(data, list) else data.get("data", [])
        if not batch:
            logger.debug(f"Empty batch on page {page}, stopping pagination")
            break
        repos.extend(batch)
        logger.debug(f"Page {page}: got {len(batch)} repos")
        page += 1
    repo_names = [f"{r['owner']['login']}/{r['name']}" for r in repos]
    logger.info(f"Fetched {len(repos)} repos: {repo_names}")
    return repos


def get_open_prs(owner, repo):
    logger.debug(f"Checking open PRs for {owner}/{repo}")
    prs = gitea_api(f"/repos/{owner}/{repo}/pulls?state=open&limit=50")
    if prs:
        logger.info(f"{owner}/{repo}: {len(prs)} open PR(s)")
    else:
        logger.debug(f"{owner}/{repo}: no open PRs")
    return prs if prs else []


def update_pr_title(owner, repo, pr_number, new_title):
    """PATCH the PR title via Gitea API."""
    url = f"{GITEA_HOST}/api/v1/repos/{owner}/{repo}/pulls/{pr_number}?token={GITEA_TOKEN}"
    payload = json.dumps({"title": new_title}).encode()
    req = urllib.request.Request(url, data=payload, method="PATCH")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            logger.info(f"{owner}/{repo}#{pr_number} title → '{new_title}'")
            return True
    except Exception as e:
        logger.warning(f"Failed to update PR title for {owner}/{repo}#{pr_number}: {e}")
        return False


# --- Worker ---
def worker():
    thread_name = threading.current_thread().name
    state.set_worker_status(thread_name, "idle")
    logger.info(f"Worker thread {thread_name} started, waiting for jobs...")
    while True:
        job = state.review_queue.get()
        pr_key = f"{job['repo']}#{job['number']}"
        original_title = job["title"]
        try:
            state.set_worker_status(thread_name, "reviewing", pr_key)
            state.update_review(pr_key, "in-progress")

            # Add WIP prefix so the author knows review is in progress
            update_pr_title(job["owner"], job["repo_name"], job["number"], f"WIP: {original_title}")

            logger.info(f"[{thread_name}] Starting review: {pr_key} — {original_title}")
            result = run_review(job)

            if result and result.get("success"):
                state.update_review(pr_key, "done", {
                    "duration_seconds": result.get("duration"),
                })
                logger.info(f"[{thread_name}] Finished review: {pr_key}")
            else:
                error_msg = result.get("error", "Unknown error") if result else "No result returned"
                state.update_review(pr_key, "failed", {"error": error_msg})
                logger.error(f"[{thread_name}] Review failed {pr_key}: {error_msg}")
        except Exception as e:
            state.update_review(pr_key, "failed", {"error": str(e)})
            logger.error(f"[{thread_name}] Review failed {pr_key}: {e}", exc_info=True)
        finally:
            # Remove WIP prefix when review is done (success or failure)
            update_pr_title(job["owner"], job["repo_name"], job["number"], original_title)
            state.set_worker_status(thread_name, "idle")
            state.review_queue.task_done()


def start_workers():
    """Start worker threads."""
    for _ in range(MAX_CONCURRENT_REVIEWS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()


def run_poll_loop():
    """Run the infinite polling loop. Blocking — meant to be called in a thread."""
    logger.info(f"Starting poller — checking every {POLL_INTERVAL}s")
    logger.info(f"Already reviewed: {len(state.review_history)} PRs")

    repos = []
    last_repo_fetch = 0

    while True:
        # Check if poller is paused
        if not state.poller_running:
            time.sleep(POLL_INTERVAL)
            continue

        try:
            now = time.time()
            if now - last_repo_fetch > 300:
                repos = get_all_repos()
                last_repo_fetch = now

            logger.debug(f"--- Poll cycle start — scanning {len(repos)} repos ---")
            new_count = 0
            for r in repos:
                owner = r["owner"]["login"]
                name = r["name"]

                prs = get_open_prs(owner, name)
                for pr in prs:
                    pr_key = f"{owner}/{name}#{pr['number']}"

                    if pr_key in state.review_history:
                        logger.debug(f"Skipping {pr_key} — already reviewed")
                        continue

                    job = {
                        "number": pr["number"],
                        "title": pr["title"],
                        "body": pr.get("body", ""),
                        "head_branch": pr["head"]["ref"],
                        "base_branch": pr["base"]["ref"],
                        "head_sha": pr["head"]["sha"],
                        "owner": owner,
                        "repo_name": name,
                        "repo": f"{owner}/{name}",
                        "sender": pr["user"]["login"],
                    }

                    logger.info(
                        f"New PR: {pr_key} — '{job['title']}' by {job['sender']}"
                    )
                    logger.info(
                        f"  Branch: {job['base_branch']} <- {job['head_branch']} | SHA: {job['head_sha'][:8]}"
                    )

                    # Store rich metadata
                    state.update_review(pr_key, "queued", {
                        "number": pr["number"],
                        "title": pr["title"],
                        "owner": owner,
                        "repo_name": name,
                        "sender": pr["user"]["login"],
                        "head_branch": pr["head"]["ref"],
                        "base_branch": pr["base"]["ref"],
                        "gitea_url": f"{GITEA_HOST}/{owner}/{name}/pulls/{pr['number']}",
                        "triggered_by": "poller",
                    })

                    state.review_queue.put(job)
                    new_count += 1

            if new_count > 0:
                logger.info(f"Queued {new_count} new PR(s) for review")
            else:
                logger.debug("No new PRs found this cycle")

        except Exception as e:
            logger.error(f"Poll cycle error: {e}")

        time.sleep(POLL_INTERVAL)


# --- Main (backward-compatible standalone entry point) ---
def main():
    if not GITEA_TOKEN:
        print("ERROR: Set GITEA_TOKEN environment variable")
        sys.exit(1)

    setup_logging()
    state.load_history()
    start_workers()
    run_poll_loop()


if __name__ == "__main__":
    main()
