# External installer smoke closeout

Run 37652697889 on executor PR #17 exited 124 after the five-minute installer
limit. The last reported phase was external OpenClaw Linux build-tool installation.
The harness resolved clawdbot 2026.1.24-3 while the downloaded installer identified
itself as OpenClaw; package/installer compatibility is not established by this run.

This change removes Docker pseudo-terminals, sets CI=1 and TERM=dumb for the
root installer process, prints the downloaded installer SHA-256, reports the
actual exit status and retains job output as an artifact. It preserves the
five-minute execution bound, version/help checks, workflow triggers and failure
propagation. No failure is converted into a skip or pass.

This is a noninteractive-harness and diagnostics repair, not evidence that the
external installer succeeds. Docker execution is unavailable in the local
workspace. Shell syntax is checked locally; GitHub CI must validate execution.
The job tests a published third-party installer, not the PR's built package.

PRP is outside this workstream. Worker 21 runtime and hub pins are unchanged.
