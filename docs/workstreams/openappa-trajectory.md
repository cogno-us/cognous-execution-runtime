# Persistent OpenAPPA trajectory qualification candidate

> Historical workstream note. See the [current OpenAPPA status](../../tools/openappa/README.md)
> for consolidated evidence, current scope and remaining work.

Runtime base `db6b06dc3c8f22f265a633ad20d4ed143c2984ff`, after synthetic conjunction
PR #50. Upstream remains `10aa0e09e32d8c6704bdb030dc3c66140bf81915`.

The conjunction probe opens a fresh in-memory runtime for each call and therefore
cannot preserve restrictions accumulated by earlier calls. This separate test
candidate exercises the real embedded runtime with `LogStore` and
`Backend::Sqlite`, without changing that probe or installing a runtime adapter.

Three proposed tests cover:

- Public send is allowed before a synthetic restricted read. The test accepts
  the upstream narrowing offer, admits the synthetic read result, and verifies
  public send is then denied. After dropping and reopening the runtime against
  the same database, public send must remain denied. A fresh database with the
  same root and policy must allow it, distinguishing accumulated history from
  a policy that always denies.
- Repeating `SessionStart` for the existing root after reopening must preserve
  the denial.
- Opening a malformed SQLite database must fail without replacing its bytes.

The narrowing acceptance is test-owned consent to stricter flow constraints;
it is not an institutional grant, authority override or external remedy. Only
synthetic output is reported. No real send or destination effect is performed.

These are candidate assertions pending actual hosted compilation and execution.
Local Rust is unavailable. The earlier embedded and conjunction test results
do not establish this persistence behavior. Any failure must be investigated,
not turned into a passing test by weakening the expected restriction.

## Remaining boundaries

Dropping and reopening a runtime object is not a hard process kill, power-loss
recovery or distributed-store test. Host identity, database path, policy and
root are supplied by the trusted fixture. A missing or substituted database and
a newly minted root can still require host-level refusal; these tests do not
implement that durable association or qualify production tenant isolation.

Next work requires an authenticated host contract and durable tenant-to-root-to-
store association, with fail-closed missing-state handling, before this can be
joined to the synthetic authority/destination path as an opt-in adapter.
Output admission coverage, credentials and production activation remain outside
this gate. Selected locks are unchanged. Preserve C0 check-to-commit limits,
optional same-host C1, no C2/C3, no real settlement and operational #30 HOLD.
