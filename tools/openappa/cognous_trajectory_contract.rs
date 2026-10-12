//! Qualification of actual embedded SQLite trajectory replay, not a host adapter.
use std::{path::Path, sync::Arc, time::Duration};
use appa_eventlog::{Backend, LogStore};
use appa_runtime::{api::{RemedyOutcome, Runtime}, config::{Config, HostDefaults}, hooks};
use appa_runtime_api::{Actor, Adapter, AdapterName, CanonicalTool, HookDecision,
    HookEvent, IdentifiedTool, OutcomeBody, ParseRefusal, ProposedCall, ToolOutcome, TrajectoryId};

const POLICY: &str = r#"
[policy]
version = 2
[[policy.tool]]
name = "host/cognous_fixture/read_secret"
delta = { audience = ["synthetic-reader@example.test"] }
[[policy.tool]]
name = "host/cognous_fixture/send_public"
requires = { audience = { contains = ["public"] } }
effects = ["egress"]
delta = {}
"#;

fn identify(raw: &str) -> Result<IdentifiedTool, ParseRefusal> {
    let canonical = match raw {
        "review" => CanonicalTool::control(),
        "read_secret" | "send_public" => CanonicalTool::of("host", "cognous_fixture", raw).unwrap(),
        _ => return Err(ParseRefusal::Malformed { detail: "unknown fixture tool".into() }),
    };
    Ok(IdentifiedTool { canonical, spawn: None })
}
const HOST: Adapter = Adapter {
    name: AdapterName::Embedded, identify_tool: identify,
    names_children: |_, _| vec![], wildcard_covers_spawn: false, spells_server: |_| false,
    spell: |tool| {
        if tool.is_control() { Some("review".into()) }
        else { tool.as_str().strip_prefix("host/cognous_fixture/").map(str::to_string) }
    },
};

fn open(path: &Path) -> Runtime {
    let config = Config::hosted(POLICY, HostDefaults::new(Duration::from_secs(5), 65536),
        |_| None).unwrap();
    let store = Arc::new(LogStore::open(Backend::Sqlite { path: path.to_path_buf() }).unwrap());
    Runtime::open_with_store_as(config, store, None, HOST).unwrap()
}
fn actor() -> Actor {
    Actor { root: TrajectoryId("synthetic-persistent-root".into()), child: None }
}
async fn start(runtime: &Runtime) {
    assert_eq!(hooks::handle(runtime, HookEvent::SessionStart {
        root: actor().root, principal: None, address: None, title: None,
        launch: None, start: None,
    }).await, HookDecision::Ack);
}
fn call(name: &str) -> ProposedCall {
    ProposedCall { tool: identify(name).unwrap().canonical.into_string(),
        arguments: serde_json::value::RawValue::from_string("{}".into()).unwrap(), cwd: None }
}
async fn propose(runtime: &Runtime, name: &str, id: &str) -> HookDecision {
    hooks::handle(runtime, HookEvent::ToolCall { actor: actor(), call: call(name),
        call_id: Some(id.into()), spawn: None, prompt: None, ruling: None }).await
}
async fn report(runtime: &Runtime, name: &str, id: &str) {
    assert_eq!(hooks::handle(runtime, HookEvent::ToolResult {
        actor: actor(), call: call(name), call_id: Some(id.into()),
        outcome: ToolOutcome::Success { body: OutcomeBody::Available("synthetic-data".into()) },
    }).await, HookDecision::Ack);
}
async fn public_allowed(runtime: &Runtime, id: &str) {
    assert_eq!(propose(runtime, "send_public", id).await, HookDecision::AllowCall { spawn: None });
    report(runtime, "send_public", id).await;
}
async fn public_denied(runtime: &Runtime, id: &str) {
    let result = propose(runtime, "send_public", id).await;
    assert!(matches!(result, HookDecision::DenyCall { .. }), "{result:?}");
}
async fn narrow(runtime: &Runtime) {
    let HookDecision::DenyCall { offers, .. } = propose(runtime, "read_secret", "read-1").await else {
        panic!("restricted read must first require acceptance");
    };
    assert!(!offers.is_empty());
    // Only synthetic narrowing consent. No authority override or external remedy.
    let plan = serde_json::json!({"offer_id": offers[0].id});
    assert_eq!(hooks::handle(runtime, HookEvent::ToolCall {
        actor: actor(), call_id: Some("accept-1".into()),
        call: ProposedCall { tool: appa_runtime_api::CONTROL_TOOL.into(), cwd: None,
            arguments: serde_json::value::RawValue::from_string(plan.to_string()).unwrap() },
        spawn: None, prompt: None, ruling: None,
    }).await, HookDecision::PassControl);
    assert!(matches!(runtime.execute_embedded_remedy(&actor(),
        serde_json::from_value(plan).unwrap()).await, RemedyOutcome::Authorized { .. }));
    assert_eq!(propose(runtime, "read_secret", "read-1").await, HookDecision::AllowCall { spawn: None });
    report(runtime, "read_secret", "read-1").await;
}

#[tokio::test]
async fn accumulated_restriction_survives_sqlite_runtime_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("trajectory.sqlite");
    {
        let runtime = open(&path);
        start(&runtime).await;
        public_allowed(&runtime, "before-read").await;
        narrow(&runtime).await;
        public_denied(&runtime, "after-read").await;
    }
    let reopened = open(&path);
    public_denied(&reopened, "after-reopen").await;
    // Same root, adapter and policy, but no history: a control for accumulated state.
    let fresh = open(&dir.path().join("fresh.sqlite"));
    start(&fresh).await;
    public_allowed(&fresh, "fresh-control").await;
}

#[tokio::test]
async fn repeated_session_start_does_not_reset_the_reopened_root() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("trajectory.sqlite");
    {
        let runtime = open(&path);
        start(&runtime).await;
        narrow(&runtime).await;
    }
    let reopened = open(&path);
    start(&reopened).await;
    public_denied(&reopened, "after-repeated-start").await;
}

#[test]
fn malformed_sqlite_is_refused_instead_of_replaced() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("trajectory.sqlite");
    std::fs::write(&path, b"synthetic-invalid-database").unwrap();
    assert!(LogStore::open(Backend::Sqlite { path: path.clone() }).is_err());
    assert_eq!(std::fs::read(&path).unwrap(), b"synthetic-invalid-database");
}
