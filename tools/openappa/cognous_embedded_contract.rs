//! Actual pinned-runtime qualification only; no Cognous authority or destination.
use std::{sync::Arc, time::Duration};

use appa_eventlog::{Backend, LogStore};
use appa_runtime::{api::Runtime, config::{Config, HostDefaults}, hooks};
use appa_runtime_api::{Actor, Adapter, AdapterName, CanonicalTool, HookDecision,
    HookEvent, IdentifiedTool, ParseRefusal, ProposedCall, TrajectoryId};

const TOOL: &str = "host/cognous_fixture/refund";
const ALLOW: &str = r#"
[policy]
version = 2
[[policy.tool]]
name = "host/cognous_fixture/refund"
delta = {}
"#;
const DENY: &str = r#"
[policy]
version = 2
[[policy.tool]]
name = "host/cognous_fixture/refund"
delta = { audience = ["internal"] }
requires = { audience = { within = ["internal"] } }
"#;

fn identify(raw: &str) -> Result<IdentifiedTool, ParseRefusal> {
    let canonical = match raw {
        "refund" => CanonicalTool::parse(TOOL).unwrap(),
        "review" => CanonicalTool::control(),
        _ => return Err(ParseRefusal::Malformed { detail: "unsupported fixture tool".into() }),
    };
    Ok(IdentifiedTool { canonical, spawn: None })
}

const HOST: Adapter = Adapter {
    name: AdapterName::Embedded,
    identify_tool: identify,
    names_children: |_, _| vec![],
    spell: |tool| {
        if tool.is_control() { Some("review".into()) }
        else if tool.as_str() == TOOL { Some("refund".into()) }
        else { None }
    },
    wildcard_covers_spawn: false,
    spells_server: |_| false,
};

fn runtime(policy: &str) -> Runtime {
    let config = Config::hosted(policy, HostDefaults::new(Duration::from_secs(5), 65536),
        |_| None).expect("fixture policy parses without credentials");
    let store = Arc::new(LogStore::open(Backend::Memory).unwrap());
    Runtime::open_with_store_as(config, store, None, HOST).expect("embedded host opens")
}

async fn start(runtime: &Runtime) -> Actor {
    let actor = Actor { root: TrajectoryId("synthetic-root".into()), child: None };
    assert_eq!(hooks::handle(runtime, HookEvent::SessionStart {
        root: actor.root.clone(), principal: None, address: None, title: None,
        launch: None, start: None,
    }).await, HookDecision::Ack);
    actor
}

fn call(actor: Actor, tool: &str) -> HookEvent {
    HookEvent::ToolCall {
        actor, call_id: Some("synthetic-call".into()),
        call: ProposedCall {
            tool: tool.into(), cwd: None,
            arguments: serde_json::value::RawValue::from_string(
                r#"{"amount":50,"target":"refund.sqlite"}"#.into()).unwrap(),
        },
        spawn: None, prompt: None, ruling: None,
    }
}

#[tokio::test]
async fn actual_runtime_allows_the_declared_unrestricted_call() {
    let runtime = runtime(ALLOW);
    let actor = start(&runtime).await;
    assert_eq!(hooks::handle(&runtime, call(actor, TOOL)).await,
        HookDecision::AllowCall { spawn: None });
}

#[tokio::test]
async fn actual_runtime_denies_the_restricted_call_without_executing_a_remedy() {
    let runtime = runtime(DENY);
    let actor = start(&runtime).await;
    let decision = hooks::handle(&runtime, call(actor, TOOL)).await;
    assert!(matches!(decision, HookDecision::DenyCall { .. }), "{decision:?}");
}

#[tokio::test]
async fn actual_runtime_refuses_an_unregistered_tool() {
    let runtime = runtime(ALLOW);
    let actor = start(&runtime).await;
    let decision = hooks::handle(&runtime, call(actor, "host/cognous_fixture/other")).await;
    assert!(matches!(decision, HookDecision::Refuse { .. }), "{decision:?}");
}

#[tokio::test]
async fn independent_stores_do_not_share_allowance_for_the_same_root_and_call() {
    let allowing = runtime(ALLOW);
    let denying = runtime(DENY);
    let first = start(&allowing).await;
    let second = start(&denying).await;
    assert_eq!(hooks::handle(&allowing, call(first, TOOL)).await,
        HookDecision::AllowCall { spawn: None });
    let decision = hooks::handle(&denying, call(second, TOOL)).await;
    assert!(matches!(decision, HookDecision::DenyCall { .. }), "{decision:?}");
}
