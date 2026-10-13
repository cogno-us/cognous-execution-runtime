"""Opt-in synthetic Linux peer-to-text boundary; not a deployed channel adapter."""
from dataclasses import dataclass
import hashlib
import json
import subprocess
from uuid import uuid4

from tools.openappa.local_resume import ResumeRefused, _text, resume


@dataclass(frozen=True)
class ReplyRoute:
    """Trusted host route, never populated from model-supplied identity fields."""
    tenant_id: str
    principal_id: str
    root_id: str
    destination_id: str


def deliver_local_reply(*, peer, registry, session_id, route, text,
                        read_existing, flow_check, deliver):
    """Resolve the kernel-selected binding before checking and sending exact text.

    Callbacks and route configuration are trusted. The delivery callback must bind
    its transport to the supplied destination; returning is only a host observation.
    No retry, result feedback, durable outcome, or check-to-send atomicity is added.
    This synchronous fixture does not supply the TypeScript dispatcher's deadline.
    """
    try:
        for value in (route.tenant_id, route.principal_id, route.root_id, route.destination_id):
            _text(value)
        if not isinstance(text, str) or not text or len(text.encode('utf-8')) > 16384:
            return 'output-refused'
        binding, _ = resume(peer, registry, session_id, read_existing=read_existing)
        if (route.tenant_id, route.principal_id, route.root_id) != (
            binding.tenant_id, binding.principal_id, binding.root_id
        ):
            return 'host-refused'
    except (ResumeRefused, OSError, ValueError, subprocess.SubprocessError):
        return 'host-refused'
    raw = json.dumps({'outputId': str(uuid4()), 'kind': 'final',
                      'destinationId': route.destination_id, 'text': text},
                     ensure_ascii=False, separators=(',', ':')).encode()
    expected = {'requestSha256': hashlib.sha256(raw).hexdigest(), 'outcome': 'allow'}
    try:
        if flow_check(binding, raw) != expected:
            return 'flow-refused'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'flow-unavailable'
    try:
        deliver(binding, route.destination_id, text)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'delivery-unknown'
    return 'delivered'
