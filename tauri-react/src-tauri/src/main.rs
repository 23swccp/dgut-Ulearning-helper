#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::{json, Value};
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::{Shutdown, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, Manager, State};

const SIDECAR_PORT: u16 = 18473;

struct BackendProcess { _child: Child }
struct BackendState { process: Mutex<Option<BackendProcess>> }
impl Default for BackendState { fn default() -> Self { Self { process: Mutex::new(None) } } }

fn bridge_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).parent().expect("src-tauri 必须位于项目根目录").join("sidecar").join("bridge.py")
}

fn start_backend(app: &AppHandle, state: &BackendState) -> Result<(), String> {
    let mut process = state.process.lock().map_err(|_| "后端进程锁定失败".to_string())?;
    if process.is_some() { return Ok(()); }
    let mut command = if cfg!(debug_assertions) {
        let mut command = Command::new("python"); command.arg(bridge_path()); command
    } else {
        Command::new(app.path().resource_dir().map_err(|error| error.to_string())?.join("yxy-sidecar.exe"))
    };
    let mut child = command.arg("--server").arg(SIDECAR_PORT.to_string())
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().map_err(|error| format!("无法启动 Python 后端：{error}"))?;
    let stdout = child.stdout.take().ok_or("无法连接 Python 后端输出")?;
    let stderr = child.stderr.take().ok_or("无法连接 Python 后端错误输出")?;
    let log_app = app.clone();
    thread::spawn(move || for line in BufReader::new(stdout).lines().map_while(Result::ok) {
        match serde_json::from_str::<Value>(&line) {
            Ok(message) if message["type"] == "log" => { let _ = log_app.emit("backend-log", message); }
            _ => { let _ = log_app.emit("backend-log", json!({"message": line, "kind": "muted"})); }
        }
    });
    let error_app = app.clone();
    thread::spawn(move || for line in BufReader::new(stderr).lines().map_while(Result::ok) {
        let _ = error_app.emit("backend-log", json!({"message": format!("[Python] {line}"), "kind": "warn"}));
    });
    *process = Some(BackendProcess { _child: child });
    Ok(())
}

fn request(command: &str, payload: Value) -> Result<Value, String> {
    // PyInstaller 单文件 sidecar 首次启动需要解压，Windows 上通常需数秒。
    let deadline = Instant::now() + Duration::from_secs(15);
    let mut stream = loop {
        match TcpStream::connect(("127.0.0.1", SIDECAR_PORT)) {
            Ok(stream) => break stream,
            Err(error) if Instant::now() < deadline => { thread::sleep(Duration::from_millis(80)); let _ = error; }
            Err(error) => return Err(format!("无法连接 Python 后端：{error}")),
        }
    };
    stream.set_read_timeout(Some(Duration::from_secs(35))).map_err(|error| error.to_string())?;
    let body = serde_json::to_vec(&json!({"command": command, "payload": payload})).map_err(|error| error.to_string())?;
    stream.write_all(&body).and_then(|_| stream.shutdown(Shutdown::Write)).map_err(|error| format!("发送命令失败：{error}"))?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).map_err(|error| format!("读取后端响应失败：{error}"))?;
    serde_json::from_slice(&response).map_err(|error| format!("后端响应无效：{error}"))
}

#[tauri::command]
fn backend_command(app: AppHandle, state: State<'_, BackendState>, command: String, payload: Value) -> Result<Value, String> {
    start_backend(&app, &state)?;
    request(&command, payload)
}

fn main() {
    tauri::Builder::default().manage(BackendState::default())
        .invoke_handler(tauri::generate_handler![backend_command])
        .run(tauri::generate_context!()).expect("error while running 优学院签到助手");
}
