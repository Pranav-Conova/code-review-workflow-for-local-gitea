import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # In Docker, env vars are injected by compose

GITEA_HOST = os.environ.get("GITEA_HOST", "http://localhost:3000")
GITEA_TOKEN = os.environ.get("GITEA_TOKEN", "")

PROJECT_DIR = os.environ.get(
    "PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

CLAUDE_BINARY = os.environ.get("CLAUDE_BINARY", "claude")
MAX_CONCURRENT_REVIEWS = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "2"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "10"))

DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.path.join(PROJECT_DIR, "data")
)

LOG_FILE = os.environ.get(
    "LOG_FILE",
    os.path.join(DATA_DIR, "review.log")
)

# File to track which PRs have already been reviewed
REVIEWED_FILE = os.path.join(DATA_DIR, "reviewed.json")
REVIEWS_FILE = os.path.join(DATA_DIR, "reviews.json")

# Dashboard settings
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8000"))
DASHBOARD_ENABLED = os.environ.get("DASHBOARD_ENABLED", "true").lower() == "true"
