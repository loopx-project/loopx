"""Durable administrative prefixes recover without changing original execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import loopx
import pytest

from loopx.control_plane.quota import execution_preparation, settlement
from loopx.control_plane.coordination import local_authority
from loopx.control_plane.runtime import time as runtime_time
from loopx.control_plane.work_items import task_lease
from tests.control_plane.test_execution_loss_closeout_cli import (
    OWNER,
    PEER,
    _assert_no_completion_or_debit,
    _canonical,
    _closeout_args,
    _fixture,
    _lease,
    _readback,
    _receipt_lines,
    _reject_without_effect,
    _runs,
    _unchanged_state,
)


def _immutable_run(context, previous_runs):
    runs = _runs(context)
    names = runs.keys() - previous_runs.keys()
    assert len(names) == 1
    return json.loads(runs[names.pop()])


def _assert_markdown(context, record, result):
    json_path, markdown_path = Path(record["json_path"]), Path(record["markdown_path"])
    assert json_path.parent == context["runtime"] / "goals" / context["goal_id"] / "runs"
    assert markdown_path == json_path.with_suffix(".md")
    assert result["json_path"] == str(json_path) and result["markdown_path"] == str(markdown_path)
    assert json_path.is_file() and markdown_path.is_file()
    assert markdown_path.read_text() == (
        "# Administrative execution-loss closeout\n\n"
        f"Turn: {record['turn_instance_id']}\nNo delivery, completion or quota spend.\n"
    )


def _peer_claim_after_expiry(context, monkeypatch):
    previous = _lease(context)
    after_expiry = datetime.fromisoformat(previous["expires_at"].replace("Z", "+00:00"))
    after_expiry = after_expiry.replace(microsecond=0) + timedelta(seconds=2)

    class AfterExpiryDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (after_expiry.astimezone(tz) if tz
                    else after_expiry.astimezone().replace(tzinfo=None))

    monkeypatch.setattr(runtime_time, "datetime", AfterExpiryDatetime)
    monkeypatch.setattr(local_authority, "now_local", lambda: after_expiry.isoformat())
    monkeypatch.setattr(task_lease, "now_utc", lambda: after_expiry.astimezone(timezone.utc))
    assert runtime_time.now_utc() == after_expiry.astimezone(timezone.utc)
    code, claimed = context["cli"]("todo", "claim", "--goal-id", context["goal_id"],
        "--todo-id", context["todo"], "--agent-id", PEER, "--claimed-by", PEER)
    assert code == 0, claimed
    lease = _lease(context)
    assert lease["owner"] == PEER and lease["lease_epoch"] > previous["lease_epoch"]
    assert lease["version"] > previous["version"] and lease["idempotency_key"] != previous["idempotency_key"]
    return lease


def _interrupted_owner(monkeypatch, context, fault):
    """Inject at the real owner IO boundary; canonical stores and locks remain real."""
    original = execution_preparation.effect_runtime_result
    module = Path(loopx.__file__).resolve().parent / "control_plane"
    script = """
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {dirname} from 'node:path';
let raw='';for await(const part of process.stdin)raw+=part;
const {request,fault,moduleRoot,marker}=JSON.parse(raw);
const index=request.runtime_root+'/goals/'+request.identity.goal_id+'/runs/index.jsonl';
const event=request.runtime_root+'/goals/'+request.identity.goal_id+'/rollout-event-log.jsonl';
const open=fs.openSync,close=fs.closeSync,write=fs.writeFileSync,sync=fs.fsyncSync,rename=fs.renameSync;
const paths=new Map(),dirty=new Set();let published=null;
fs.openSync=(path,...args)=>{const fd=open(path,...args);paths.set(fd,String(path));return fd;};
fs.closeSync=fd=>{paths.delete(fd);dirty.delete(fd);return close(fd);};
fs.writeFileSync=(target,data,...args)=>{
  const result=write(target,data,...args);
  if(typeof target==='number'&&String(data).includes('execution_loss_closeout'))dirty.add(target);
  return result;
};
fs.renameSync=(source,target)=>{
  const result=rename(source,target);
  if(String(target).includes('/execution-loss-'))published=String(target);
  return result;
};
fs.fsyncSync=fd=>{
  const result=sync(fd),path=paths.get(fd);
  const hit=(fault==='json'&&published?.endsWith('.json')&&path===dirname(published))
    ||(fault==='markdown'&&published?.endsWith('.md')&&path===dirname(published))
    ||(fault==='index'&&path===index&&dirty.has(fd))
    ||(fault==='event'&&path===event&&dirty.has(fd));
  if(hit){write(marker,fault);throw new Error('injected administrative interruption: '+fault);}
  return result;
};
syncBuiltinESMExports();
if(fault==='store_identity'){
  for(const [path,name] of [['coordination/file_authority_store.ts','FileAuthorityStore'],
                          ['coordination/sqlite_authority_store.ts','SqliteAuthorityStore']]){
    const Type=(await import(moduleRoot+'/'+path))[name],checkpoint=Type.prototype.withCheckpointHead;
    Type.prototype.withCheckpointHead=function(save){
      return checkpoint.call(this,(head,identity)=>save(head,identity+':replacement'));
    };
  }
}
const {commitExecutionLossCloseout}=await import(moduleRoot+'/quota/execution_loss_closeout.ts');
try{process.stdout.write(JSON.stringify({ok:true,result:await commitExecutionLossCloseout(request)}));}
catch(error){process.stdout.write(JSON.stringify({ok:false,error:String(error),code:error.code}));}
"""

    def invoke(method, params, **kwargs):
        if method != "quota.execution_loss_closeout.commit":
            return original(method, params, **kwargs)
        marker = context["project"] / "administrative-fault-observed"
        child = subprocess.run(
            ["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
             "--input-type=module", "-e", script],
            input=json.dumps({"request": params, "fault": fault, "moduleRoot": module.as_uri(),
                             "marker": str(marker)}),
            text=True, capture_output=True, timeout=45, check=False,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        result = json.loads(child.stdout)
        if fault != "store_identity":
            assert marker.read_text() == fault, result
        assert result["ok"] is False, result
        if fault == "store_identity":
            assert result["code"] == "execution_loss_commit_unknown", result
        raise ValueError(result["error"])

    monkeypatch.setattr(execution_preparation, "effect_runtime_result", invoke)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("fault", ["json", "markdown", "index", "event"])
def test_real_durable_prefix_recovery_keeps_original_admission_and_no_spend(
    tmp_path, monkeypatch, capsys, provider, fault,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    original = _readback(context)
    authority, state, receipts = _canonical(context), context["state"].read_bytes(), _receipt_lines(context)
    runs_before = _runs(context)
    argv = _closeout_args(context)
    with monkeypatch.context() as injected:
        _interrupted_owner(injected, context, fault)
        code, failed = context["cli"](*argv)
    assert code != 0 and failed["ok"] is False, failed
    assert _canonical(context) == authority
    assert context["state"].read_bytes() == state
    assert _receipt_lines(context) == receipts
    record = _immutable_run(context, runs_before)
    frozen = record["execution_loss_closeout"]
    assert frozen["original_prepared"] == original.prepared_lease
    current = _lease(context)
    assert current["owner"] == OWNER
    assert frozen["current_control_writer"] == {
        "owner": OWNER, "lease_key_sha256": sha256(current["idempotency_key"].encode()).hexdigest(),
        "version": current["version"], "lease_epoch": current["lease_epoch"],
        "store_identity": authority["store_identity"], "provider_revision": authority["provider_revision"],
    }
    immutable = _runs(context)
    code, renewal = context["cli"]("task-lease", "renew", "--goal-id", context["goal_id"],
        "--todo-id", context["todo"], "--owner", OWNER, "--idempotency-key", current["idempotency_key"],
        "--expected-version", str(current["version"]), "--ttl-seconds", "3600")
    assert code == 0 and renewal["ok"] is True, renewal
    renewed = _lease(context)
    assert renewed["owner"] == current["owner"] == OWNER
    assert renewed["idempotency_key"] == current["idempotency_key"]
    assert renewed["lease_epoch"] == current["lease_epoch"]
    assert renewed["version"] == current["version"] + 1
    assert datetime.fromisoformat(renewed["expires_at"].replace("Z", "+00:00")) > datetime.fromisoformat(
        current["expires_at"].replace("Z", "+00:00"))
    renewed_authority = _canonical(context)
    assert renewed_authority["store_identity"] == authority["store_identity"]
    assert renewed_authority["provider_revision"] != authority["provider_revision"]
    assert _receipt_lines(context) == receipts and _runs(context) == immutable
    _reject_without_effect(context, argv, expected_code="refresh_task_lease_unavailable")
    renewed_argv = _closeout_args(context, lease=renewed)
    code, recovered = context["cli"](*renewed_argv)
    assert code == 0 and recovered["settlement_progress"]["state"] == "settled", recovered
    assert recovered["idempotent_replay"] is (fault == "event")
    assert recovered["execution_loss_closeout"] == frozen
    _assert_markdown(context, record, recovered)
    assert _runs(context) == immutable
    settled = _readback(context)
    assert settled.replay_phase == settlement.ReceiptBoundReplayPhase.SETTLED
    assert settled.prepared_lease == original.prepared_lease
    assert _canonical(context) == renewed_authority and _lease(context) == renewed
    assert _receipt_lines(context) == receipts and context["state"].read_bytes() == state
    _assert_no_completion_or_debit(context)
    before_replay = _unchanged_state(context)
    code, replay = context["cli"](*renewed_argv)
    assert code == 0 and replay["idempotent_replay"] is True, replay
    assert _unchanged_state(context) == before_replay


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_frozen_administrative_intent_rejects_changed_store_identity(
    tmp_path, monkeypatch, capsys, provider,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    argv = _closeout_args(context)
    original, runs_before = _readback(context), _runs(context)
    authority, receipts = _canonical(context), _receipt_lines(context)
    with monkeypatch.context() as injected:
        _interrupted_owner(injected, context, "json")
        code, interrupted = context["cli"](*argv)
    assert code != 0 and interrupted["ok"] is False, interrupted
    record = _immutable_run(context, runs_before)
    assert record["execution_loss_closeout"]["original_prepared"] == original.prepared_lease
    assert not Path(record["markdown_path"]).exists()
    before = _unchanged_state(context)
    with monkeypatch.context() as injected:
        _interrupted_owner(injected, context, "store_identity")
        code, rejected = context["cli"](*argv)
    assert code != 0 and rejected["ok"] is False, rejected
    assert _unchanged_state(context) == before
    code, recovered = context["cli"](*argv)
    assert code == 0 and recovered["idempotent_replay"] is False, recovered
    assert recovered["settlement_progress"]["state"] == "settled"
    assert recovered["execution_loss_closeout"] == record["execution_loss_closeout"]
    assert _runs(context) == before[2]
    _assert_markdown(context, record, recovered)
    assert _canonical(context) == authority and _receipt_lines(context) == receipts
    _assert_no_completion_or_debit(context)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_peer_takeover_after_durable_prefix_cannot_recover_original_owner_intent(
    tmp_path, monkeypatch, capsys, provider,
):
    context = _fixture(tmp_path, monkeypatch, capsys, provider, source_profile=False)
    original, runs_before = _readback(context), _runs(context)
    receipts = _receipt_lines(context)
    with monkeypatch.context() as injected:
        _interrupted_owner(injected, context, "json")
        code, interrupted = context["cli"](*_closeout_args(context))
    assert code != 0 and interrupted["ok"] is False, interrupted
    record = _immutable_run(context, runs_before)
    immutable = _runs(context)
    peer_lease = _peer_claim_after_expiry(context, monkeypatch)
    authority = _canonical(context)
    todo = next(todo for todo in authority["todos"] if todo["todo_id"] == context["todo"])
    assert todo["claimed_by"] == PEER
    assert _receipt_lines(context) == receipts and _runs(context) == immutable
    _reject_without_effect(context, _closeout_args(context, lease=peer_lease),
                           expected_code="refresh_task_lease_unavailable")
    assert _canonical(context) == authority and _lease(context) == peer_lease
    assert _runs(context) == immutable and _receipt_lines(context) == receipts
    assert not Path(record["markdown_path"]).exists()
    readback = _readback(context)
    assert readback.prepared_lease == original.prepared_lease
    assert readback.heartbeat_receipt == original.heartbeat_receipt
    assert not readback.writeback.receipts and not readback.spend.receipts
    _assert_no_completion_or_debit(context)
