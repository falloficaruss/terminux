
import httpx
import time
import subprocess
import os

def run_test():
    # Start backend
    env = os.environ.copy()
    env["TERMINUX_EMBEDDING_BACKEND"] = "gemini"
    env["TERMINUX_GEMINI_API_KEY"] = ""
    env["TERMINUX_OLLAMA_API_BASE"] = "http://127.0.0.1:11436" # Non-existent
    env["TERMINUX_EMBEDDING_DIM"] = "768"
    env["TERMINUX_SQLITE_PATH"] = "./data/test_terminux_hash.db"
    
    if os.path.exists("./data/test_terminux_hash.db"):
        os.remove("./data/test_terminux_hash.db")

    process = subprocess.Popen(
        ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8003"],
        cwd="/home/falloficaruss/terminux/backend",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    time.sleep(5)
    
    api_url = "http://127.0.0.1:8003"
    
    try:
        resp = httpx.get(f"{api_url}/health")
        health = resp.json()
        print(f"Health: {health}")
        
        if health["embedding_backend"] == "hash":
            print("SUCCESS: Fallback to hash worked.")
        else:
            print(f"FAILURE: Expected hash backend, got {health['embedding_backend']}")
            
    finally:
        process.terminate()
        process.wait()
        print("Backend logs (stderr):")
        print(process.stderr.read())

if __name__ == "__main__":
    run_test()
