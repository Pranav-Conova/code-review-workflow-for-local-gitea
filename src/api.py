"""REST + WebSocket API routes for the PR reviewer dashboard."""

import json
import logging
import threading
import urllib.request
import urllib.error

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import GITEA_HOST, GITEA_TOKEN
import state
from log_handler import ws_log_handler
from reviewer import run_batch_review, run_codebase_review

logger = logging.getLogger("pr-review")
router = APIRouter()

templates = Jinja2Templates(directory="src/templates")


# --- Dashboard ---
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "gitea_host": GITEA_HOST,
    })


# --- System Status ---
@router.get("/api/status")
def get_status():
    return state.get_status_snapshot()


# --- Poller Control ---
@router.post("/api/poller/toggle")
def toggle_poller():
    state.poller_running = not state.poller_running
    status = "running" if state.poller_running else "stopped"
    logger.info(f"Poller toggled: {status}")
    return {"poller_running": state.poller_running}


# --- PR Reviews ---
@router.get("/api/reviews")
def get_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query(None),
):
    reviews = sorted(
        state.review_history.values(),
        key=lambda r: r.get("queued_at") or 0,
        reverse=True,
    )
    if status:
        reviews = [r for r in reviews if r.get("status") == status]
    total = len(reviews)
    reviews = reviews[offset:offset + limit]
    return {"total": total, "reviews": reviews}


@router.post("/api/reviews/trigger", status_code=202)
def trigger_review(body: dict):
    owner = body.get("owner", "").strip()
    repo = body.get("repo", "").strip()
    pr_number = body.get("pr_number")

    if not owner or not repo or not pr_number:
        return {"error": "owner, repo, and pr_number are required"}

    try:
        pr_number = int(pr_number)
    except (ValueError, TypeError):
        return {"error": "pr_number must be an integer"}

    pr_key = f"{owner}/{repo}#{pr_number}"

    existing = state.review_history.get(pr_key)
    if existing and existing.get("status") in ("queued", "in-progress"):
        return {"error": f"PR {pr_key} is already {existing['status']}", "pr_key": pr_key}

    pr = _fetch_pr(owner, repo, pr_number)
    if isinstance(pr, dict) and "error" in pr:
        return pr

    job = _pr_to_job(pr, owner, repo)

    state.update_review(pr_key, "queued", {
        "number": pr["number"],
        "title": pr["title"],
        "owner": owner,
        "repo_name": repo,
        "sender": pr["user"]["login"],
        "head_branch": pr["head"]["ref"],
        "base_branch": pr["base"]["ref"],
        "gitea_url": f"{GITEA_HOST}/{owner}/{repo}/pulls/{pr_number}",
        "triggered_by": "manual",
    })

    state.review_queue.put(job)
    logger.info(f"Manual trigger: queued {pr_key} for review")
    return {"pr_key": pr_key, "status": "queued", "message": "PR queued for review"}


# --- Batch PR Review (integration analysis, posts to Gitea) ---
@router.get("/api/batch-reviews")
def get_batch_reviews():
    reviews = sorted(
        state.batch_reviews.values(),
        key=lambda r: r.get("created_at") or 0,
        reverse=True,
    )
    return {"reviews": reviews}


@router.get("/api/batch-reviews/{bid}")
def get_batch_review(bid: str):
    entry = state.batch_reviews.get(bid)
    if not entry:
        return {"error": "Not found"}
    return entry


