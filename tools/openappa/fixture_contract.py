"""Offline fixture integrity checks; never an authorization or dispatch gate."""
import hashlib
import json
from engine.operation_admission import OperationDiscriminator, OperationAttempt

UPSTREAM_SHA = '10aa0e09e32d8c6704bdb030dc3c66140bf81915'


def validate_fixture(bundle):
    if bundle['upstream_sha'] != UPSTREAM_SHA or bundle['authority_effect'] != 'none':
        raise ValueError('unrecognized source or authority claim')
    request = bundle['request_json'].encode('utf-8')
    if bundle['request_sha256'] != hashlib.sha256(request).hexdigest():
        raise ValueError('request commitment mismatch')
    wire = json.loads(request)
    # Cognous context is out-of-band, never invented upstream wire fields.
    expected = {'protocol', 'adapter', 'event', 'root_id', 'tool', 'call_id', 'arguments'}
    if set(wire) != expected or wire['protocol'] != 1 or wire['adapter'] != 'kagent':
        raise ValueError('unsupported fixture envelope')
    context = bundle['host_context']
    keys = {'tenant_id', 'principal_id', 'authority_domain', 'operation_id',
            'attempt_id', 'effect_id', 'root_id', 'call_id', 'tool'}
    if set(context) != keys or any(not isinstance(v, str) or not v.strip() for v in context.values()):
        raise ValueError('incomplete host context')
    if any(context[k] != wire[k] for k in ('root_id', 'call_id', 'tool')):
        raise ValueError('host/wire correlation mismatch')
    if context != bundle['expected_host_context']:
        raise ValueError('fixture identity substitution')
    operation = OperationDiscriminator(context['principal_id'], context['authority_domain'], context['operation_id'])
    attempt = OperationAttempt(operation, context['attempt_id'])
    return operation, attempt
