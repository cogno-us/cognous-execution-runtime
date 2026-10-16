# Quarantine of the upstream Moltbot application (2026-10-16)

Status: owner decision implemented on `main`. This is a scope change only. It is not a security clearance, certification, release or production authorization.

## Decision

The repository owner decided to **quarantine** the upstream Moltbot TypeScript/native application formerly retained in this repository: [cognous-open-control-stack issue #68, comment 6214239202](https://github.com/cogno-us/cognous-open-control-stack/issues/68#issuecomment-6214239202).

That application was already outside the reviewed Cognous execution boundary (see [execution-boundary.md](execution-boundary.md)). It had 18 confirmed, unrepaired security findings, 6 of them high severity.

## Preserved state

| Item | Value |
| --- | --- |
| Tag | `upstream-moltbot-quarantine-20261016` (annotated) |
| Preserved commit | `fd6b76fa0e85feb5b67324114bf790d3a12fdaf1` (the former `main`) |
| License of preserved upstream code | MIT, Copyright (c) 2025 Peter Steinberger ([LICENSE](../LICENSE)) |

The tag preserves the exact tree, which includes the upstream application. The preserved application is **not maintained**, **not reviewed** and **not part of the V1 boundary**. Do not build, deploy or run it as part of the Cognous stack.

Removing the application from `main` does not erase it. Every prior commit stays in public Git history and remains reachable from the tag.

## What remains on `main`

- the Python execution boundary under `engine/` and its tests in `tests/`;
- examples, including the optional OpenShell worker image;
- Cognous-authored documentation, workstream records and collateral;
- Python CI and the thin wrappers that call pinned cross-component qualification workflows;
- license and notice files.

The upstream source and build tooling were removed, along with its upstream documentation pages and the CI that built or tested the TypeScript/native application.

## Unrepaired residual findings in the quarantined code

These findings from the 2026-10-16 scan apply only to the code preserved at the tag and in history. None of them was repaired. They are recorded here as residuals of quarantined code, not as fixed or accepted.

| Finding ID | Severity | Area |
| --- | --- | --- |
| `csf_f1d78919474a50d56f6501fc` | high | `src/` exec allowlist |
| `csf_cb9780cbbc1916154b9b7adf` | high | `src/` safe-bin PATH trust |
| `csf_abe57b7b880e060d70f923fa` | high | `src/` gateway pairing scope |
| `csf_cfc9f6c95ae9d365547a1e99` | high | `src/` MEDIA directive file exfiltration |
| `csf_bf4bdb4a125c198aa6fbb11e` | high | `src/` Telegram webhook secret |
| `csf_2c83d477dd26972a98c91a24` | high | `extensions/` Zalo group authorization |
| `csf_5c8c13f448c80d28f610c2ec` | medium | `src/` workspace hooks |
| `csf_37bacbc5e0bf44a841034eae` | medium | `apps/` Android TLS pin discovery |
| `csf_044741d40baf403a6ecfa65a` | medium | `apps/` native canvas local files |
| `csf_d28c94313ca4ef5dcc1a513e` | medium | `src/` attachment buffering limits |
| `csf_eeaedd9edd788bbe92d53769` | medium | `src/` canvas route authentication |
| `csf_21472b00675c36e9dd79ef3a` | medium | `src/` pairing store account scope |
| `csf_fe00993c170ce26bfa3976fb` | medium | `extensions/` Zalo Personal routing |
| `csf_0c009747a0adfb0296a309d1` | medium | `extensions/` Nextcloud Talk replay |
| `csf_7ebc8c9d8b8e4f6e04439de7` | medium | `apps/` iOS canvas action bridge |
| `csf_d4914b7c1ad2063a3aa3a406` | medium | `apps/` macOS canvas scheme containment |
| `csf_f69507a105a5af002b0b0207` | medium | `apps/` cleartext gateway credentials |
| `csf_c1d36746f25f4ac9dcae56d1` | low | `src/` plugin discovery shadowing |

Findings in `engine/` are handled separately and are not covered by this quarantine.

## Limits

- Historical records under `docs/workstreams/` and in `CHANGELOG.md` may still link to or describe removed upstream paths. Those links resolve at the tag. The records are not rewritten.
- The qualification workflow `cognous-execution-runtime--openappa-reply-admission.yml` exercised upstream TypeScript reply code. It cannot run against `main` after this change, and its source wrapper was removed. It still applies to the tag.
- This change modifies a candidate component revision. It requires F3 requalification, refreeze and re-review before acceptance. It grants no release or production authorization.
