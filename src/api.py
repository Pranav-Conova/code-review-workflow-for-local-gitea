"""REST + WebSocket API routes for the PR reviewer dashboard."""

import json
import logging
import urllib.request
import urllib.error

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import GITEA_HOST, GITEA_TOKEN
import state
from log_handler import ws_log_handler

logger = logging.getLogger("pr-review")
router = APIRouter()

templates = Jinja2Templates(directory="src/templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "gitea_host": GITEA_HOST,
    })


@router.get("/api/status")
def get_status():
    return state.get_status_snapshot()


@router.get("/api/reviews")
def get_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str = Query(None),
):
    # Sort by queued_at descending (newest first), nulls last
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
        return {"error": "owner, repo, and pr_number are required"}, 422

    try:
        pr_number = int(pr_number)
    except (ValueError, TypeError):
        return {"error": "pr_number must be an integer"}, 422

    pr_key = f"{owner}/{repo}#{pr_number}"

    # Check if already queued or in-progress
    existing = state.review_history.get(pr_key)
    if existing and existing.get("status") in ("queued", "in-progress"):
        return {"error": f"PR {pr_key} is already {existing['status']}", "pr_key": pr_key}

    # Fetch PR from Gitea to validate it exists
    url = f"{GITEA_HOST}/api/v1/repos/{owner}/{repo}/pulls/{pr_number}?token={GITEA_TOKEN}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            pr = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"error": f"PR {pr_key} not found on Gitea"}
        return {"error": f"Gitea API error: {e.code}"}
    except Exception as e:
        return {"error": f"Failed to reach Gitea: {e}"}

    # Build job and queue it
    job = {
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


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    ws_log_handler.clients.add(websocket)

    # Send buffered log history
    for line in ws_log_handler.get_buffered_logs():
        try:
            await websocket.send_text(line)
        except Exception:
            break

    try:
        while True:
            # Keep connection alive — wait for client messages (pings/close)
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        ws_log_handler.clients.discard(websocket)