@router.post("/api/batch-reviews", status_code=202)
def trigger_batch_review(body: dict):
    prs = body.get("prs", [])
    if len(prs) < 2:
        return {"error": "Need at least 2 PRs for batch review"}

    # Validate and fetch all PRs
    jobs = []
    for pr_ref in prs:
        owner = pr_ref.get("owner", "").strip()
        repo = pr_ref.get("repo", "").strip()
        pr_number = pr_ref.get("pr_number")
        if not owner or not repo or not pr_number:
            return {"error": f"Invalid PR reference: {pr_ref}"}

        pr = _fetch_pr(owner, repo, int(pr_number))
        if isinstance(pr, dict) and "error" in pr:
            return pr
        jobs.append(_pr_to_job(pr, owner, repo))

    bid = state.create_batch_review(
        [{"owner": j["owner"], "repo_name": j["repo_name"], "number": j["number"], "title": j["title"]} for j in jobs]
    )

    # Run in background thread
    def _run():
        state.update_batch_review(bid, "in-progress")
        logger.info(f"[Batch:{bid}] Starting integration review")
        result = run_batch_review(jobs)
        if result["success"]:
            state.update_batch_review(bid, "done", result=result.get("output", ""))
            logger.info(f"[Batch:{bid}] Completed")
        else:
            state.update_batch_review(bid, "failed", error=result.get("error", "Unknown"))
            logger.error(f"[Batch:{bid}] Failed: {result.get('error')}")

    threading.Thread(target=_run, daemon=True, name=f"batch-{bid}").start()

    return {"id": bid, "status": "queued", "message": "Batch review started"}


# --- Codebase Review (full repo analysis, website-only, NO Gitea posting) ---
@router.get("/api/codebase-reviews")
def get_codebase_reviews():
    reviews = sorted(
        state.codebase_reviews.values(),
        key=lambda r: r.get("created_at") or 0,
        reverse=True,
    )
    return {"reviews": reviews}


@router.get("/api/codebase-reviews/{cid}")
def get_codebase_review(cid: str):
    entry = state.codebase_reviews.get(cid)
    if not entry:
        return {"error": "Not found"}
    return entry


@router.post("/api/codebase-reviews", status_code=202)
def trigger_codebase_review(body: dict):
    repos = body.get("repos", [])
    if len(repos) < 1:
        return {"error": "Need at least 1 repository"}

    # Validate repos exist
    for repo_ref in repos:
        owner = repo_ref.get("owner", "").strip()
        repo = repo_ref.get("repo", "").strip()
        if not owner or not repo:
            return {"error": f"Invalid repo reference: {repo_ref}"}

    cid = state.create_codebase_review(repos)

    # Run in background thread
    def _run():
        state.update_codebase_review(cid, "in-progress")
        logger.info(f"[Codebase:{cid}] Starting codebase review")
        result = run_codebase_review(repos)
        if result["success"]:
            state.update_codebase_review(cid, "done", result=result.get("output", ""))
            logger.info(f"[Codebase:{cid}] Completed")
        else:
            state.update_codebase_review(cid, "failed", result=result.get("output", ""), error=result.get("error", "Unknown"))
            logger.error(f"[Codebase:{cid}] Failed: {result.get('error')}")

    threading.Thread(target=_run, daemon=True, name=f"codebase-{cid}").start()

    return {"id": cid, "status": "queued", "message": "Codebase review started"}


# --- WebSocket ---
@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    ws_log_handler.clients.add(websocket)

    for line in ws_log_handler.get_buffered_logs():
        try:
            await websocket.send_text(line)
        except Exception:
            break

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_log_handler.clients.discard(websocket)


# --- Helpers ---
def _fetch_pr(owner, repo, pr_number):
    """Fetch a PR from Gitea. Returns PR dict or error dict."""
    url = f"{GITEA_HOST}/api/v1/repos/{owner}/{repo}/pulls/{pr_number}?token={GITEA_TOKEN}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"PR {owner}/{repo}#{pr_number} not found on Gitea"}
        return {"error": f"Gitea API error: {e.code}"}
    except Exception as e:
        return {"error": f"Failed to reach Gitea: {e}"}


def _pr_to_job(pr, owner, repo):
    """Convert a Gitea PR API response to a job dict."""
    return {
        "number": pr["number"],
        "title": pr["title"],
        "body": pr.get("body", ""),
        "head_branch": pr["head"]["ref"],
        "base_branch": pr["base"]["ref"],
        "head_sha": pr["head"]["sha"],
        "owner": owner,
        "repo_name": repo,
        "repo": f"{owner}/{repo}",
        "sender": pr["user"]["login"],
    }
