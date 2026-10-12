# Focused dompurify restart

Predecessor PR #27 is closed, unmerged. Its reviewed head was
bbf8d2ac5daf8999159922d47b7d628b227907b1. This branch preserves that candidate and its
lockfile, adding focused qualification before opening another PR. Main and
accepted hub pins remain unchanged. Existing application CI is unchanged.

Windows job 113089757485 failed with EBUSY during pi-embedded-runner temporary directory cleanup and an unexpected worker exit. Linux application jobs passed. This does not establish DOMPurify caused the Windows failures. The proposed bounded cleanup retry handles transient locks but still fails on persistent locks. The worker exit remains an independent acceptance question.

## Qualification process

The push-only workflow targets this exact preparation branch. Linux runs first
with bounded install and test steps. A separate Windows job then runs only the embedded-runner suite with one worker.
No whole-application PR matrix is started yet. No test is removed or converted
to a fabricated pass. Queue time is outside GitHub job execution timeouts.

Local checks cover workflow structure, the bounded cleanup setting, and PNG
chunk/decompression integrity. Full dependency installation is not rerun in the
local environment where dependency policy previously blocked libsignal.
GitHub focused results are pending at commit creation. A new acceptance PR
requires review of those results and all remaining applicable platform/runtime
checks. Focused success alone is not full repository qualification.

Active predecessor workflow cancellation was unavailable through the connector;
the browser was signed out. Closing the PR does not prove its active jobs stopped.
