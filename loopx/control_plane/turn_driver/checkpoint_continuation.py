"""Current-Agent IO for the existing Goal checkpoint and Turn receipt owners."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...file_lock import exclusive_file_lock, exclusive_cross_runtime_file_lock
from ...registry import atomic_write_json
from ..goals.checkpoint_context_io import (
    _checkpoint_effect, _receipt_path, _read_context_receipt, _resolve,
    _local_source_facts, _source_guard, _inspect_attempt, CheckpointReadContextRejected,
    read_checkpoint_context, checkpoint_continuation_required,
)
from ..quota.settlement import SettlementIdentity, read_heartbeat_settlement
from .host_failure import BuiltInHostError, build_host_failure_record


class CurrentAgentCheckpoint:
    def __init__(self, registry: Path, root: Path, state: Path, identity: SettlementIdentity,
                 runner: Callable[[dict[str, Any]], dict[str, Any]], *, explicit_resume: bool = False):
        self.registry, self.root, self.state, self.identity, self.runner = registry, root, state, identity, runner
        self.explicit_resume = explicit_resume
        self._enabled: bool | None = None

    def enabled(self) -> bool:
        if self._enabled is not None:
            return self._enabled
        readback = read_heartbeat_settlement(self.root, goal_id=self.identity.goal_id,
            agent_id=self.identity.agent_id, todo_id=self.identity.todo_id,
            turn_instance_id=self.identity.turn_instance_id,
            replan_obligation_id=self.identity.replan_obligation_id, registry_path=self.registry)
        self._enabled = readback is not None and checkpoint_continuation_required(readback)
        return self._enabled

    def _save(self, path: Path, receipt: dict[str, Any]) -> None:
        with exclusive_cross_runtime_file_lock(self.root / 'goals' / self.identity.goal_id / 'runs/index.jsonl',
                operation='checkpoint-direction-binding'):
            with _source_guard(self.root, self.identity.goal_id, self.state, self.registry):
                current = _read_context_receipt(path)
                if current.get('read_context_id') != receipt['read_context_id']:
                    raise ValueError('Direction input was replaced while the current Agent was judging')
                atomic_write_json(path, receipt)

    def continue_direction(self, result: Mapping[str, Any], *, purpose: str = 'first_delivery') -> dict[str, Any]:
        if not self.enabled():
            return dict(result)
        path = _receipt_path(self.root, self.identity)
        with exclusive_file_lock(path):
            receipt = _read_context_receipt(path) if path.exists() else None
            if receipt is not None:
                with exclusive_cross_runtime_file_lock(self.root / 'goals' / self.identity.goal_id / 'runs/index.jsonl',
                        operation='checkpoint-direction-readback'):
                    with _source_guard(self.root, self.identity.goal_id, self.state, self.registry):
                        attempt = _inspect_attempt(self.root, self.identity)
                        if attempt.get('status') != 'committed':
                            if attempt.get('status') == 'absent':
                                # Native readback cleared a proved-absent append.
                                # Consume its persisted receipt, not the old marker.
                                receipt = _read_context_receipt(path)
                            try:
                                _resolve(self.root, phase='check', identity=self.identity.as_dict(),
                                    purpose=purpose, checkpoint_continuation=True,
                                    read_context_id=receipt['read_context_id'], receipt=receipt,
                                    facts=_local_source_facts(self.root, self.registry, self.state, self.identity))
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
                    purpose=purpose)
                receipt = _read_context_receipt(path)
            else:
                context = {'read_context_id': receipt['read_context_id'], 'basis': receipt['decision_basis'],
                    'settlement_identity': self.identity.as_dict(), 'purpose': purpose,
                    'vision_authoring': receipt['vision_authoring'], 'original_outcome': receipt.get('original_outcome')}
            decision = receipt.get('current_agent_decision')
            if decision is None:
                dispatch = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_dispatch', 'receipt': receipt, 'explicit_resume': self.explicit_resume})
                if dispatch.get('ok') is not True:
                    raise CheckpointReadContextRejected(dispatch)
                receipt['direction_host_attempt_count'] = dispatch['attempt']
                self._save(path, receipt)
                try:
                    decision = self.runner(context)
                except BuiltInHostError as error:
                    receipt['direction_host_failure'] = build_host_failure_record(error.failure_kind,
                        attempt=dispatch['attempt'])
                    self._save(path, receipt)
                    raise ValueError(error.reason) from error
                if decision.get('result_kind') in {'wait', 'user_action_required', 'iteration_failed'}:
                    # A valid Host response supersedes the prior transport
                    # failure; retain the total attempt count for future failures.
                    receipt.pop('direction_host_failure', None)
                    receipt['direction_stopped'] = True
                    receipt['direction_stops'] = [*receipt.get('direction_stops', []), {
                        'attempt': dispatch['attempt'], 'result_kind': decision['result_kind'],
                        **({'stop_reason': decision['stop_reason']} if decision.get('stop_reason') else {})}]
                    self._save(path, receipt)
                    raise ValueError('Current Agent stopped direction continuation; result retained')
                bound = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_response', 'identity': self.identity.as_dict(), 'receipt': receipt, 'decision': decision})
                if bound.get('ok') is not True:
                    raise ValueError(bound.get('error', 'Direction response binding rejected'))
                receipt = {**receipt, 'current_agent_decision': decision, 'current_agent_binding': bound['binding']}
                receipt.pop('direction_host_failure', None)
                receipt.pop('direction_stopped', None)
                self._save(path, receipt)
            else:
                bound = _checkpoint_effect('goal.checkpoint_read_context.work_binding', {
                    'phase': 'direction_response', 'identity': self.identity.as_dict(), 'receipt': receipt, 'decision': decision})
                if bound.get('binding') != receipt.get('current_agent_binding'):
                    raise ValueError('Original direction response binding is unavailable')
            return {**result, 'agent_vision': decision.get('agent_vision'),
                'vision_unchanged_reason': decision.get('vision_unchanged_reason'),
                'checkpoint_read_context_id': context['read_context_id']}
