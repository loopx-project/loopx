"""Current-Agent IO for the existing Goal checkpoint and Turn receipt owners."""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock, exclusive_cross_runtime_file_lock
from ..runtime.document_io import atomic_write_private_json as atomic_write_json
from ..goals.checkpoint_context_io import (
    _checkpoint_effect, _receipt_path, _read_context_receipt, _resolve,
    _local_source_facts, _source_guard, _inspect_attempt, CheckpointReadContextRejected,
    read_checkpoint_context, checkpoint_continuation_required,
)
from ..quota.settlement import SettlementIdentity, read_heartbeat_settlement
from ..quota.accounting_admission import quota_accounting_admission
from .host_failure import BuiltInHostError, build_host_failure_record
from ..goals.first_party_host_admission import FirstPartyHostRuntimeRejected


class CurrentAgentCheckpoint:
    def __init__(self, registry: Path, root: Path, state: Path, identity: SettlementIdentity,
                 runner: Callable[[dict[str, Any]], dict[str, Any]], *, explicit_resume: bool = False,
                 goal_ref: Mapping[str, Any] | None = None):
        self.registry, self.root, self.state, self.identity, self.runner = registry, root, state, identity, runner
        self.explicit_resume = explicit_resume
        self.goal_ref = dict(goal_ref) if goal_ref is not None else None
        self._enabled: bool | None = None

    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        readback = read_heartbeat_settlement(self.root, goal_id=self.identity.goal_id,
            agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
            turn_instance_id=self.identity.turn_instance_id,
            replan_obligation_id=self.identity.replan_obligation_id, registry_path=self.registry,
            goal_ref=self.goal_ref)
        self._enabled = readback is not None and checkpoint_continuation_required(readback)
        return self._enabled

    @contextmanager
    def _source_context(self, operation: str) -> Iterator[Mapping[str, Any] | None]:
        with ExitStack() as locks:
            admission = None
            if self.goal_ref is None:
                locks.enter_context(exclusive_cross_runtime_file_lock(
                    self.root / 'goals' / self.identity.goal_id / 'runs/index.jsonl', operation=operation))
            else:
                admission = locks.enter_context(quota_accounting_admission(runtime_root=self.root,
                    registry_path=self.registry, goal_id=self.identity.goal_id, goal_ref=self.goal_ref,
                    operation=operation, handoff_legacy_index=True))
                # A lock witness is not a grant. The typed owner verifies the
                # current source and original binding before scoped IO.
                readback = read_heartbeat_settlement(self.root, goal_id=self.identity.goal_id,
                    agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
                    turn_instance_id=self.identity.turn_instance_id,
                    replan_obligation_id=self.identity.replan_obligation_id, registry_path=self.registry,
                    goal_ref=self.goal_ref, source_admission=admission, borrow_source_admission=True)
                if readback is None or readback.identity.value is None:
                    raise ValueError('Original checkpoint admission is unavailable')
            locks.enter_context(_source_guard(self.root, self.identity.goal_id, self.state, self.registry))
            yield admission

    def _save(self, path: Path, receipt: dict[str, Any]) -> None:
        with self._source_context('checkpoint-direction-binding'):
            current = _read_context_receipt(path)
            if current.get('read_context_id') != receipt['read_context_id']:
                raise ValueError('Direction input was replaced while the current Agent was judging')
            atomic_write_json(path, receipt)

    def _committed_result(self, result: Mapping[str, Any], proof: Mapping[str, Any]) -> dict[str, Any]:
        context = proof.get('context')
        if not isinstance(context, Mapping) or not context.get('read_context_id'):
            raise ValueError('Committed direction has no verified original read identity')
        # Let the existing refresh owner replay its original effect without a
        # new judgment or an obsolete pre-result Vision from the business Host.
        return {**{key: value for key, value in result.items()
            if key not in {'agent_vision', 'vision_unchanged_reason'}},
            'checkpoint_read_context_id': context['read_context_id']}

    def continue_direction(self, result: Mapping[str, Any], *, purpose: str = 'first_delivery') -> dict[str, Any]:
        if not self.enabled():
            return dict(result)
        path = _receipt_path(self.root, self.identity)
        with exclusive_file_lock(path):
            receipt = None
            verified_context: dict[str, Any] = {}
            if path.exists():
                with self._source_context('checkpoint-direction-readback') as admission:
                    attempt = _inspect_attempt(self.root, self.identity, goal_ref=self.goal_ref)
                    if attempt.get('status') == 'committed':
                        return self._committed_result(result, attempt)
                    receipt = _read_context_receipt(path)
                    try:
                        verified_context = _resolve(self.root, phase='check', identity=self.identity.as_dict(),
                            include_retained_content=True,
                            purpose=purpose, checkpoint_continuation=True,
                            read_context_id=receipt['read_context_id'], receipt=receipt,
                            facts=_local_source_facts(self.root, self.registry, self.state, self.identity,
                                **({'goal_ref': self.goal_ref, 'source_admission': admission}
                                    if self.goal_ref is not None else {})))
                    except CheckpointReadContextRejected as error:
                        if error.code not in {'checkpoint_read_context_stale', 'checkpoint_read_context_unknown_or_replaced'}:
                            raise
                        receipt = None
            # Keep the actual input/decision binding on retry. The commit owner
            # still resolves uncertainty and rechecks current sources.
            if receipt is None or receipt.get('purpose', 'supplement_checkpoint') != purpose:
                context = read_checkpoint_context(registry_path=self.registry, runtime_root_override=str(self.root),
                    goal_id=self.identity.goal_id, agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
                    replan_obligation_id=self.identity.replan_obligation_id, turn_instance_id=self.identity.turn_instance_id,
                    purpose=purpose, goal_ref=self.goal_ref, include_retained_content=True)
                if context.get('status') == 'committed':
                    return self._committed_result(result, context)
                receipt = _read_context_receipt(path)
            else:
                context = {'read_context_id': receipt['read_context_id'], 'basis': receipt['decision_basis'],
                    'settlement_identity': self.identity.as_dict(), 'purpose': purpose,
                    'vision_authoring': receipt['vision_authoring'], 'original_outcome': receipt.get('original_outcome'),
                    **{key: receipt[key] for key in ('retained_result_binding', 'current_progress_qualification',
                        'retained_result_artifacts', 'instructions') if key in receipt},
                    **{key: verified_context[key] for key in ('retained_result_content',) if key in verified_context}}
            decision = receipt.get('current_agent_decision')
            if decision is None:
                dispatch = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_dispatch', 'receipt': receipt, 'explicit_resume': self.explicit_resume})
                if dispatch.get('ok') is not True:
                    raise CheckpointReadContextRejected(dispatch)
                attempt = dispatch['attempt']
                if dispatch.get('reuse_prepared') is not True:
                    receipt['direction_host_attempt_count'] = attempt
                    receipt['direction_host_prepared_attempt'] = attempt
                    receipt.pop('direction_host_failure', None)
                    receipt.pop('direction_host_returned_attempt', None)
                    self._save(path, receipt)
                # Once preparation is consumed, loss of the launch outcome is unknown.
                del receipt['direction_host_prepared_attempt']
                self._save(path, receipt)
                try:
                    decision = self.runner(context)
                except FirstPartyHostRuntimeRejected as error:
                    if error.launch_not_started:
                        failure = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                            'phase': 'direction_launch_failure', 'receipt': receipt,
                            'error_code': error.code, 'not_started': True})
                        if failure.get('ok') is not True:
                            raise CheckpointReadContextRejected(failure) from error
                        self._save(path, failure['receipt'])
                    # A refusal not observed before launch is still unknown;
                    # retaining the prepared attempt must not permit a replay.
                    raise ValueError(str(error)) from error
                except BuiltInHostError as error:
                    receipt['direction_host_failure'] = build_host_failure_record(error.failure_kind,
                        attempt=attempt)
                    self._save(path, receipt)
                    raise ValueError(error.reason) from error
                if (decision.get('result_kind') is None and not decision.get('stop_reason')
                        and decision.get('read_context_id') == receipt['read_context_id']):
                    # Preserve transport completion before the separate decision binding.
                    # This proves the attempt returned without adopting its judgment.
                    receipt['direction_host_returned_attempt'] = attempt
                    self._save(path, receipt)
                bound = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_return', 'identity': self.identity.as_dict(), 'receipt': receipt, 'decision': decision})
                if bound.get('ok') is not True:
                    raise ValueError(bound.get('error', 'Direction response binding rejected'))
                receipt = bound['receipt']
                self._save(path, receipt)
                if bound['stopped']:
                    raise ValueError('Current Agent stopped direction continuation; result retained')
            else:
                bound = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_response', 'identity': self.identity.as_dict(), 'receipt': receipt, 'decision': decision})
                if bound.get('binding') != receipt.get('current_agent_binding'):
                    raise ValueError('Original direction response binding is unavailable')
            return {**result, 'agent_vision': decision.get('agent_vision'),
                'vision_unchanged_reason': decision.get('vision_unchanged_reason'),
                'checkpoint_read_context_id': context['read_context_id']}
