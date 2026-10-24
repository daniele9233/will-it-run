//! Will It Run desktop shell.
//!
//! The shell owns one job: start the Will It Run core (the Python service that does
//! hardware analysis, LM Studio integration and LangGraph execution), tell the UI
//! where it is listening, and stop it when the window closes.
//!
//! The core binds to 127.0.0.1 on a free port and requires a bearer token that is
//! generated here for each launch, so other local processes cannot drive it.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

/// Line the core prints once its HTTP server is about to accept connections.
const READY_PREFIX: &str = "AGENTFIT_READY port=";

#[derive(Default)]
struct Backend {
    port: u16,
    token: String,
    error: Option<String>,
    child: Option<Child>,
}

type SharedBackend = Arc<Mutex<Backend>>;

#[derive(Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
    error: Option<String>,
}

/// Port and token of the core. `port` is 0 until the core has started.
#[tauri::command]
fn backend_info(state: State<'_, SharedBackend>) -> BackendInfo {
    let backend = state.lock().expect("backend state poisoned");
    BackendInfo {
        port: backend.port,
        token: backend.token.clone(),
        error: backend.error.clone(),
    }
}

/// Command line that starts the core.
///
/// Release builds run the bundled executable from the app's resources. Debug
/// builds run the module from the repository's virtual environment so Python
/// changes take effect without rebuilding anything.
fn core_command(app: &tauri::AppHandle) -> Result<Command, String> {
    if cfg!(debug_assertions) {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..");
        let python = repo
            .join("backend")
            .join(".venv")
            .join("Scripts")
            .join("python.exe");
        if !python.exists() {
            return Err(format!(
                "Development Python environment not found at {}. Run: uv venv backend/.venv && uv pip install -e backend[dev]",
                python.display()
            ));
        }
        let mut command = Command::new(python);
        command.args(["-m", "agentfit"]);
        return Ok(command);
    }
    let resources = app
        .path()
        .resource_dir()
        .map_err(|e| format!("Cannot locate application resources: {e}"))?;
    let exe = resources.join("willitrun-core").join("willitrun-core.exe");
    if !exe.exists() {
        return Err(format!(
            "Will It Run core is missing from the installation ({}). Reinstall Will It Run.",
            exe.display()
        ));
    }
    Ok(Command::new(exe))
}

fn spawn_core(app: &tauri::AppHandle, state: SharedBackend) {
    let token = uuid::Uuid::new_v4().simple().to_string();
    state.lock().expect("backend state poisoned").token = token.clone();

    let mut command = match core_command(app) {
        Ok(command) => command,
        Err(message) => {
            state.lock().expect("backend state poisoned").error = Some(message);
            return;
        }
    };
    command
        .args(["serve", "--port", "0"])
        // The token travels in the environment, not on the command line, so it does
        // not show up in process listings.
        .env("AGENTFIT_TOKEN", &token)
        // Lets the core notice if this process dies without stopping it.
        .env("AGENTFIT_PARENT_PID", std::process::id().to_string())
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(e) => {
            state.lock().expect("backend state poisoned").error =
                Some(format!("Could not start the Will It Run core: {e}"));
            return;
        }
    };
    let stdout = child.stdout.take();
    state.lock().expect("backend state poisoned").child = Some(child);

    std::thread::spawn(move || {
        let Some(stdout) = stdout else { return };
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            if let Some(port) = line
                .trim()
                .strip_prefix(READY_PREFIX)
                .and_then(|p| p.parse::<u16>().ok())
            {
                state.lock().expect("backend state poisoned").port = port;
            }
        }
        // stdout closed: the core exited. If it never reported a port, startup failed.
        let mut backend = state.lock().expect("backend state poisoned");
        if backend.port == 0 && backend.error.is_none() {
            backend.error =
                Some("The Will It Run core stopped before it finished starting.".into());
        }
    });
}

fn stop_core(state: &SharedBackend) {
    if let Ok(mut backend) = state.lock() {
        if let Some(mut child) = backend.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let state: SharedBackend = Arc::new(Mutex::new(Backend::default()));
    let exit_state = state.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(state.clone())
        .invoke_handler(tauri::generate_handler![backend_info])
        .setup(move |app| {
            spawn_core(app.handle(), state.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Will It Run")
        .run(move |_app, event| {
            if let RunEvent::Exit = event {
                stop_core(&exit_state);
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_line_yields_the_port() {
        let line = "AGENTFIT_READY port=49231";
        let port = line
            .trim()
            .strip_prefix(READY_PREFIX)
            .and_then(|p| p.parse::<u16>().ok());
        assert_eq!(port, Some(49231));
    }

    #[test]
    fn other_output_is_not_mistaken_for_the_ready_line() {
        for line in [
            "",
            "INFO: started",
            "AGENTFIT_READY port=abc",
            "AGENTFIT_READY port=99999999",
        ] {
            let port = line
                .trim()
                .strip_prefix(READY_PREFIX)
                .and_then(|p| p.parse::<u16>().ok());
            assert_eq!(port, None, "line: {line}");
        }
    }

    #[test]
    fn backend_starts_without_port_or_error() {
        let backend = Backend::default();
        assert_eq!(backend.port, 0);
        assert!(backend.error.is_none() && backend.child.is_none());
    }
}
