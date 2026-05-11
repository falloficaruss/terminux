
import httpx
import time
import subprocess
import os
import signal
from fastapi import FastAPI
import uvicorn
import threading

# Mock Ollama server
mock_ollama_app = FastAPI()

@mock_ollama_app.get("/api/tags")
def tags():
    return {"models": [{"name": "nomic-embed-text"}]}

@mock_ollama_app.post("/api/embeddings")
def embeddings(payload: dict):
    # Return a 768-dim mock vector
    return {"embedding": [0.1] * 768}

def run_mock_ollama():
    uvicorn.run(mock_ollama_app, host="127.0.0.1", port=11435)

def run_test():
    # Start mock Ollama in a thread
    ollama_thread = threading.Thread(target=run_mock_ollama, daemon=True)
    ollama_thread.start()
    time.sleep(2)

    # Start backend
    env = os.environ.copy()
    # Requested backend gemini, but no key -> should fallback to ollama
    env["TERMINUX_EMBEDDING_BACKEND"] = "gemini"
    env["TERMINUX_GEMINI_API_KEY"] = ""
    env["TERMINUX_OLLAMA_API_BASE"] = "http://127.0.0.1:11435"
    env["TERMINUX_EMBEDDING_DIM"] = "768"
    env["TERMINUX_SQLITE_PATH"] = "./data/test_terminux_new.db"
    
    if os.path.exists("./data/test_terminux_new.db"):
        os.remove("./data/test_terminux_new.db")

    process = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8002"],
        cwd="/home/falloficaruss/terminux/backend",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    time.sleep(5) # Wait for startup
    
    api_url = "http://127.0.0.1:8002"
    
    try:
        # Check health to see active backend
        print("Checking health...")
        resp = httpx.get(f"{api_url}/health")
        health = resp.json()
        print(f"Health: {health}")
        
        if health["embedding_backend"] == "ollama":
            print("SUCCESS: Fallback to ollama worked.")
        else:
            print(f"FAILURE: Expected ollama backend, got {health['embedding_backend']}")

        # Ingest pipewire event
        print("Ingesting event...")
        resp = httpx.post(f"{api_url}/v1/events", json={
            "command": "systemctl --user restart pipewire",
            "output": "Job for pipewire.service started.",
            "exit_code": 0,
            "cwd": "/home/falloficaruss",
            "timestamp": "2026-05-11T10:00:00Z"
        })
        print(f"Ingest status: {resp.status_code}")
        
        # Verify vector dim (implicitly by ingest success if it went to qdrant, but here we don't have qdrant)
        # We can check logs for startup info
        print("Checking backend logs for startup message...")
        # Since we use subprocess.PIPE, we can read a bit
        # But uvicorn logs to stderr usually
        
    finally:
        process.terminate()
        process.wait()
        print("Backend logs (stderr):")
        print(process.stderr.read())

if __name__ == "__main__":
    run_test()
