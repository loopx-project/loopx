//! Official source-ancestry transport; runtime selection retains its policy.
use serde_json::Value;
use std::{cmp::Ordering, process::Command, time::Duration};

pub(crate) fn compare_official_commits(left: &str, right: &str) -> Option<Ordering> {
    compare_using(left, right, |command| {
        let output = crate::services::timed_output_with_timeout(command, Duration::from_secs(3))?;
        output
            .status
            .success()
            .then(|| serde_json::from_slice(&output.stdout).ok())
            .flatten()
    })
}

fn compare_using(
    left: &str,
    right: &str,
    mut request: impl FnMut(Command) -> Option<Value>,
) -> Option<Ordering> {
    if ![left, right]
        .iter()
        .all(|revision| revision.len() == 40 && revision.bytes().all(|c| c.is_ascii_hexdigit()))
    {
        return None;
    }
    // Page two retains the relation without downloading page one's file patches.
    let path = format!("repos/loopx-project/loopx/compare/{left}...{right}?per_page=1&page=2");
    let url = format!("https://api.github.com/{path}");
    let mut command = Command::new("curl");
    crate::services::configure_runtime_environment(&mut command);
    command.args([
        "--fail",
        "--silent",
        "--show-error",
        "--proto",
        "=https",
        "--max-time",
        "2",
        "--header",
        "Accept: application/vnd.github+json",
        &url,
    ]);
    let payload = request(command).or_else(|| {
        // The anonymous API can be rate-limited even when a user's existing
        // GitHub CLI can read this same public relation. Never start login or
        // let GH_HOST redirect the read to another authority.
        let mut command = Command::new("gh");
        crate::services::configure_runtime_environment(&mut command);
        command.env("GH_PROMPT_DISABLED", "1").args([
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "--header",
            "Accept: application/vnd.github+json",
            &path,
        ]);
        request(command)
    })?;
    match payload["status"].as_str()? {
        "ahead" => Some(Ordering::Less),
        "behind" => Some(Ordering::Greater),
        "identical" => Some(Ordering::Equal),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const LEFT: &str = "1111111111111111111111111111111111111111";
    const RIGHT: &str = "2222222222222222222222222222222222222222";

    #[test]
    fn comparison_relation_is_not_a_package_version_or_wall_clock() {
        for (status, expected) in [
            ("ahead", Some(Ordering::Less)),
            ("behind", Some(Ordering::Greater)),
            ("identical", Some(Ordering::Equal)),
            ("diverged", None),
        ] {
            let mut calls = 0;
            let actual = compare_using(LEFT, RIGHT, |command| {
                calls += 1;
                assert_eq!(command.get_program(), "curl");
                Some(serde_json::json!({"status":status}))
            });
            assert_eq!(actual, expected);
            assert_eq!(calls, 1);
        }
    }

    #[test]
    fn invalid_revisions_do_not_reach_a_process() {
        for revision in [
            "",
            "--help",
            "../private",
            "111111111111111111111111111111111111111z",
        ] {
            assert_eq!(
                compare_using(revision, RIGHT, |_| panic!("must not invoke a tool")),
                None
            );
        }
    }

    #[test]
    fn anonymous_failure_uses_the_same_public_read_without_starting_login() {
        let mut calls = 0;
        let actual = compare_using(LEFT, RIGHT, |command| {
            calls += 1;
            if calls == 1 {
                assert_eq!(command.get_program(), "curl");
                return None;
            }
            assert_eq!(command.get_program(), "gh");
            let args: Vec<_> = command
                .get_args()
                .map(|arg| arg.to_str().unwrap())
                .collect();
            assert_eq!(
                args,
                [
                    "api",
                    "--hostname",
                    "github.com",
                    "--method",
                    "GET",
                    "--header",
                    "Accept: application/vnd.github+json",
                    &format!(
                        "repos/loopx-project/loopx/compare/{LEFT}...{RIGHT}?per_page=1&page=2"
                    )
                ]
            );
            assert!(command
                .get_envs()
                .any(|(key, value)| key == "GH_PROMPT_DISABLED"
                    && value == Some(std::ffi::OsStr::new("1"))));
            Some(serde_json::json!({"status":"ahead"}))
        });
        assert_eq!(actual, Some(Ordering::Less));
        assert_eq!(calls, 2);
    }

    #[test]
    fn unavailable_tools_leave_ancestry_unknown() {
        let mut calls = 0;
        assert_eq!(
            compare_using(LEFT, RIGHT, |_| {
                calls += 1;
                None
            }),
            None
        );
        assert_eq!(calls, 2);
    }

    #[test]
    fn a_successful_nonordering_answer_is_not_retried_under_another_account() {
        let mut calls = 0;
        assert_eq!(
            compare_using(LEFT, RIGHT, |_| {
                calls += 1;
                Some(serde_json::json!({"status":"diverged"}))
            }),
            None
        );
        assert_eq!(calls, 1);
    }
}
