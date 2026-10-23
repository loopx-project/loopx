//! Optional native presentation preference. The frontend still owns routing;
//! this adapter only carries local selection across changing loopback ports.
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs,
    io::{Read, Write},
    path::Path,
};
use tauri::{AppHandle, Manager, Runtime, Url};

#[derive(Debug, Default, PartialEq)]
struct LocalWorkspace {
    goal_id: String,
    view: Option<String>,
    work_agent: Option<String>,
    todo: Option<String>,
}

// Match the existing Goal instance ID and frontend Agent/Todo search boundary.
// These are selection identifiers, never paths, commands or execution grants.
fn selection_id(value: &str) -> Option<String> {
    (!value.is_empty()
        && value.len() <= 200
        && value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"._:-".contains(&c)))
    .then(|| value.to_owned())
}

impl LocalWorkspace {
    fn selections(goal: &str, view: &str, agent: &str, todo: &str) -> Option<Self> {
        if goal.is_empty() {
            return Some(Self::default());
        }
        Some(Self {
            goal_id: selection_id(goal)?,
            // Existing local presentation vocabulary, owned by dashboard's
            // router.tsx. Unknown/retired views use the frontend's default.
            view: matches!(
                view,
                "conversation" | "overview" | "tasks" | "research" | "files"
            )
            .then(|| view.to_owned()),
            work_agent: selection_id(agent),
            todo: selection_id(todo),
        })
    }

    fn capture(current: &Url, origin: &Url) -> Option<Self> {
        if current.origin() != origin.origin()
            || current.path() != "/chat/"
            || !current.username().is_empty()
            || current.password().is_some()
        {
            // Boot/error pages and foreign navigation must not erase a good
            // local preference when the workspace has not committed yet.
            return None;
        }
        let mut query = BTreeMap::new();
        for (key, value) in current.query_pairs().into_owned() {
            // Do not reinterpret an ambiguous source/selection differently
            // from the frontend's search parser.
            if query.insert(key, value).is_some() {
                return None;
            }
        }
        let get = |key: &str| query.get(key).map(String::as_str).unwrap_or("");
        if !get("workspace").is_empty() || !local_status(get("statusUrl"), origin) {
            // A remote source's Goal/Agent must not be replayed against local
            // authority. Reopening that unsupported context returns Manager.
            return Some(Self::default());
        }
        Self::selections(get("goalId"), get("view"), get("workAgent"), get("todo"))
    }

    fn destination(&self, origin: &Url) -> Url {
        let mut destination = origin.clone();
        destination.set_query(None);
        destination.set_fragment(None);
        let mut query = destination.query_pairs_mut();
        query.append_pair("goalId", &self.goal_id);
        query.append_pair("statusUrl", "/status.json");
        for (key, value) in [
            ("view", &self.view),
            ("workAgent", &self.work_agent),
            ("todo", &self.todo),
        ] {
            if let Some(value) = value {
                query.append_pair(key, value);
            }
        }
        drop(query);
        destination
    }

    fn from_value(value: &Value) -> Option<Self> {
        if value["version"] != 1 {
            return None;
        }
        Self::selections(
            value["goal_id"].as_str()?,
            value["view"].as_str().unwrap_or(""),
            value["work_agent"].as_str().unwrap_or(""),
            value["todo"].as_str().unwrap_or(""),
        )
    }

    fn value(&self) -> Value {
        json!({"version": 1, "goal_id": self.goal_id, "view": self.view,
            "work_agent": self.work_agent, "todo": self.todo})
    }
}

fn local_status(value: &str, origin: &Url) -> bool {
    if value.is_empty() {
        return true;
    }
    origin.join(value).is_ok_and(|url| {
        url.origin() == origin.origin()
            && url.path() == "/status.json"
            && url.query().is_none()
            && url.fragment().is_none()
            && url.username().is_empty()
            && url.password().is_none()
    })
}

fn read(path: &Path) -> Option<LocalWorkspace> {
    let mut bytes = Vec::new();
    fs::File::open(path)
        .ok()?
        .take(4097)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() > 4096 {
        return None;
    }
    LocalWorkspace::from_value(&serde_json::from_slice::<Value>(&bytes).ok()?)
}

fn write(path: &Path, selection: &LocalWorkspace) -> std::io::Result<()> {
    if read(path).as_ref() == Some(selection) {
        return Ok(());
    }
    let directory = path
        .parent()
        .ok_or_else(|| std::io::Error::other("missing preference directory"))?;
    fs::create_dir_all(directory)?;
    let mut file = tempfile::NamedTempFile::new_in(directory)?;
    file.write_all(selection.value().to_string().as_bytes())?;
    file.as_file().sync_all()?;
    file.persist(path)?;
    Ok(())
}

pub(crate) fn launch_destination(app: &AppHandle, origin: &Url) -> Url {
    // Development keeps its existing Vite handoff. An optional preference can
    // never block startup, change runtime selection or choose a foreign origin.
    if origin.path() == "/chat/" {
        if let Some(selection) = app
            .path()
            .app_local_data_dir()
            .ok()
            .and_then(|directory| read(&directory.join("workspace-location.json")))
        {
            return selection.destination(origin);
        }
    }
    origin.clone()
}

