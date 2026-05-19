# Manual Testing Guide: Phase A & Phase B

This guide provides step-by-step instructions to test terminal command capture, Zsh/Bash hooks, classification precision, and secret redaction.

---

## Step 1: Start the Backend API Server

In your first terminal tab, start the local FastAPI backend server:

```bash
cd /home/falloficaruss/terminux/backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Keep this tab open to monitor the log outputs in real-time.

---

## Step 2: Open a New Shell and Source the Hook

Open a **new terminal shell tab** and source the integration script depending on the shell you are running:

### For Zsh Users:
```bash
source /home/falloficaruss/terminux/scripts/terminux_hook.zsh
```

### For Bash Users:
```bash
source /home/falloficaruss/terminux/scripts/terminux_hook.bash
```

*Note: Once sourced, a nested session wrapped in `script` will automatically start to capture input/output.*

---

## Step 3: Verify Passive Interactive Capture (Phase A)

In the hooked terminal tab, run some standard commands:

```bash
ls -la
pwd
echo "hello world"
```

### What to check:
- Switch to the **Backend API Server tab**. You should see incoming `POST /v1/events` log outputs being processed with `200 OK` status codes immediately after each command finishes execution!

---

## Step 4: Verify Precision Categorization (Phase B)

Test the refined classifier's capability to differentiate true tool runs from false-positive strings:

### A. Test False Positives (Should resolve to `general` category)
Execute these commands in the hooked terminal tab:
```bash
echo "compose an email"
echo "check uv levels"
echo "my access token is token_name"
```

### B. Test Genuine Commands (Should resolve to specific categories)
Execute these commands in the hooked terminal tab:
```bash
docker compose up --help
uv run --version
export access_token=secret_val
```

### C. Verify Results in CLI
In another terminal, use the `tm recall` CLI tool to inspect how these events were classified:
```bash
python /home/falloficaruss/terminux/tm recall "compose an email"
python /home/falloficaruss/terminux/tm recall "docker compose up"
python /home/falloficaruss/terminux/tm recall "uv run"
```
You will observe that `"compose an email"` and `"check uv levels"` are correctly classified under `general`, whereas `"docker compose up"` is classified under `container` and `"uv run"` under `python-dev`!

---

## Step 5: Verify Secret Redaction Heuristics (Phase B)

Verify that webhooks, SSH private keys, and quoted secret assignments are completely sanitized before persistence:

### A. Execute Sensitive Commands
Run these commands in the hooked terminal tab:
```bash
echo "Slack Webhook: https://hooks.slack.com/services/T012ABC34/B012DEF34/abc123xyz456"
echo "Discord Webhook: https://discord.com/api/webhooks/1234567890/abc123xyz_DEF-ghi"
echo -e "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtcn\ncHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcA==\n-----END OPENSSH PRIVATE KEY-----"
password = "supersecretpassword123"
```

### B. Inspect Redacted Log Values in the CLI
Execute a recall query to inspect the logged values:
```bash
python /home/falloficaruss/terminux/tm recall "hooks.slack.com"
```
You will see that the webhook strings, the entire private key multiline block, and the quoted password string have been fully replaced with the `[REDACTED]` marker in the returned output!
