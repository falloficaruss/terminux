use std::env;
use std::fs;
use std::io::{self, Read};
use std::path::PathBuf;

use chrono::{DateTime, Utc};
use clap::{Parser, Subcommand};
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};

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
}

#[derive(Debug, Serialize, Deserialize)]
struct EventPayload {
    command: String,
    cwd: String,
    output: String,
    exit_code: i32,
    duration_ms: Option<i64>,
    timestamp: DateTime<Utc>,
}

fn default_api_url() -> String {
    env::var("TERMINUX_API_URL")
        .or_else(|_| env::var("TERMINUS_API_URL"))
        .unwrap_or_else(|_| "http://127.0.0.1:8000".to_owned())
}

fn current_cwd() -> String {
    env::current_dir()
        .ok()
        .and_then(|dir| dir.into_os_string().into_string().ok())
        .unwrap_or_else(|| ".".to_owned())
}

fn post_event(payload: &EventPayload) -> Result<(), String> {
    let api_url = default_api_url();
    let url = format!("{api_url}/v1/events");

    let client = Client::new();
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
            post_event(&payload)
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
                .and_then(|payload| post_event(&payload))
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
                post_event(&payload)
            }),
    };

    if let Err(err) = result {
        eprintln!("error: {err}");
        std::process::exit(1);
    }
}
