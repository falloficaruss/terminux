use std::collections::HashMap;
use std::env;
use std::fs;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
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
    /// Send raw JSON to the daemon socket (for shell hook start/end protocol)
    Send,
    /// Passively watch /proc for terminal activity (no shell hooks needed)
    Watch {
        /// Poll interval in milliseconds (default: 1000)
        #[arg(long, default_value_t = 1000)]
        poll_ms: u64,
    },
    /// Run the background daemon that accepts events via Unix socket
    /// and forwards them to the Terminux API with retry
    Daemon,
}

// ---------------------------------------------------------------------------
// Event payload (wire format to the backend)
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
// Socket protocol: start/end messages from shell hooks
// ---------------------------------------------------------------------------
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "type")]
enum SocketMessage {
    #[serde(rename = "start")]
    Start {
        seq: u64,
        command: String,
        cwd: String,
        timestamp: DateTime<Utc>,
    },
    #[serde(rename = "end")]
    End {
        seq: u64,
        exit_code: i32,
        duration_ms: Option<i64>,
    },
}

#[derive(Debug)]
struct StartInfo {
    command: String,
    cwd: String,
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
                    let _ = self
                        .conn
                        .execute("DELETE FROM pending_events WHERE id = ?1", [id]);
                    continue;
                }
            };

            match post_event(&payload) {
                Ok(()) => {
                    let _ = self
                        .conn
                        .execute("DELETE FROM pending_events WHERE id = ?1", [id]);
                    forwarded += 1;
                }
                Err(_) => {
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
// HTTP posting
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
// Payload builder
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
// Client-side send helpers
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

    let msg = format!("{json}\n");
    stream
        .write_all(msg.as_bytes())
        .map_err(|e| format!("socket write: {e}"))?;
    stream.flush().map_err(|e| format!("socket flush: {e}"))?;
    Ok(())
}

fn send_raw_via_socket(json: &str) -> Result<(), String> {
    let sock = socket_path();
    let mut stream = UnixStream::connect(&sock)
        .map_err(|e| format!("socket connect: {e}"))?;
    stream
        .set_write_timeout(Some(Duration::from_millis(500)))
        .ok();

    let msg = format!("{json}\n");
    stream
        .write_all(msg.as_bytes())
        .map_err(|e| format!("socket write: {e}"))?;
    stream.flush().map_err(|e| format!("socket flush: {e}"))?;
    Ok(())
}

fn send_event(payload: &EventPayload) -> Result<(), String> {
    if send_via_socket(payload).is_ok() {
        return Ok(());
    }
    post_event(payload)
}

// ---------------------------------------------------------------------------
// Daemon: forward an assembled payload or enqueue for retry
// ---------------------------------------------------------------------------
fn forward_or_enqueue(
    payload: &EventPayload,
    db_path: &PathBuf,
    notify: &mpsc::Sender<()>,
) {
    match post_event(payload) {
        Ok(()) => {}
        Err(_e) => {
            if let Ok(queue) = RetryQueue::open(db_path) {
                if let Err(e) = queue.enqueue(payload) {
                    eprintln!("enqueue error: {e}");
                } else {
                    let _ = notify.send(());
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Daemon: Unix socket listener + background retry drain
// ---------------------------------------------------------------------------
fn run_daemon() -> Result<(), String> {
    let sock = socket_path();
    let db_path = retry_db_path();

    if let Some(parent) = sock.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("mkdir: {e}"))?;
    }

    if sock.exists() {
        fs::remove_file(&sock).ok();
    }

    let listener = UnixListener::bind(&sock)
        .map_err(|e| format!("bind {}: {e}", sock.display()))?;

    eprintln!("terminux-daemon listening on {}", sock.display());

    let (notify_tx, notify_rx) = mpsc::channel::<()>();

    // Background drain thread
    let drain_db_path = db_path.clone();
    thread::spawn(move || {
        loop {
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
    let mut pending_starts: HashMap<u64, StartInfo> = HashMap::new();

    for line in reader.lines() {
        let line = match line {
            Ok(l) if !l.trim().is_empty() => l,
            _ => continue,
        };

        if let Ok(msg) = serde_json::from_str::<SocketMessage>(&line) {
            match msg {
                SocketMessage::Start {
                    seq,
                    command,
                    cwd,
                    timestamp,
                } => {
                    pending_starts.insert(
                        seq,
                        StartInfo {
                            command,
                            cwd,
                            timestamp,
                        },
                    );
                }
                SocketMessage::End {
                    seq,
                    exit_code,
                    duration_ms,
                } => {
                    if let Some(start) = pending_starts.remove(&seq) {
                        let payload = EventPayload {
                            command: start.command,
                            cwd: start.cwd,
                            output: String::new(),
                            exit_code,
                            duration_ms,
                            timestamp: start.timestamp,
                        };
                        forward_or_enqueue(&payload, db_path, notify);
                    }
                }
            }
            continue;
        }

        if let Ok(payload) = serde_json::from_str::<EventPayload>(&line) {
            forward_or_enqueue(&payload, db_path, notify);
            continue;
        }

        eprintln!("bad payload: unrecognized message format");
    }
}

// ---------------------------------------------------------------------------
// Watch mode: passive /proc monitoring (no shell hooks needed)
// ---------------------------------------------------------------------------

/// Shell command names to exclude from watch tracking (they're sessions, not commands).
const SHELL_COMMS: &[&str] = &["bash", "sh", "zsh", "fish", "dash", "ksh", "mksh", "elvish"];

#[derive(Debug, Clone)]
struct ProcInfo {
    pid: u32,
    cmdline: String,
    cwd: String,
}

#[derive(Debug, Clone)]
struct TrackedProc {
    cmdline: String,
    cwd: String,
    detected_at: DateTime<Utc>,
}

/// Parse /proc/PID/stat for comm and tty_nr.
/// Format: pid (comm) state ppid pgrp session tty_nr ...
fn parse_stat(content: &str) -> Option<(String, u32)> {
    let after_pid = content.find(' ')?;
    let rest = &content[after_pid + 1..];

    let comm_start = rest.find('(')?;
    let comm_end = rest.rfind(')')?;
    let comm = rest[comm_start + 1..comm_end].to_string();

    let after_comm = &rest[comm_end + 2..];
    let mut fields = after_comm.split_whitespace();

    let _state = fields.next()?;
    let _ppid: u32 = fields.next()?.parse().ok()?;
    let _pgrp: u32 = fields.next()?.parse().ok()?;
    let _session: u32 = fields.next()?.parse().ok()?;
    let tty_nr: u32 = fields.next()?.parse().ok()?;

    Some((comm, tty_nr))
}

/// Read cmdline from /proc/PID/cmdline (null-separated args → space-joined).
fn read_cmdline(path: &Path) -> String {
    fs::read(path)
        .ok()
        .map(|bytes| {
            bytes
                .split(|&b| b == 0)
                .filter(|s| !s.is_empty())
                .map(|s| String::from_utf8_lossy(s).to_string())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default()
}

/// Read cwd from /proc/PID/cwd symlink.
fn read_cwd(pid: u32) -> String {
    let path = PathBuf::from(format!("/proc/{pid}/cwd"));
    fs::read_link(&path)
        .ok()
        .and_then(|p| p.into_os_string().into_string().ok())
        .unwrap_or_else(|| ".".to_string())
}

/// Scan /proc for processes with a controlling terminal, excluding known shells.
fn scan_procs() -> Vec<ProcInfo> {
    let mut result = Vec::new();

    let dir = match fs::read_dir("/proc") {
        Ok(d) => d,
        Err(_) => return result,
    };

    for entry in dir.flatten() {
        let name = entry.file_name();
        let name_str = name.to_string_lossy();
        let pid: u32 = match name_str.parse() {
            Ok(n) => n,
            Err(_) => continue,
        };

        let stat_path = entry.path().join("stat");
        let stat_content = match fs::read_to_string(&stat_path) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let Some((comm, tty)) = parse_stat(&stat_content) else {
            continue;
        };

        // Skip processes without a controlling terminal
        if tty == 0 {
            continue;
        }

        // Skip known shells (they are sessions, not commands)
        if SHELL_COMMS.contains(&comm.as_str()) {
            continue;
        }

        let cmdline = read_cmdline(&entry.path().join("cmdline"));
        if cmdline.is_empty() && !comm.is_empty() {
            // Fallback to comm name if cmdline is empty (e.g. kernel threads or zombies)
            continue;
        }

        let cwd = read_cwd(pid);

        result.push(ProcInfo {
            pid,
            cmdline,
            cwd,
        });
    }

    result
}

/// Run the watch loop: poll /proc, track process lifetimes, post events on exit.
fn run_watch(poll_ms: u64) -> Result<(), String> {
    eprintln!(
        "terminux-daemon watch started (polling /proc every {poll_ms}ms)"
    );

    let mut tracked: HashMap<u32, TrackedProc> = HashMap::new();

    loop {
        let snapshot = scan_procs();

        // -- detect completed processes ---------------------------------------
        let mut completed: Vec<(u32, TrackedProc)> = Vec::new();
        tracked.retain(|pid, info| {
            let alive = snapshot.iter().any(|p| p.pid == *pid);
            if !alive {
                completed.push((*pid, info.clone()));
            }
            alive
        });

        let now = Utc::now();
        for (_pid, info) in &completed {
            let elapsed = now.signed_duration_since(info.detected_at);
            let duration_ms = elapsed.num_milliseconds().max(0);

            let payload = EventPayload {
                command: info.cmdline.clone(),
                cwd: info.cwd.clone(),
                output: String::new(),
                exit_code: -1,
                duration_ms: Some(duration_ms),
                timestamp: info.detected_at,
            };

            if let Err(e) = post_event(&payload) {
                eprintln!("watch: post error: {e}");
            }
        }

        // -- track new processes ----------------------------------------------
        for proc in &snapshot {
            tracked.entry(proc.pid).or_insert_with(|| TrackedProc {
                cmdline: proc.cmdline.clone(),
                cwd: proc.cwd.clone(),
                detected_at: now,
            });
        }

        thread::sleep(Duration::from_millis(poll_ms));
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
        Commands::Send => {
            let mut input = String::new();
            io::stdin()
                .read_to_string(&mut input)
                .map_err(|err| format!("read stdin: {err}"))
                .and_then(|_| {
                    let json = input.trim();
                    if json.is_empty() {
                        return Err("empty payload".into());
                    }
                    serde_json::from_str::<serde_json::Value>(json)
                        .map_err(|e| format!("invalid json: {e}"))
                })
                .and_then(|_| send_raw_via_socket(input.trim()))
        }
        Commands::Watch { poll_ms } => run_watch(poll_ms),
        Commands::Daemon => run_daemon(),
    };

    if let Err(err) = result {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}
