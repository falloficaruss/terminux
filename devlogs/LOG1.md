## 1. Architectural Blueprint (Current State)

The project consists of three core components:

```mermaid
graph TD
    A[Shell Hooks / User Term] -->|Manual Command / Future Hook| B[Rust Daemon /daemon]
    B -->|Ingest payload| C[FastAPI Backend /backend]
    C -->|Store raw metadata| D[(SQLite DB)]
    C -->|Embed & index| E[(Qdrant Vector Store)]
    C -->|Synthesis API| F[Gemini Generative API]
    G[CLI Tool: tm] -->|Query endpoints| C
```

### Component Status Table

| Component | Status | Key Features | Tech Stack |
| :--- | :--- | :--- | :--- |
| **FastAPI Backend** | **Solid V1** | Ingestion, session gap detection, semantic recall, SQLite + Qdrant sync. | Python, FastAPI, SQLite |
| **CLI Tool (`tm`)** | **Solid V1** | Rich panels, formatted tables, subcommands for recall, reports, replay. | Python, `rich`, `httpx` |
| **Capture Daemon** | **Partial V1** | CLI wrapper for manual/file ingestions. **No active passive watcher.** | Rust, `clap`, `reqwest` |
| **Embedding Engine** | **Excellent** | Multi-backend fallback (Gemini → Ollama → Local SHA1 token hashing). | Python, `httpx`, `hashlib` |
| **Redaction Layer** | **Good** | Regex-based scrubbing of AWS keys, database connection strings, JWTs, IPs. | Python, `re` |

---

## 2. Core Strengths & Completed Milestones (V1)

Reviewing `PLAN.md` shows that the core of Phase 1 (V1) is highly functional:
- **Intelligent Sessionizer**: `db.py` groups consecutive terminal commands into distinct logical sessions anchored around the inferred `project_root` (resolving `.git`, `Cargo.toml`, etc.) and a configurable time gap (e.g., 20 minutes).
- **Failure-Recovery Tracking**: The system detects a failing command (exit code $\neq 0$), captures its `likely_root_cause` via stdout heuristics, and automatically registers a recovery link (`failure_fixes` table) when the same command subsequently succeeds.
- **Hybrid Recall & Synthesis**: `/v1/recall` combines semantic vectors from Qdrant with classic SQLite `LIKE` fallbacks, feeding relevant context to the Gemini generative model to write a clean natural language answer to questions like `tm recall "why did docker fail last time?"`.
- **Weekly Reporting**: Automatic aggregation of category stats, failure rates, and recurring failure commands.

---

## 3. Discovered Gaps & Technical Opportunities

### A. The Capture Daemon is Missing Shell Hooks
> [!IMPORTANT]
> The largest architectural gap is the **lack of automated shell interception**. Right now, commands must be manually ingested via `tm ingest` or `daemon`. Passive observation (the primary value proposition of Terminux) is not yet active.

### B. Regex Classifier Ambiguity & False Positives
`tests/test_classifier.py` contains documented edge cases of the regex classifier:
- `compose` alone matches `container` (e.g. `compose an email` is classified as `container`).
- `uv` alone matches `python-dev` (e.g. `check uv levels` matches `python-dev`).
- `token` matches `auth` anywhere in a command or output (e.g., `echo token_name` becomes `auth`).

### C. Secret Redaction Completeness
Though we have 8 strong regex patterns (IPs, AWS keys, JWTs, database strings), we don't yet capture:
- Private SSH keys (`-----BEGIN OPENSSH PRIVATE KEY-----`).
- Slack/Discord webhook URLs.
- Generic config values matching `password = ...` with multiline blocks.

### D. User Feedback Loop for Corrections (V1.5 Goal)
The system classifies events automatically, but has no command for the user to override or correct a misclassification (e.g. fixing a `compose an email` event to be `general` instead of `container`).

---

## 4. Proposed Next Steps Plan

To systematically advance Terminux, we suggest prioritizing the roadmap into three distinct phases:

### Phase A: Shell Hooking & Automation (Highly Recommended Immediate Step)
1. **Develop Shell Hooks**: Write shell scripts (for Bash/Zsh) that leverage shell hooks (`PROMPT_COMMAND` in Bash, `preexec`/`precmd` in Zsh) to capture commands, execution duration, and exit codes.
2. **Daemon Enhancement**: Update the Rust daemon to passively receive background signals from these shell hooks, tail the terminal output buffers, and automatically post the details asynchronously to the FastAPI backend.

### Phase B: Strengthen Classifier & Secret Redaction (Low-Hanging Fruit)
1. **Refine Classifier Regular Expressions**:
   - Upgrade `classifier.py` to use stricter word boundaries (e.g., `\buv\b` is good, but check surrounding tokens to ensure it isn't `uv index` or `uv rays`).
   - Add negative lookaheads/lookbehinds to category matching.
2. **Expand Redaction Patterns**: Add SSH key headers and webhooks, and cover common config patterns to guarantee local-first safety.

### Phase C: Implementing User Feedback Loop & Memory Correction (V1.5 Milestones)
1. **API Support for Memory Correction**: Add a `/v1/events/{id}/correction` endpoint to update database records and Qdrant payloads with corrected categories or root causes.
2. **CLI Integration**: Implement `tm correct <event_id> --category <new_category>` to easily fix any automated misclassifications.
3. **Confidence Scoring**: Add a lightweight confidence model/heuristics rating to inferred root causes (e.g., `confidence: High` for exact matching errors like `ModuleNotFoundError`).
