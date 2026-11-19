//! macOS private service lifetime, including abrupt native App termination.
//!
//! Re-enter this executable before Tauri starts. The App retains the helper's
//! stdin writer; EOF is exact ownership loss, without PID polling or adoption
//! of a listener. Core and its ordinary descendants inherit the helper's group,
//! so the existing CommandGroup stop boundary also covers the helper.
use command_group::{CommandGroup, GroupChild};
use rustix::process::{getpgrp, getpid, kill_process_group, Signal};
use std::{
    env,
    ffi::{OsStr, OsString},
    io::{self, Read},
    process::{self, Command, Stdio},
    sync::mpsc::{self, RecvTimeoutError},
    thread,
    time::{Duration, Instant},
};

// Internal argv within this native executable, not a Core CLI or user setting.
const HELPER_ARG: &str = "--loopx-owned-service";

pub(crate) fn spawn(service: &Command) -> io::Result<GroupChild> {
    // libtest owns the executable entry in unit tests. Its fixture invokes the
    // same supervisor; packaged validation uses the real native argv entry.
    #[cfg(test)]
    let mut helper = tests::command_for_service(service);
    #[cfg(not(test))]
    let mut helper = Command::new(env::current_exe()?);
    #[cfg(not(test))]
    helper
        .arg(HELPER_ARG)
        .arg(service.get_program())
        .args(service.get_args());
    for (key, value) in service.get_envs() {
        if let Some(value) = value {
            helper.env(key, value);
        } else {
            helper.env_remove(key);
        }
    }
    if let Some(directory) = service.get_current_dir() {
        helper.current_dir(directory);
    }
    helper
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .group_spawn()
}

pub(crate) fn run_if_requested() {
    let mut args = env::args_os().skip(1);
    if args.next().as_deref() != Some(OsStr::new(HELPER_ARG)) {
        return;
    }
    let result = args
        .next()
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "missing owned service"))
        .and_then(|program| supervise(&program, args.collect()));
    if let Err(error) = result {
        eprintln!("LoopX owned service could not start: {error}");
    }
    process::exit(1);
}

fn supervise(program: &OsStr, args: Vec<OsString>) -> io::Result<()> {
    let group = getpgrp();
    if group != getpid() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "owned service requires its own process group",
        ));
    }
    let (closed, owner) = mpsc::channel();
    thread::spawn(move || {
        let mut input = io::stdin().lock();
        let mut bytes = [0; 64];
        loop {
            match input.read(&mut bytes) {
                Ok(0) => break,
                Ok(_) => continue,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => break,
            }
        }
        let _ = closed.send(());
    });
    // Prepare the owner observation before spawning anything that needs it.
    let mut child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()?;
    loop {
        // A service exit must also release the helper, allowing the existing
        // native supervisor to observe it and recover that service normally.
        if !matches!(child.try_wait(), Ok(None))
            || !matches!(
                owner.recv_timeout(Duration::from_millis(100)),
                Err(RecvTimeoutError::Timeout)
            )
        {
            let _ = child.kill();
            let _ = child.wait();
            // The target is this still-live helper's own group, never a supplied
            // PID or discovered listener. Match CommandGroup's existing KILL
            // semantics, including this helper and owned service descendants.
            let _ = kill_process_group(group, Signal::KILL);
            process::exit(1);
        }
    }
}

// A killed helper can have children that the native parent cannot waitpid.
// Observe its already-killed owned group draining before reusing the private
// endpoint. This never signals a discovered process or an occupied port.
pub(crate) fn wait_for_group_exit(group: u32) -> io::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let output = Command::new("/bin/ps")
            .args(["-g", &group.to_string(), "-o", "stat="])
            .output()?;
        if (!output.status.success() && output.stderr.is_empty() && output.stdout.is_empty())
            || (output.status.success()
                && String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .all(|line| line.trim().starts_with('Z')))
        {
            return Ok(());
        }
        if !output.status.success() {
            return Err(io::Error::other("could not inspect owned service group"));
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "owned service group did not exit",
            ));
        }
        thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(test)]
#[path = "service_lifetime_tests.rs"]
mod tests;
