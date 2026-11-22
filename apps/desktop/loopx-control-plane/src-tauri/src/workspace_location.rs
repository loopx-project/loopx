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
    research: Option<ResearchReading>,
}

/// Selection within the existing dashboard reader, scoped to this local Goal.
#[derive(Debug, PartialEq)]
struct ResearchReading {
    mode: String,
    pack_id: Option<String>,
    chapter: Option<String>,
}

fn digest_id(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}

impl ResearchReading {
    fn selections(mode: &str, pack: &str, chapter: &str) -> Option<Self> {
        if !matches!(mode, "case" | "history") {
            return None;
        }
        let pack_id = digest_id(pack).then(|| pack.to_owned());
        // The frontend resolves this bookmark against the saved study. Carry
        // only its declared chapter/citation grammar, never an arbitrary URL.
        let chapter = pack_id.as_deref().and_then(|pack| {
            let body = chapter.strip_prefix("case-section-")?;
            let prefix = format!("{pack}-");
            let section = body.strip_prefix(&prefix).or_else(|| {
                let (namespace, rest) = body.split_once('-')?;
                digest_id(namespace).then_some(rest)?.strip_prefix(&prefix)
            })?;
            let (id, citation) = match section.split_once('~') {
                Some((id, ordinal)) => (id, Some(ordinal)),
                None => (section, None),
            };
            let valid_id = id.len() <= 80
                && id
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
                && (!id.is_empty() || citation.is_some());
            let valid_citation = citation.is_none_or(|ordinal| {
                ordinal.len() <= 2
                    && !ordinal.starts_with('0')
                    && ordinal.bytes().all(|c| c.is_ascii_digit())
                    && !ordinal.is_empty()
            });
            (mode == "case" && valid_id && valid_citation).then(|| chapter.to_owned())
        });
        Some(Self {
            mode: mode.to_owned(),
            pack_id,
            chapter,
        })
    }

