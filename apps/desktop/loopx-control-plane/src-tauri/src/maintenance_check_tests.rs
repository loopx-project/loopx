//! Manual feed admission, service independence and native update ownership.
use super::*;

#[test]
fn manual_check_waits_for_the_existing_transaction_without_reserving_busy() {
    use std::{
        future::Future,
        task::{Context, Waker},
    };
    let state = Maintenance::default();
    let transaction = state.supervision.try_lock().unwrap();
    let mut check = Box::pin(state.run_manual_check(
        "main",
        std::future::ready(Ok::<_, String>(Some(()))),
        |_| panic!("replacement has priority"),
    ));
    assert!(check
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
        .is_pending());
    assert!(!state.busy.load(Ordering::Acquire));
    state.complete_app_replacement(json!({"version":"1.2.4"}));
    drop(transaction);
    assert_eq!(
        tauri::async_runtime::block_on(check).unwrap_err(),
        "restart_required"
    );
}

#[test]
fn a_checked_candidate_remains_actionable_when_local_runtime_is_unavailable() {
    let state = Maintenance::default();
    state.publish(
        "runtime_required",
        json!({"code":"runtime_identity_unavailable"}),
    );
    tauri::async_runtime::block_on(state.run_manual_check(
        "main",
        std::future::ready(Ok::<_, String>(Some("1.2.4"))),
        |update| json!({"channel":"main","version":update}),
    ))
    .unwrap();
    assert!(state
        .reconcile_services::<()>(|| Err("unqualified runtime".into()))
        .is_err());
    assert_eq!(state.startup_snapshot()["phase"], "runtime_required");
    assert_eq!(state.status_snapshot()["phase"], "available");
    state
        .incomplete_app_installation
        .store(true, Ordering::Release);
    assert_eq!(
        state.status_snapshot()["details"]["code"],
        "app_install_incomplete"
    );
}

#[test]
fn manual_feed_read_does_not_own_startup_or_child_recovery() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    let state = Maintenance::default();
    let released = AtomicBool::new(false);
    let mut request = Box::pin(state.run_manual_check(
        "main",
        std::future::poll_fn(|_| {
            if released.load(Ordering::Acquire) {
                Poll::Ready(Ok(Some("1.2.4")))
            } else {
                Poll::Pending
            }
        }),
        |update| json!({"channel":"main","version":update}),
    ));
    assert!(request
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
        .is_pending());
    assert_eq!(state.reconcile_services(|| Ok(1)).unwrap(), Some(1));
    assert_eq!(state.recover_connected(|| 2), Some(2));
    assert_eq!(state.status_snapshot()["phase"], "checking");
    released.store(true, Ordering::Release);
    assert_eq!(
        tauri::async_runtime::block_on(request).unwrap()["phase"],
        "available"
    );
    assert_eq!(state.reconcile_services(|| Ok(3)).unwrap(), Some(3));
    assert_eq!(state.status_snapshot()["phase"], "available");
    assert_eq!(state.status_snapshot()["details"]["version"], "1.2.4");
}

#[test]
fn local_runtime_qualification_does_not_cancel_a_manual_feed_result() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    let state = Maintenance::default();
    let released = AtomicBool::new(false);
    let mut request = Box::pin(state.run_manual_check(
        "main",
        std::future::poll_fn(|_| {
            if released.load(Ordering::Acquire) {
                Poll::Ready(Ok(Some("1.2.4")))
            } else {
                Poll::Pending
            }
        }),
        |update| json!({"version":update}),
    ));
    assert!(request
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
        .is_pending());
    // resume_runtime borrows busy while qualifying the local executable;
    // it does not cancel or supersede an update check.
    let qualification = state.acquire().unwrap();
    released.store(true, Ordering::Release);
    assert_eq!(
        tauri::async_runtime::block_on(request).unwrap()["phase"],
        "available"
    );
    drop(qualification);
    state.reconcile_services(|| Ok(())).unwrap();
    assert_eq!(state.status_snapshot()["details"]["version"], "1.2.4");
}

