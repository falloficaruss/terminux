import os
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from backend.app.db import Store, to_iso
from backend.app.vector_store import VectorStore
from backend.app.config import settings

def test_cwd_narrowness():
    db_path = "test_terminux_cwd.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Initialize components
    store = Store(sqlite_path=db_path, session_gap_seconds=1200)
    vstore = VectorStore(settings)
    
    now = datetime.now(tz=timezone.utc)
    
    # Simulate failure in a subdirectory
    cwd_sub = "/home/user/project/src"
    cmd = "docker compose up"
    
    # Ingest failure
    event_id1, session_id1 = store.add_event(
        command=cmd,
        output="Error",
        exit_code=1,
        duration_ms=100,
        cwd=cwd_sub,
        category="error",
        root_cause="missing .env",
        event_time=now,
        env={}
    )
    
    # Simulate success in the project root
    cwd_root = "/home/user/project"
    success_time = now + timedelta(minutes=5)
    
    # New ingestion logic (as implemented in main.py)
    # 1. Search for failure
    # Cross-session exact match (uses cwd)
    failure = store.find_recent_failure_cross_session(cwd=cwd_root, command=cmd)
    
    if failure:
        print("SUCCESS: Found failure across directories!")
    else:
        print("FAILURE: Could NOT find failure because directories differ.")
        print(f"  Failure CWD: {cwd_sub}")
        print(f"  Success CWD: {cwd_root}")

    os.remove(db_path)

if __name__ == "__main__":
    test_cwd_narrowness()