    fn value(&self) -> Value {
        json!({"mode": self.mode, "pack_id": self.pack_id, "chapter": self.chapter})
    }
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
            research: None,
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
        let mut selection =
            Self::selections(get("goalId"), get("view"), get("workAgent"), get("todo"))?;
        if selection.view.as_deref() == Some("research")
            && get("research_goal") == selection.goal_id
        {
            let pack = if get("research_analysis_goal") == selection.goal_id {
                get("research_pack")
            } else {
                ""
            };
            selection.research = ResearchReading::selections(
                get("research_reading"),
                pack,
                current.fragment().unwrap_or(""),
            );
        }
        Some(selection)
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
        if let Some(research) = &self.research {
            query.append_pair("research_goal", &self.goal_id);
            query.append_pair("research_reading", &research.mode);
            if let Some(pack) = &research.pack_id {
                query.append_pair("research_analysis_goal", &self.goal_id);
                query.append_pair("research_pack", pack);
            }
        }
        drop(query);
        destination.set_fragment(
            self.research
                .as_ref()
                .and_then(|research| research.chapter.as_deref()),
        );
        destination
    }

    fn from_value(value: &Value) -> Option<Self> {
        if value["version"] != 1 {
            return None;
        }
        let mut selection = Self::selections(
            value["goal_id"].as_str()?,
            value["view"].as_str().unwrap_or(""),
            value["work_agent"].as_str().unwrap_or(""),
            value["todo"].as_str().unwrap_or(""),
        )?;
        if !selection.goal_id.is_empty() && selection.view.as_deref() == Some("research") {
            let research = &value["research"];
            selection.research = ResearchReading::selections(
                research["mode"].as_str().unwrap_or(""),
                research["pack_id"].as_str().unwrap_or(""),
                research["chapter"].as_str().unwrap_or(""),
            );
        }
        Some(selection)
    }

    fn value(&self) -> Value {
        let mut value = json!({"version": 1, "goal_id": self.goal_id, "view": self.view,
            "work_agent": self.work_agent, "todo": self.todo});
        if let Some(research) = &self.research {
            value["research"] = research.value();
        }
        value
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

    fn research_page(port: u16) -> Url {
        let mut page = origin(port);
        page.query_pairs_mut().extend_pairs([
            ("goalId", "example"),
            ("view", "research"),
            ("research_goal", "example"),
            ("research_reading", "case"),
            ("research_analysis_goal", "example"),
            ("research_pack", &"a".repeat(64)),
        ]);
        page
    }

    #[test]
    fn research_reopen_keeps_registered_pack_and_chapter_or_citation_on_the_new_port() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("workspace-location.json");
        for namespace in [String::new(), format!("{}-", "b".repeat(64))] {
            for section in ["turning-point", "turning-point~2", "~1"] {
                let mut page = research_page(49123);
                let chapter = format!("case-section-{namespace}{}-{section}", "a".repeat(64));
                page.set_fragment(Some(&chapter));
                let saved = LocalWorkspace::capture(&page, &origin(49123)).unwrap();
                write(&path, &saved).unwrap();
                let reopened = read(&path).unwrap().destination(&origin(59234));
                assert_eq!(reopened.port(), Some(59234));
                let reopened_query = reopened
                    .query_pairs()
                    .into_owned()
                    .collect::<BTreeMap<_, _>>();
                for (key, value) in page.query_pairs().into_owned() {
                    assert_eq!(reopened_query.get(&key), Some(&value));
                }
                assert_eq!(reopened.fragment(), Some(chapter.as_str()));
                let bytes = fs::read_to_string(&path).unwrap();
                assert!(!bytes.contains("49123") && !bytes.contains("http"));
                assert_eq!(read(&path), Some(saved));
            }
        }
    }

    #[test]
    fn research_selections_stay_with_their_goal_view_and_pack() {
        let mut page = research_page(49123);
        page.set_fragment(Some(&format!(
            "case-section-{}-turning-point",
            "a".repeat(64)
        )));
        for (key, value) in [
            ("goalId", "other"),
            ("view", "tasks"),
            ("research_goal", "other"),
        ] {
            let mut changed = page.clone();
            let pairs = changed
                .query_pairs()
                .into_owned()
                .map(|(k, v)| {
                    let v = if k == key { value.to_owned() } else { v };
                    (k, v)
                })
                .collect::<Vec<_>>();
            changed.query_pairs_mut().clear().extend_pairs(pairs);
            assert!(LocalWorkspace::capture(&changed, &origin(49123))
                .unwrap()
                .research
                .is_none());
        }
        for pack in ["wrong", &"c".repeat(64)] {
            let mut changed = page.clone();
            changed.query_pairs_mut().clear().extend_pairs([
                ("goalId", "example"),
                ("view", "research"),
                ("research_goal", "example"),
                ("research_reading", "case"),
                ("research_analysis_goal", "other"),
                ("research_pack", pack),
            ]);
            let saved = LocalWorkspace::capture(&changed, &origin(49123)).unwrap();
            let research = saved.research.unwrap();
            assert!(research.pack_id.is_none() && research.chapter.is_none());
        }
        let history =
            ResearchReading::selections("history", &"a".repeat(64), page.fragment().unwrap())
                .unwrap();
        assert_eq!(history.mode, "history");
        assert!(history.chapter.is_none());
        assert!(ResearchReading::selections("retired", "", "").is_none());
    }

    #[test]
    fn invalid_bookmarks_and_unrelated_preference_fields_are_not_restored() {
        let pack = "a".repeat(64);
        for chapter in [
            "https://example.org/secret".to_owned(),
            format!("case-section-{}-chapter", "c".repeat(64)),
            format!("case-section-{pack}-../secret"),
            format!("case-section-{pack}-chapter~0"),
            format!("case-section-{pack}-chapter~01"),
            format!("case-section-{pack}-chapter~100"),
            format!("case-section-{pack}-{}", "x".repeat(81)),
        ] {
            assert!(ResearchReading::selections("case", &pack, &chapter)
                .unwrap()
                .chapter
                .is_none());
        }
        let legacy = json!({"version": 1, "goal_id": "example", "view": "research"});
        let saved = LocalWorkspace::from_value(&legacy).unwrap();
        assert!(saved.research.is_none());
        let mut modified = legacy;
        modified["research"] = json!({"mode": "case", "pack_id": pack, "chapter": "secret", "url": "https://example.org"});
        let restored = LocalWorkspace::from_value(&modified).unwrap();
        assert!(restored.research.as_ref().unwrap().chapter.is_none());
        assert!(!restored.value().to_string().contains("secret"));
        assert!(!restored.value().to_string().contains("example.org"));
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
