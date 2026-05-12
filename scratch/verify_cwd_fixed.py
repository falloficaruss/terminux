import os
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from backend.app.db import Store, to_iso, find_project_root
from backend.app.vector_store import VectorStore
from backend.app.config import settings

def test_cwd_narrowness_fixed():
    db_path = "test_terminux_cwd_fixed.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Initialize components
    store = Store(sqlite_path=db_path, session_gap_seconds=1200)
    
    now = datetime.now(tz=timezone.utc)
    
    # Simulate a project root
    # We'll use real paths if possible, but for mocking we'll just use strings that find_project_root would handle
    # Actually, let's create a real temporary project structure
    base = os.path.abspath("test_proj_root")
    sub = os.path.join(base, "src")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(base, ".git"), "w") as f:
        f.write("")
        
    print(f"Project base: {base}")
    print(f"Project sub: {sub}")
    
    root_base = find_project_root(base)
    root_sub = find_project_root(sub)
    
    print(f"Detected root for base: {root_base}")
    print(f"Detected root for sub: {root_sub}")
    
    if root_base != root_sub or root_base != base:
        print("ERROR: Project root detection failed in test setup!")
    
    cmd = "docker compose up"
    
    # 1. Ingest failure in sub
    event_id1, session_id1 = store.add_event(
        command=cmd,
        output="Error",
        exit_code=1,
        duration_ms=100,
        cwd=sub,
        project_root=root_sub,
        category="error",
        root_cause="missing .env",
        event_time=now,
        env={}
    )
    
    # 2. Ingest success in base (after 5 mins)
    success_time = now + timedelta(minutes=5)
    
    # Simulation of main.py logic
    failure = store.find_recent_failure_cross_session(project_root=root_base, command=cmd)
    
    if failure:
        print("SUCCESS: Found failure across directories using project_root!")
        print(f"  Linked failure ID: {failure['id']}")
        print(f"  Failure CWD: {failure['cwd']}")
    else:
        print("FAILURE: Still could NOT find failure across directories.")

    # Check if they are in the same session
    event_id2, session_id2 = store.add_event(
        command=cmd,
        output="Success",
        exit_code=0,
        duration_ms=5000,
        cwd=base,
        project_root=root_base,
        category="success",
        root_cause=None,
        event_time=success_time,
        env={}
    )
    
    if session_id1 == session_id2:
        print(f"SUCCESS: Both events share the same session ID: {session_id1}")
    else:
        print(f"FAILURE: Events have different session IDs: {session_id1} vs {session_id2}")

    # Cleanup
    import shutil
    shutil.rmtree(base)
    if os.path.exists(db_path):
        os.remove(db_path)

if __name__ == "__main__":
    test_cwd_narrowness_fixed()
