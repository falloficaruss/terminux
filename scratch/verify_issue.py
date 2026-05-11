
import httpx
import time
import subprocess
import os
import signal

def run_test():
    # Start backend
    env = os.environ.copy()
    env["TERMINUX_EMBEDDING_BACKEND"] = "hash"
    env["TERMINUX_SQLITE_PATH"] = "./data/test_terminux.db"
    
    if os.path.exists("./data/test_terminux.db"):
        os.remove("./data/test_terminux.db")

    process = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001"],
        cwd="/home/falloficaruss/terminux/backend",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    time.sleep(3) # Wait for startup
    
    api_url = "http://127.0.0.1:8001"
    
    try:
        # Ingest pipewire event
        print("Ingesting pipewire event...")
        resp = httpx.post(f"{api_url}/v1/events", json={
            "command": "systemctl --user restart pipewire",
            "output": "Job for pipewire.service started.",
            "exit_code": 0,
            "cwd": "/home/falloficaruss",
            "timestamp": "2026-05-11T10:00:00Z"
        })
        print(f"Ingest status: {resp.status_code}")
        
        # Search for bluetooth audio
        print("Searching for 'bluetooth audio'...")
        resp = httpx.get(f"{api_url}/v1/recall", params={"query": "bluetooth audio", "limit": 5})
        print(f"Recall results: {resp.json()}")
        
        found = any("pipewire" in item["command"] for item in resp.json()["results"])
        if not found:
            print("FAILURE: 'pipewire' not found for 'bluetooth audio' query with hash backend.")
        else:
            print("SUCCESS: 'pipewire' found (unexpectedly for hash backend, maybe keyword match?)")

    finally:
        process.terminate()
        process.wait()

if __name__ == "__main__":
    run_test()
