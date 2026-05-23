use std::env;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use reqwest::blocking::Client;
use rusqlite::Connection;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// CLI definition
// ---------------------------------------------------------------------------
#[derive(Parser, Debug)]
#[command(author, version, about = "Terminux capture daemon")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Emit one terminal event to the Terminux API
    Emit {
        #[arg(long)]
        command: String,
        #[arg(long)]
        cwd: Option<String>,
        #[arg(long, default_value = "")]
        output: String,
        #[arg(long, default_value_t = 0)]
        exit_code: i32,
        #[arg(long)]
        duration_ms: Option<i64>,
    },
    /// Read a JSON event payload from stdin and send it to the API
    ReadJson,
    /// Read command output from file then emit an event
    EmitFromFile {
        #[arg(long)]
        command: String,
        #[arg(long)]
        output_file: PathBuf,
        #[arg(long)]
        cwd: Option<String>,
        #[arg(long, default_value_t = 0)]
        exit_code: i32,
        #[arg(long)]
        duration_ms: Option<i64>,
    },
    /// Run the background daemon that accepts events via Unix socket
    /// and forwards them to the Terminux API with retry
    Daemon,
}

// ---------------------------------------------------------------------------
// Event payload (unchanged wire format)
// ---------------------------------------------------------------------------
#[derive(Debug, Serialize, Deserialize, Clone)]
struct EventPayload {
    command: String,
    cwd: String,
    output: String,
    exit_code: i32,
    duration_ms: Option<i64>,
    timestamp: DateTime<Utc>,
}

// ---------------------------------------------------------------------------
// Path helpers
// ---------------------------------------------------------------------------
fn terminux_dir() -> PathBuf {
    let home = env::var("HOME").unwrap_or_else(|_| "/tmp".to_owned());
    PathBuf::from(home).join(".terminux")
}

fn socket_path() -> PathBuf {
    env::var("TERMINUX_SOCK")
        .map(PathBuf::from)
        .unwrap_or_else(|_| terminux_dir().join("terminux.sock"))
}

fn retry_db_path() -> PathBuf {
    terminux_dir().join("retry_queue.db")
}

fn default_api_url() -> String {
    env::var("TERMINUX_API_URL").unwrap_or_else(|_| "http://127.0.0.1:8000".to_owned())
}

fn current_cwd() -> String {
    env::current_dir()
        .ok()
        .and_then(|dir| dir.into_os_string().into_string().ok())
        .unwrap_or_else(|| ".".to_owned())
}

// ---------------------------------------------------------------------------
// SQLite-backed retry queue
// ---------------------------------------------------------------------------
struct RetryQueue {
    conn: Connection,
}

impl RetryQueue {
    fn open(path: &PathBuf) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("failed to create retry queue dir: {e}"))?;
        }
        let conn = Connection::open(path)
            .map_err(|e| format!("failed to open retry queue db: {e}"))?;

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS pending_events (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                payload_json TEXT NOT NULL,
                created_at   TEXT NOT NULL DEFAULT (datetime('now')),
                attempts     INTEGER NOT NULL DEFAULT 0
            );",
        )
        .map_err(|e| format!("failed to init retry queue schema: {e}"))?;

        Ok(Self { conn })
    }

    fn enqueue(&self, payload: &EventPayload) -> Result<(), String> {
        let json = serde_json::to_string(payload)
            .map_err(|e| format!("serialize error: {e}"))?;
        self.conn
            .execute(
                "INSERT INTO pending_events (payload_json) VALUES (?1)",
                [&json],
            )
            .map_err(|e| format!("enqueue error: {e}"))?;
        Ok(())
    }

    /// Drain up to `batch` rows, posting each to the API.
    /// Returns the number of events successfully forwarded.
    fn drain(&self, batch: usize) -> usize {
        let mut stmt = match self.conn.prepare(
            "SELECT id, payload_json FROM pending_events ORDER BY id ASC LIMIT ?1",
        ) {
            Ok(s) => s,
            Err(_) => return 0,
        };

        let rows: Vec<(i64, String)> = stmt
            .query_map([batch as i64], |row| {
                Ok((row.get::<_, i64>(0)?, row.get::<_, String>(1)?))
            })
            .ok()
            .map(|iter| iter.filter_map(|r| r.ok()).collect())
            .unwrap_or_default();

        let mut forwarded = 0;
        for (id, json) in &rows {
            let payload: EventPayload = match serde_json::from_str(json) {
                Ok(p) => p,
                Err(_) => {
                    // Corrupt row – delete it so it doesn't block the queue
                    let _ = self.conn.execute(
                        "DELETE FROM pending_events WHERE id = ?1",
                        [id],
                    );
                    continue;
                }
            };

            match post_event(&payload) {
                Ok(()) => {
                    let _ = self.conn.execute(
                        "DELETE FROM pending_events WHERE id = ?1",
                        [id],
                    );
                    forwarded += 1;
                }
                Err(_) => {
                    // API still down – bump attempt counter and stop
                    let _ = self.conn.execute(
                        "UPDATE pending_events SET attempts = attempts + 1 WHERE id = ?1",
                        [id],
                    );
                    break;
                }
            }
        }
        forwarded
    }

    fn pending_count(&self) -> usize {
        self.conn
            .query_row("SELECT COUNT(*) FROM pending_events", [], |row| {
                row.get::<_, usize>(0)
            })
            .unwrap_or(0)
    }
}

