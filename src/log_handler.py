"""Custom logging handler that broadcasts log records to WebSocket clients."""

import asyncio
import logging
from collections import deque


class WebSocketLogHandler(logging.Handler):
    """Captures log records and sends them to connected WebSocket clients.

    Designed to be called from sync threads (poller/workers) and bridge
    to the async FastAPI event loop via asyncio.run_coroutine_threadsafe().
    """

    def __init__(self, max_buffer=500):
        super().__init__()
        self.clients = set()
        self.buffer = deque(maxlen=max_buffer)
        self._loop = None  # Set during FastAPI startup

    def set_event_loop(self, loop):
        """Store reference to the running asyncio event loop."""
        self._loop = loop

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return

        self.buffer.append(msg)

        if self._loop and self.clients:
            try:
                asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
            except RuntimeError:
                pass  # Loop is closed or not running

    async def _broadcast(self, msg):
        dead = set()
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self.clients -= dead

    def get_buffered_logs(self):
        """Return recent log lines for new WebSocket connections."""
        return list(self.buffer)


# Module-level singleton
ws_log_handler = WebSocketLogHandler()
