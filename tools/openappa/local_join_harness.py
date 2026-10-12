"""Synthetic local-host conjunction; not a service or production adapter."""
import copy
import subprocess

from tools.openappa.conjunction_harness import execute_joined
from tools.openappa.local_resume import ResumeRefused, resume


def execute_local_joined(*, peer, registry, session_id, read_existing, flow_check,
                         request, host, executor, proposal, decision, now):
    """Trusted fixture callbacks consume the persisted, kernel-selected binding.

    read_existing only checks status. flow_check(binding, raw) uses that store
    for a fresh correlated decision. Neither callback may dispatch an effect.
    The directory owner and host configuration remain trusted; this does not
    serialize lifecycle changes or close the flow-check-to-effect race.
    """
    frozen_host = copy.deepcopy(host)
    frozen_request = copy.deepcopy(request)
    try:
        binding, _ = resume(peer, registry, session_id, read_existing=read_existing)
        if (frozen_host['tenant_id'], frozen_host['principal_id'], frozen_host['root_id']) != (
            binding.tenant_id, binding.principal_id, binding.root_id
        ):
            return 'host-refused'
    except ResumeRefused:
        return 'host-refused'
    except (OSError, ValueError, subprocess.SubprocessError):
        return 'host-unavailable'
    return execute_joined(request=frozen_request, host=frozen_host, executor=executor,
                          proposal=proposal, decision=decision, now=now,
                          flow_check=lambda raw: flow_check(binding, raw))