// ---------------------------------------------------------------------------
// HTTP posting (unchanged logic)
// ---------------------------------------------------------------------------
fn post_event(payload: &EventPayload) -> Result<(), String> {
    let api_url = default_api_url();
    let url = format!("{api_url}/v1/events");

    let client = Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .map_err(|e| format!("client build error: {e}"))?;

    let response = client
        .post(url)
        .json(payload)
        .send()
        .map_err(|err| format!("request failed: {err}"))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response
            .text()
            .unwrap_or_else(|_| "<failed to read body>".to_owned());
        return Err(format!("api returned {status}: {body}"));
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// Payload builder (unchanged)
// ---------------------------------------------------------------------------
fn build_payload(
    command: String,
    cwd: Option<String>,
    output: String,
    exit_code: i32,
    duration_ms: Option<i64>,
) -> EventPayload {
    EventPayload {
        command,
        cwd: cwd.unwrap_or_else(current_cwd),
        output,
        exit_code,
        duration_ms,
        timestamp: Utc::now(),
    }
}

// ---------------------------------------------------------------------------
// Client-side: send event via Unix socket (fast path) or fall back to HTTP
// ---------------------------------------------------------------------------
fn send_via_socket(payload: &EventPayload) -> Result<(), String> {
    let sock = socket_path();
    let mut stream = UnixStream::connect(&sock)
        .map_err(|e| format!("socket connect: {e}"))?;
    stream
        .set_write_timeout(Some(Duration::from_millis(500)))
        .ok();

    let json = serde_json::to_string(payload)
        .map_err(|e| format!("serialize: {e}"))?;

    // Protocol: one JSON object per line, terminated by newline
    let msg = format!("{json}\n");
    stream
        .write_all(msg.as_bytes())
        .map_err(|e| format!("socket write: {e}"))?;
    stream.flush().map_err(|e| format!("socket flush: {e}"))?;
    Ok(())
}

/// Try the daemon socket first; if that fails, post directly via HTTP.
fn send_event(payload: &EventPayload) -> Result<(), String> {
    // Fast path: write to daemon socket
    if send_via_socket(payload).is_ok() {
        return Ok(());
    }

    // Fallback: direct HTTP POST (ensures backward compat & test compat)
    post_event(payload)
}

// ---------------------------------------------------------------------------
// Daemon: Unix socket listener + background retry drain
// ---------------------------------------------------------------------------
fn run_daemon() -> Result<(), String> {
    let sock = socket_path();
    let db_path = retry_db_path();

    // Ensure directory exists
    if let Some(parent) = sock.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("mkdir: {e}"))?;
    }

    // Remove stale socket file
    if sock.exists() {
        fs::remove_file(&sock).ok();
    }

    let listener = UnixListener::bind(&sock)
        .map_err(|e| format!("bind {}: {e}", sock.display()))?;

    eprintln!("terminux-daemon listening on {}", sock.display());

    // Channel to notify the drain thread when new events arrive
    let (notify_tx, notify_rx) = mpsc::channel::<()>();

    // Background drain thread
    let drain_db_path = db_path.clone();
    thread::spawn(move || {
        loop {
            // Wait for a notification or a periodic timeout (10 s)
            let _ = notify_rx.recv_timeout(Duration::from_secs(10));

            let queue = match RetryQueue::open(&drain_db_path) {
                Ok(q) => q,
                Err(e) => {
                    eprintln!("drain: queue open error: {e}");
                    continue;
                }
            };

            let pending = queue.pending_count();
            if pending > 0 {
                let sent = queue.drain(50);
                if sent > 0 {
                    eprintln!("drain: forwarded {sent}/{pending} queued events");
                }
            }
        }
    });

    // Accept connections (each connection can send multiple newline-delimited events)
    for stream in listener.incoming() {
        let stream = match stream {
            Ok(s) => s,
            Err(e) => {
                eprintln!("accept error: {e}");
                continue;
            }
        };

        let notify = notify_tx.clone();
        let conn_db_path = db_path.clone();

        thread::spawn(move || {
            handle_connection(stream, &conn_db_path, &notify);
        });
    }

    Ok(())
}

fn handle_connection(stream: UnixStream, db_path: &PathBuf, notify: &mpsc::Sender<()>) {
    let reader = BufReader::new(stream);
    for line in reader.lines() {
        let line = match line {
            Ok(l) if !l.trim().is_empty() => l,
            _ => continue,
        };

        let payload: EventPayload = match serde_json::from_str(&line) {
            Ok(p) => p,
            Err(e) => {
                eprintln!("bad payload: {e}");
                continue;
            }
        };

        // Try forwarding to API immediately
        match post_event(&payload) {
            Ok(()) => {}
            Err(_e) => {
                // API unreachable – enqueue for retry
                if let Ok(queue) = RetryQueue::open(db_path) {
                    if let Err(e) = queue.enqueue(&payload) {
                        eprintln!("enqueue error: {e}");
                    } else {
                        let _ = notify.send(());
                    }
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------
fn main() {
    let cli = Cli::parse();

    let result = match cli.command {
        Commands::Emit {
            command,
            cwd,
            output,
            exit_code,
            duration_ms,
        } => {
            let payload = build_payload(command, cwd, output, exit_code, duration_ms);
            send_event(&payload)
        }
        Commands::ReadJson => {
            let mut input = String::new();
            io::stdin()
                .read_to_string(&mut input)
                .map_err(|err| format!("failed to read stdin: {err}"))
                .and_then(|_| {
                    serde_json::from_str::<EventPayload>(&input)
                        .map_err(|err| format!("invalid json payload: {err}"))
                })
                .and_then(|payload| send_event(&payload))
        }
        Commands::EmitFromFile {
            command,
            output_file,
            cwd,
            exit_code,
            duration_ms,
        } => fs::read_to_string(output_file)
            .map_err(|err| format!("failed to read output file: {err}"))
            .and_then(|output| {
                let payload = build_payload(command, cwd, output, exit_code, duration_ms);
                send_event(&payload)
            }),
        Commands::Daemon => run_daemon(),
    };

    if let Err(err) = result {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}
