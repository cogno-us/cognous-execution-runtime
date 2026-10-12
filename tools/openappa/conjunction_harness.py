"""Synthetic integration harness only. Not an authenticated runtime adapter."""
import copy
import hashlib
import json
import subprocess

from engine.operation_admission import OperationAttempt, OperationDiscriminator
from engine.safe_executor import snapshot_envelope


def encode_request(request, host):
    """Host is separately configured by the test owner, never model input."""
    op = snapshot_envelope(request).operation
    if (host['principal_id'], host['authority_domain'], host['institution_id']) != (
        op.principal, op.authority_domain, op.institution_id
    ):
        raise ValueError('host identity mismatch')
    if request.attempt_id != host['attempt_id'] or request.effect_id != host['effect_id']:
        raise ValueError('host attempt/effect mismatch')
    if any(not isinstance(value, str) or not value.strip() for value in host.values()):
        raise ValueError('missing host identity')
    OperationAttempt(OperationDiscriminator(host['principal_id'], host['authority_domain'],
                                           host['operation_id']), host['attempt_id'])
    return json.dumps({'context': host, 'tool': 'host/cognous_fixture/refund',
                       'arguments': op.canonical_dict(), 'decision_id': request.decision_id},
                      sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def probe(binary, policy, request_bytes):
    completed = subprocess.run([str(binary), policy], input=request_bytes,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=15, check=True)
    if len(completed.stdout) > 4096:
        raise ValueError('oversized response')
    return json.loads(completed.stdout)


def execute_joined(*, request, host, executor, proposal, decision, now, flow_check):
    """Bind one private-pipe response to one snapshot, then revalidate authority.

    The trusted test runner owns flow_check and host. This is not a public API,
    process attestation, durable trajectory, or reusable flow permit.
    """
    frozen_request = copy.deepcopy(request)
    request_bytes = encode_request(frozen_request, copy.deepcopy(host))
    try:
        response = flow_check(request_bytes)
        if (not isinstance(response, dict)
                or set(response) != {'request_sha256', 'outcome'}
                or response['request_sha256'] != hashlib.sha256(request_bytes).hexdigest()
                or response['outcome'] != 'allow'):
            return 'flow-refused'
    except (ValueError, OSError, subprocess.SubprocessError):
        return 'flow-unavailable'
    return executor.execute(envelope=frozen_request, proposal=proposal,
                            decision=decision, now=now).status
