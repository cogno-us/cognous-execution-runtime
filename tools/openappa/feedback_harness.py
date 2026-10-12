"""Successful-result feedback for the synthetic local conjunction only."""
from dataclasses import dataclass
import hashlib
import json
import subprocess

from tools.openappa.local_join_harness import execute_local_joined


@dataclass(frozen=True)
class FeedbackOutcome:
    execution_status: str
    feedback_status: str


def execute_with_feedback(*, executor, flow_check, report_success, **arguments):
    """Never retry execution when feedback is unavailable or unconfirmed.

    The trusted callback receives the same enrolled binding and exact request
    used for flow checking. Partial/unknown/denied results are not success.
    This is not a durable outbox or an outcome delivery guarantee.
    """
    captured = {}
    class CaptureExecutor:
        def execute(self, **kwargs):
            result = executor.execute(**kwargs)
            captured['result'] = result
            return result
    def flow(binding, raw):
        captured.update(binding=binding, raw=raw)
        return flow_check(binding, raw)
    status = execute_local_joined(executor=CaptureExecutor(), flow_check=flow, **arguments)
    result = captured.get('result')
    if result is None or result.status not in ('executed', 'reconciled') or result.observed_state != 'applied':
        return FeedbackOutcome(status, 'not-reported')
    raw = captured['raw']
    request = json.loads(raw)
    if (result.decision_id, result.effect_id, result.attempt_id) != (
        request['decision_id'], request['context']['effect_id'], request['context']['attempt_id']
    ):
        return FeedbackOutcome(status, 'unconfirmed')
    try:
        receipt = report_success(captured['binding'], raw)
        expected = {'request_sha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'ack'}
        confirmed = receipt == expected
    except (OSError, ValueError, subprocess.SubprocessError):
        confirmed = False
    return FeedbackOutcome(status, 'acknowledged' if confirmed else 'unconfirmed')
