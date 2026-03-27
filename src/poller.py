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
from queue import Queue

from config import (
    GITEA_HOST, GITEA_TOKEN, POLL_INTERVAL,
    MAX_CONCURRENT_REVIEWS, LOG_FILE, REVIEWED_FILE, DATA_DIR,
)
from reviewer import run_review

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

# --- Logging ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.DEBUG),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("pr-review")

# --- Track reviewed PRs ---
def load_reviewed():
    if os.path.exists(REVIEWED_FILE):
        with open(REVIEWED_FILE) as f:
            return set(json.load(f))
    return set()


def save_reviewed(reviewed: set):
    with open(REVIEWED_FILE, "w") as f:
        json.dump(sorted(reviewed), f)


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


# --- Review queue ---
review_queue = Queue()


def worker():
    thread_name = threading.current_thread().name
    logger.info(f"Worker thread {thread_name} started, waiting for jobs...")
    while True:
        job = review_queue.get()
        pr_id = f"{job['repo']}#{job['number']}"
        original_title = job["title"]
        try:
            # Add WIP prefix so the author knows review is in progress
            update_pr_title(job["owner"], job["repo_name"], job["number"], f"WIP: {original_title}")

            logger.info(f"[{thread_name}] Starting review: {pr_id} — {original_title}")
            run_review(job)
            logger.info(f"[{thread_name}] Finished review: {pr_id}")
        except Exception as e:
            logger.error(f"[{thread_name}] Review failed {pr_id}: {e}", exc_info=True)
        finally:
            # Remove WIP prefix when review is done (success or failure)
            update_pr_title(job["owner"], job["repo_name"], job["number"], original_title)
            review_queue.task_done()


# --- Main loop ---
def main():
    if not GITEA_TOKEN:
        print("ERROR: Set GITEA_TOKEN environment variable")
        sys.exit(1)

    # Start worker threads
    for _ in range(MAX_CONCURRENT_REVIEWS):
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    reviewed = load_reviewed()
    logger.info(f"Starting poller — checking every {POLL_INTERVAL}s")
    logger.info(f"Already reviewed: {len(reviewed)} PRs")

    # Fetch repos once at startup, refresh every 5 minutes
    repos = []
    last_repo_fetch = 0

    while True:
        try:
            # Refresh repo list every 5 minutes
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
                    # Unique key: owner/repo#number
                    pr_key = f"{owner}/{name}#{pr['number']}"

                    if pr_key in reviewed:
                        logger.debug(f"Skipping {pr_key} — already reviewed")
                        continue

                    # New PR found
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
                    review_queue.put(job)

                    # Mark as reviewed immediately to avoid duplicate queueing
                    reviewed.add(pr_key)
                    save_reviewed(reviewed)
                    new_count += 1

            if new_count > 0:
                logger.info(f"Queued {new_count} new PR(s) for review")
            else:
                logger.debug("No new PRs found this cycle")

        except Exception as e:
            logger.error(f"Poll cycle error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
