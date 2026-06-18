"""Integration tests for the Rust terminux-daemon.

Spawns the compiled Rust binary in a subprocess and asserts that it correctly
sends HTTP POST payloads to the Terminux API.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Generator

import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
DAEMON_BIN = ROOT_DIR / "daemon" / "target" / "debug" / "terminux-daemon"
_BINARY_EXISTS = DAEMON_BIN.exists()

pytestmark = pytest.mark.skipif(
    not _BINARY_EXISTS,
    reason=f"Rust daemon binary not found at {DAEMON_BIN}; run `cargo build` in daemon/",
)


# ---------------------------------------------------------------------------
# Dynamic Port Discovery & Background Mock HTTP Server
# ---------------------------------------------------------------------------
def get_free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class MockHandler(BaseHTTPRequestHandler):
    requests_received: list[dict] = []

    def do_POST(self) -> None:
        content_length = int(self.headers["Content-Length"])
        post_data = self.rfile.read(content_length)

        MockHandler.requests_received.append(
            {
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(post_data.decode("utf-8")),
            }
        )

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"event_id": 42, "session_id": 7, "category": "general"}')

    def log_message(self, format: str, *args: any) -> None:
        # Prevent logging spam during pytest execution
        pass


@pytest.fixture()
def mock_api_server() -> Generator[str, None, None]:
    """Spawns a local HTTP server in a background thread to capture POSTs."""
    MockHandler.requests_received.clear()
    port = get_free_port()
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join()


# ---------------------------------------------------------------------------
# Daemon Tests
# ---------------------------------------------------------------------------
def test_daemon_binary_exists() -> None:
    """Ensure the binary has been compiled and is at the expected path."""
    assert DAEMON_BIN.exists(), (
        f"Daemon binary not found at {DAEMON_BIN}. "
        f"Run 'cargo build' in the daemon directory first."
    )


def test_emit_command(mock_api_server: str) -> None:
    """Verifies 'terminux-daemon emit' successfully hits the API."""
    env = os.environ.copy()
    env["TERMINUX_API_URL"] = mock_api_server

    result = subprocess.run(
        [
            str(DAEMON_BIN),
            "emit",
            "--command",
            "git status",
            "--cwd",
            "/tmp",
            "--output",
            "On branch main",
            "--exit-code",
            "0",
            "--duration-ms",
            "120",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Daemon failed: {result.stderr}"
    assert len(MockHandler.requests_received) == 1

    req = MockHandler.requests_received[0]
    assert req["path"] == "/v1/events"
    assert req["body"]["command"] == "git status"
    assert req["body"]["cwd"] == "/tmp"
    assert req["body"]["output"] == "On branch main"
    assert req["body"]["exit_code"] == 0
    assert req["body"]["duration_ms"] == 120
    assert "timestamp" in req["body"]


def test_emit_from_file_command(mock_api_server: str, tmp_path: Path) -> None:
    """Verifies 'terminux-daemon emit-from-file' correctly reads output file."""
    output_file = tmp_path / "terminal_output.log"
    output_file.write_text("Hello from the log file output!\nAnother line.")

    env = os.environ.copy()
    env["TERMINUX_API_URL"] = mock_api_server

    result = subprocess.run(
        [
            str(DAEMON_BIN),
            "emit-from-file",
            "--command",
            "cat terminal_output.log",
            "--output-file",
            str(output_file),
            "--cwd",
            "/home/user",
            "--exit-code",
            "1",
            "--duration-ms",
            "250",
        ],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Daemon failed: {result.stderr}"
    assert len(MockHandler.requests_received) == 1

    req = MockHandler.requests_received[0]
    assert req["path"] == "/v1/events"
    assert req["body"]["command"] == "cat terminal_output.log"
    assert req["body"]["cwd"] == "/home/user"
    assert req["body"]["output"] == "Hello from the log file output!\nAnother line."
    assert req["body"]["exit_code"] == 1
    assert req["body"]["duration_ms"] == 250


def test_read_json_command(mock_api_server: str) -> None:
    """Verifies 'terminux-daemon read-json' consumes JSON via stdin."""
    payload = {
        "command": "poetry run pytest",
        "cwd": "/home/user/app",
        "output": "10 passed in 0.12s",
        "exit_code": 0,
        "duration_ms": 1500,
        "timestamp": "2026-05-18T10:00:00Z",
    }
    input_str = json.dumps(payload)

    env = os.environ.copy()
    env["TERMINUX_API_URL"] = mock_api_server

    result = subprocess.run(
        [str(DAEMON_BIN), "read-json"],
        input=input_str,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Daemon failed: {result.stderr}"
    assert len(MockHandler.requests_received) == 1

    req = MockHandler.requests_received[0]
    assert req["path"] == "/v1/events"
    assert req["body"]["command"] == "poetry run pytest"
    assert req["body"]["cwd"] == "/home/user/app"
    assert req["body"]["output"] == "10 passed in 0.12s"
    assert req["body"]["exit_code"] == 0
    assert req["body"]["duration_ms"] == 1500
    assert req["body"]["timestamp"] == "2026-05-18T10:00:00Z"
