# Focused music-metadata lock update

Base: `8e7d634cf0a22b6f3c1fc6d93afe92c7414fa243`.
Original PR #37 head: `afe9cceea2c6fe6d0e925d509e360352835a2776`.

The original lock regeneration changed unrelated libsignal, protobuf, audio
and sharp resolutions. This candidate starts with accepted main and copies only
the new music-metadata 11.16.0 package/snapshot closure from that reviewed PR.
It updates the Matrix importer and both Baileys references. Matrix's ^11.10.6
and Baileys' ^11.7.0 ranges admit the selected version; manifests stay unchanged.

Eight new package/snapshot entries are required: music-metadata 11.16.0,
@borewit/text-codec 0.2.2, content-type 2.1.0, file-type 21.3.4,
media-typer 2.0.0, strtok3 10.3.5, uint8array-extras 1.6.0 and win-guid 0.2.1.
The unused music-metadata 11.10.6 entry is removed. Existing shared parser
versions remain for other consumers. All other existing package records and
snapshot resolutions are semantically identical to main. The lock diff is
71 additions and 20 deletions; no dependency override or patch is added.

## Validation checkpoint

Node 24.19.0 and repository-pinned pnpm 10.23.0:

- Frozen installation passed; root/UI/graph static preflight passed.
- pnpm lint passed: 2517 files, zero warnings/errors.
- pnpm build passed, including A2UI bundling.
- Matrix extension suite passed: eight files, 22 tests, one worker.
- Actual installed music-metadata parsed a generated one-second PCM WAV;
  duration, sample rate and channel count assertions passed.
- Semantic lock comparison and whitespace check passed.

An early composition trial removed the old parser before updating Baileys;
the graph check rejected both missing references. The final candidate updates
those references and passes the graph check. Frozen installation alone is not
used as proof of complete transitive graph coverage.

These are scoped results, not full application qualification. The full local
landing gate remains unmet: this execution environment denies Node network
interface enumeration, as independently reproduced during PR #29 validation.
No full-suite pass, security clearance or release adoption is claimed. Keep
PR #37 unmerged until its integrated-head CI and required local gate qualify.
