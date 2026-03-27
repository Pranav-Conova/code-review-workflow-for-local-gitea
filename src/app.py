#!/usr/bin/env python3
"""FastAPI dashboard + PR review poller — unified entry point."""

import asyncio
import logging
import os
import sys
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import (
    GITEA_TOKEN, DASHBOARD_HOST, DASHBOARD_PORT, DASHBOARD_ENABLED, DATA_DIR,
    LOG_FILE,
)
import state
from log_handler import ws_log_handler
from api import router

# Ensure data directory exists
os.makedirs(DATA_DIR, exist_ok=True)

LOG_LEVEL = os.environ.get("LOG_LEVEL", "DEBUG").upper()


def setup_logging():
    """Configure logging with file + console + WebSocket handlers."""
    ws_log_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.DEBUG),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(),
            ws_log_handler,
        ],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Store the event loop for sync-to-async log broadcasting
    ws_log_handler.set_event_loop(asyncio.get_running_loop())

    logger = logging.getLogger("pr-review")
    logger.info("Dashboard starting up...")

    # Load review history from disk
    state.load_history()
    logger.info(f"Loaded {len(state.review_history)} review(s) from history")

    # Import poller here to avoid circular imports at module level
    import poller

    # Start worker threads
    poller.start_workers()

    # Start poller loop in a daemon thread
    poller_thread = threading.Thread(
        target=poller.run_poll_loop,
        name="poller",
        daemon=True,
    )
    poller_thread.start()
    logger.info("Poller thread started")

    yield

    logger.info("Dashboard shutting down...")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Claude PR Reviewer",
        lifespan=lifespan,
    )
    app.include_router(router)

    # Serve static files (CSS)
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    os.makedirs(static_dir, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    return app


app = create_app()


if __name__ == "__main__":
    if not GITEA_TOKEN:
        print("ERROR: Set GITEA_TOKEN environment variable")
        sys.exit(1)

    setup_logging()

    if not DASHBOARD_ENABLED:
        # Fall back to standalone poller mode
        import poller
        poller.main()
    else:
        import uvicorn
        uvicorn.run(
            app,
            host=DASHBOARD_HOST,
            port=DASHBOARD_PORT,
            log_level="info",
        )
