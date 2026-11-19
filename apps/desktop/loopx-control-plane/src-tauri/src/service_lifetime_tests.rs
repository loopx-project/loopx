use super::*;
use std::{fs, time::Instant};

const FIXTURE_ARGS: &str = "LOOPX_TEST_SERVICE_LIFETIME_ARGS";

// Enter the production supervisor without a second Tauri application in the
// test runner. Packaged QA also exercises the actual native argv entry.
#[test]
#[ignore = "subprocess fixture, invoked by the lifetime tests"]
fn helper_process() {
    let Ok(value) = env::var(FIXTURE_ARGS) else {
        return;
    };
    let args: Vec<String> = serde_json::from_str(&value).unwrap();
    let result = supervise(
        OsStr::new(&args[0]),
        args[1..].iter().map(OsString::from).collect(),
    );
    assert!(result.is_ok(), "{result:?}");
}

struct Fixture {
    child: GroupChild,
    directory: tempfile::TempDir,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        if matches!(self.child.try_wait(), Ok(None)) {
            let _ = self.child.kill();
        }
        let _ = self.child.wait();
    }
}

impl Fixture {
    fn new(script: &str) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("pids");
        let args = [
            "/bin/sh",
            "-c",
            script,
            "service-lifetime-fixture",
            path.to_str().unwrap(),
        ]
        .map(str::to_owned);
        let child = helper_command(&args).group_spawn().unwrap();
        Self { child, directory }
    }

    fn pids(&self) -> Vec<u32> {
        let path = self.directory.path().join("pids");
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Ok(value) = fs::read_to_string(&path) {
                let pids: Vec<u32> = value
                    .split_whitespace()
                    .map(|value| value.parse().unwrap())
                    .collect();
                if pids.len() == 2 {
                    return pids;
                }
            }
            assert!(Instant::now() < deadline, "service did not initialize");
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_exit(&mut self) {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(status) = self.child.try_wait().unwrap() {
                assert!(!status.success());
                return;
            }
            assert!(Instant::now() < deadline, "lifetime helper remained alive");
            thread::sleep(Duration::from_millis(10));
        }
    }
}

fn helper_command(args: &[String]) -> Command {
    let mut command = Command::new(env::current_exe().unwrap());
    command
        .args([
            "--exact",
            "service_lifetime::tests::helper_process",
            "--ignored",
            "--nocapture",
        ])
        .env(FIXTURE_ARGS, serde_json::to_string(args).unwrap())
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command
}

pub(super) fn command_for_service(service: &Command) -> Command {
    let args: Vec<String> = std::iter::once(service.get_program())
        .chain(service.get_args())
        .map(|value| value.to_str().expect("UTF-8 fixture command").to_owned())
        .collect();
    helper_command(&args)
}

fn running(pid: u32) -> bool {
    let output = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "stat="])
        .output()
        .unwrap();
    let status = String::from_utf8_lossy(&output.stdout);
    output.status.success() && !status.trim().is_empty() && !status.trim().starts_with('Z')
}

fn wait_stopped(pids: &[u32]) {
    let deadline = Instant::now() + Duration::from_secs(5);
    while pids.iter().any(|&pid| running(pid)) {
        assert!(
            Instant::now() < deadline,
            "owned descendants remained alive"
        );
        thread::sleep(Duration::from_millis(10));
    }
}

const WAITING_SERVICE: &str = r#"
    if IFS= read -r unexpected; then exit 31; fi
    sleep 30 &
    printf '%s %s' "$$" "$!" > "$1"
    wait
"#;

#[test]
fn owner_pipe_eof_stops_only_its_service_and_descendants() {
    let mut first = Fixture::new(WAITING_SERVICE);
    let first_pids = first.pids();
    let mut second = Fixture::new(WAITING_SERVICE);
    let second_pids = second.pids();
    assert!(first_pids
        .iter()
        .chain(&second_pids)
        .all(|&pid| running(pid)));

    // PID markers also prove service stdin was null, rather than the owner's
    // separate lifetime pipe. A lost owner must not stop the other group.
    drop(first.child.inner().stdin.take());
    first.wait_exit();
    wait_stopped(&first_pids);
    assert!(second_pids.iter().all(|&pid| running(pid)));
    assert!(second.child.try_wait().unwrap().is_none());
    drop(second.child.inner().stdin.take());
    second.wait_exit();
    wait_stopped(&second_pids);
}

#[test]
fn service_exit_releases_helper_and_remaining_group_members() {
    let mut fixture =
        Fixture::new(r#"sleep 30 & printf '%s %s' "$$" "$!" > "$1"; sleep 0.1; exit 7"#);
    let pids = fixture.pids();
    fixture.wait_exit();
    wait_stopped(&pids);
}

#[test]
fn existing_group_stop_cleans_helper_service_and_descendants() {
    let mut fixture = Fixture::new(WAITING_SERVICE);
    let pids = fixture.pids();
    fixture.child.kill().unwrap();
    fixture.wait_exit();
    wait_stopped(&pids);
}

#[test]
fn helper_rejects_a_shared_group_before_starting_service() {
    let directory = tempfile::tempdir().unwrap();
    let marker = directory.path().join("must-not-exist");
    let args = [
        "/bin/sh".to_owned(),
        "-c".to_owned(),
        "touch \"$1\"".to_owned(),
        "fixture".to_owned(),
        marker.to_string_lossy().into_owned(),
    ];
    // Plain spawn inherits the runner's group. Never target that group.
    let mut child = helper_command(&args).spawn().unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(!status.success());
            break;
        }
        assert!(
            Instant::now() < deadline,
            "shared-group helper did not refuse"
        );
        thread::sleep(Duration::from_millis(10));
    }
    assert!(!marker.exists());
}

#[test]
fn missing_service_executable_does_not_leave_a_helper() {
    let directory = tempfile::tempdir().unwrap();
    let args = [directory
        .path()
        .join("missing-runtime")
        .to_string_lossy()
        .into_owned()];
    let child = helper_command(&args).group_spawn().unwrap();
    let mut fixture = Fixture { child, directory };
    fixture.wait_exit();
}
