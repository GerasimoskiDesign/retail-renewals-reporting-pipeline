"""Run the mock portal in a background thread (used by the e2e test and the demo run)."""

from __future__ import annotations

import logging
import socket
import threading
import time

import uvicorn
from fastapi import FastAPI

log = logging.getLogger(__name__)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class BackgroundServer:
    """Uvicorn server on a daemon thread; ``with BackgroundServer(app, port):`` blocks until up."""

    def __init__(self, app: FastAPI, port: int, host: str = "127.0.0.1") -> None:
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.base_url = f"http://{host}:{port}"

    def start(self, timeout: float = 15.0) -> None:
        self.thread.start()
        deadline = time.monotonic() + timeout
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("Mock portal did not start")
            time.sleep(0.05)
        log.info("Mock portal listening on %s", self.base_url)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def __enter__(self) -> BackgroundServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
