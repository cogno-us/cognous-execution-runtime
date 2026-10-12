# Focused sharp restart

Predecessor PR #28 is closed, unmerged. Its reviewed head was
b27d798ff834f022e2f4681ca535d57be7fb1be1. This branch preserves that candidate and its
lockfile, adding focused qualification before opening another PR. Main and
accepted hub pins remain unchanged. Existing application CI is unchanged.

Build job 113084870010 rejected the Sharp module namespace as callable. Sharp 0.35.5 declares its ESM constructor as the default export, verified against the upstream tagged build script. The loader type now names that export. Image-tool job 113084869998 failed PNG optimization. Its test fixture has both an invalid IDAT CRC and a failing zlib checksum; replace it with a valid 1x1 RGBA PNG without relaxing production decoder behavior. New native tests exercise PNG optimization and JPEG conversion through the production functions.

## Qualification process

The push-only workflow targets this exact preparation branch. Linux runs first
with bounded install and test steps. Type checking and image tests are separate steps.
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

## First focused run

Run 37710162293 passed frozen installation and exposed the removed Sharp
`failOnError` option during type checking; runtime steps did not execute.
Upstream v0.34.5 input.js maps `failOnError: false` to `failOn: "none"`.
The restart now uses that equivalent supported option, preserving the prior
input-tolerance behavior rather than weakening it further. Requalification is
required on the amended head.
