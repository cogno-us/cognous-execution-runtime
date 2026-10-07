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

## Follow-up after run 37654389072

The noninteractive installer completed and installed OpenClaw 2026.9.8. The
harness then failed because it expected clawdbot/moltbot. The workflow now
explicitly selects the upstream `openclaw` package, and the Docker wrapper
forwards that selection to both root and non-root checks. An explicit package
selection cannot fall back to another product. Exact version equality and CLI
help execution remain mandatory; this checks the external upstream installer,
not the Cognous build. Shell syntax can be checked locally; the revised Docker
execution remains pending CI. No accepted component pins are advanced.

Run 37662349253 completed installation in under one minute but rejected the
upstream version banner `OpenClaw 2026.9.8 (fc23bc8)` against `2026.9.8`.
The harness now recognizes only the named OpenClaw banner with a hexadecimal
build revision, then retains exact version equality. Wrong products, malformed
banners and mismatched versions remain failures. CLI help must still succeed.

## Clean restart

Recovered from closed PR #20 head 6399bc9e1a01a82fe154f96f15ed233fbd977892
onto main f6435736089d3482cd9c785c8c5d162c00c0fdeb. The four executable/workflow
files are reused unchanged. Source-head Install Smoke run 37662883500 succeeded.
Current-main Install Smoke run 37670868737 failed. Current main differs from the
previous baseline e0c127178247fbe33ec2c80997c464575738be1e only in README.md.

This establishes an installer baseline defect, not the cause of every application
CI failure. Baseline CI run 37646874272 also failed on Windows, but in
pi-embedded-runner.test.ts; it does not prove equivalence to the gateway timeout.
Fresh-head CI remains pending. Worker 21 runtime and diagnostic gateway changes
are excluded. No accepted dependency pins or required checks are changed.