#[test]
fn failed_manual_feed_keeps_diagnostics_without_stopping_local_startup() {
    let state = Maintenance::default();
    let error = tauri::async_runtime::block_on(state.run_manual_check(
        "main",
        std::future::ready(Err::<Option<()>, _>("update_network_failed".into())),
        |_| panic!("failed reads admit no candidate"),
    ))
    .unwrap_err();
    assert_eq!(error, "update_network_failed");
    assert!(!state.manual_failure_pending.load(Ordering::Acquire));
    assert_eq!(state.reconcile_services(|| Ok(1)).unwrap(), Some(1));
    assert_eq!(state.status_snapshot()["details"]["code"], error);
    assert_eq!(*state.last_failure.lock().unwrap(), state.status_snapshot());
    assert_eq!(state.recover_connected(|| 2), Some(2));
}

#[test]
fn superseded_manual_feed_cannot_admit_a_candidate_or_publish_an_old_failure() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    for late in [Ok(Some(())), Err("update_network_failed".to_string())] {
        let state = Maintenance::default();
        let released = AtomicBool::new(false);
        let mut request = Box::pin(state.run_manual_check(
            "main",
            std::future::poll_fn(|_| {
                if released.load(Ordering::Acquire) {
                    Poll::Ready(late.clone())
                } else {
                    Poll::Pending
                }
            }),
            |_| panic!("superseded feed cannot replace the current candidate"),
        ));
        assert!(request
            .as_mut()
            .poll(&mut Context::from_waker(Waker::noop()))
            .is_pending());
        let current = tauri::async_runtime::block_on(state.run_manual_check(
            "stable",
            std::future::ready(Ok::<_, String>(None::<()>)),
            |_| json!({"channel":"stable"}),
        ))
        .unwrap();
        state.reconcile_services(|| Ok(())).unwrap();
        released.store(true, Ordering::Release);
        assert_eq!(tauri::async_runtime::block_on(request).unwrap(), current);
        assert_eq!(state.status_snapshot(), current);
        assert!(state.last_failure.lock().unwrap().is_null());
    }
}

#[test]
fn manual_feed_still_respects_mutation_and_restart_ownership() {
    let state = Maintenance::default();
    let mutation = state.acquire().unwrap();
    let refused = || std::future::ready(Ok::<_, String>(Some(())));
    assert_eq!(
        tauri::async_runtime::block_on(
            state.run_manual_check("main", refused(), |_| panic!("a mutation owns this action"),)
        )
        .unwrap_err(),
        "update_busy"
    );
    drop(mutation);
    state.complete_app_replacement(json!({"version":"1.2.4"}));
    assert_eq!(
        tauri::async_runtime::block_on(state.run_manual_check("main", refused(), |_| panic!(
            "replacement requires restart"
        ),))
        .unwrap_err(),
        "restart_required"
    );
    assert_eq!(state.status_snapshot()["phase"], "restart_required");
}

#[test]
fn replacement_supersedes_an_already_pending_manual_check() {
    use std::{
        future::Future,
        task::{Context, Poll, Waker},
    };
    let state = Maintenance::default();
    let released = AtomicBool::new(false);
    let mut request = Box::pin(state.run_manual_check(
        "main",
        std::future::poll_fn(|_| {
            if released.load(Ordering::Acquire) {
                Poll::Ready(Ok(Some(())))
            } else {
                Poll::Pending
            }
        }),
        |_| panic!("an old check cannot admit a candidate after replacement"),
    ));
    assert!(request
        .as_mut()
        .poll(&mut Context::from_waker(Waker::noop()))
        .is_pending());
    let replacement = state.acquire().unwrap();
    state.cancel_background_update();
    let current = state.complete_app_replacement(json!({"version":"1.2.4"}));
    drop(replacement);
    released.store(true, Ordering::Release);
    assert_eq!(tauri::async_runtime::block_on(request).unwrap(), current);
    assert_eq!(state.status_snapshot(), current);
}