pub(crate) fn remember_current<R: Runtime>(app: &AppHandle<R>) {
    let Some(handoff) = app
        .try_state::<std::sync::Arc<std::sync::Mutex<crate::workspace_handoff::WorkspaceHandoff>>>(
        )
    else {
        return;
    };
    // Read the native WebView once at close/quit, including SPA history changes.
    // No page script, per-navigation disk writes, URL polling or new IPC grant.
    let Some(current) = app
        .get_webview_window("main")
        .and_then(|window| window.url().ok())
    else {
        return;
    };
    let origin = handoff
        .lock()
        .expect("workspace handoff lock")
        .origin()
        .clone();
    let Some(selection) = LocalWorkspace::capture(&current, &origin) else {
        return;
    };
    let result = app
        .path()
        .app_local_data_dir()
        .ok()
        .map(|directory| write(&directory.join("workspace-location.json"), &selection));
    if !matches!(result, Some(Ok(()))) {
        eprintln!("LoopX could not save the local workspace selection; startup remains available");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn origin(port: u16) -> Url {
        format!("http://127.0.0.1:{port}/chat/").parse().unwrap()
    }

    #[test]
    fn ordinary_reopen_keeps_selection_across_ports_without_replaying_other_inputs() {
        let old = origin(49123);
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("app/workspace-location.json");
        for view in ["conversation", "overview", "tasks", "research", "files"] {
            let mut page = old.clone();
            page.query_pairs_mut().extend_pairs([
                ("goalId", "example"),
                ("view", view),
                ("workAgent", "worker"),
                ("todo", "todo_123"),
                ("statusUrl", "/status.json"),
                ("token", "secret"),
                ("prompt", "run"),
                ("reportSessionId", "report"),
            ]);
            page.set_fragment(Some("secret"));
            let selection = LocalWorkspace::capture(&page, &old).unwrap();
            write(&path, &selection).unwrap();
            let reopened = read(&path).unwrap().destination(&origin(59234));
            assert_eq!(reopened.as_str(), format!("http://127.0.0.1:59234/chat/?goalId=example&statusUrl=%2Fstatus.json&view={view}&workAgent=worker&todo=todo_123"));
            let stored = fs::read_to_string(&path).unwrap();
            for excluded in ["secret", "49123", "prompt", "report", "token", "http"] {
                assert!(!stored.contains(excluded), "{view}: {excluded}");
            }
        }
    }

    #[test]
    fn boot_foreign_and_non_workspace_pages_do_not_replace_the_local_selection() {
        let local = origin(49123);
        for page in [
            "tauri://localhost/",
            "about:blank",
            "https://example.org/chat/?goalId=foreign",
            "http://127.0.0.1:49124/chat/?goalId=other",
            "http://127.0.0.1:49123/diagnostics",
            "http://user:secret@127.0.0.1:49123/chat/?goalId=example",
            "http://127.0.0.1:49123/chat/?goalId=first&goalId=second",
            "http://127.0.0.1:49123/chat/?goalId=remote&statusUrl=https%3A%2F%2Fexample.org%2Fstatus.json&statusUrl=%2Fstatus.json",
        ] {
            assert_eq!(
                LocalWorkspace::capture(&page.parse().unwrap(), &local),
                None,
                "{page}"
            );
        }
    }

    #[test]
    fn remote_source_and_explicit_manager_return_do_not_replay_a_goal_against_local_authority() {
        let local = origin(49123);
        for query in [
            "?goalId=remote&statusUrl=https%3A%2F%2Fexample.org%2Fstatus.json",
            "?goalId=remote&statusUrl=http%3A%2F%2F127.0.0.1%3A8766%2Fstatus.json",
            "?goalId=remote&statusUrl=%2Fstatus.json%3Ftoken%3Dsecret",
            "?goalId=remote&workspace=remoteHost",
            "?goalId=&workAgent=worker&view=tasks",
        ] {
            assert_eq!(
                LocalWorkspace::capture(&local.join(query).unwrap(), &local),
                Some(LocalWorkspace::default()),
                "{query}"
            );
        }
        assert!(local_status("http://127.0.0.1:49123/status.json", &local));
        assert!(local_status("/status.json", &local));
    }

    #[test]
    fn missing_corrupt_obsolete_and_oversized_preferences_cannot_block_launch() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("workspace-location.json");
        assert_eq!(read(&path), None);
        for bytes in [
            "broken".into(),
            "{}".into(),
            "{\"version\":2,\"goal_id\":\"example\"}".into(),
            "x".repeat(4097),
        ] {
            fs::write(&path, bytes).unwrap();
            assert_eq!(read(&path), None);
        }
        let valid = LocalWorkspace::selections("example", "tasks", "worker", "todo_123").unwrap();
        write(&path, &valid).unwrap();
        assert_eq!(read(&path), Some(valid));
        // A directory/unwritable destination remains a contained optional error.
        assert!(write(directory.path(), &LocalWorkspace::default()).is_err());
    }

    #[test]
    fn invalid_or_retired_selections_never_produce_a_router_error() {
        let value = json!({"version": 1, "goal_id": "example", "view": "retired-view", "work_agent": "../worker", "todo": "x".repeat(201), "url": "https://example.org"});
        let selection = LocalWorkspace::from_value(&value).unwrap();
        assert_eq!(
            selection.destination(&origin(49123)).as_str(),
            "http://127.0.0.1:49123/chat/?goalId=example&statusUrl=%2Fstatus.json"
        );
        for goal in ["../example", "goal with whitespace", "secret\nvalue"] {
            assert_eq!(LocalWorkspace::selections(goal, "overview", "", ""), None);
        }
        for view in ["overview", "conversation", "tasks", "files"] {
            assert_eq!(
                LocalWorkspace::selections("example", view, "", "")
                    .unwrap()
                    .view
                    .as_deref(),
                Some(view)
            );
        }
    }
}
