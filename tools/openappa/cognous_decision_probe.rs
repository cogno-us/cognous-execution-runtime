//! Test-only one-call probe; fresh store per call, no production session semantics.
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

async fn start(runtime: &Runtime, root: &str) -> Actor {
    let actor = Actor { root: TrajectoryId(root.into()), child: None };
    assert_eq!(hooks::handle(runtime, HookEvent::SessionStart {
        root: actor.root.clone(), principal: None, address: None, title: None,
        launch: None, start: None,
    }).await, HookDecision::Ack);
    actor
}

#[tokio::main]
async fn main() {
    use std::io::{Read, Write};
    use sha2::{Digest, Sha256};
    let policy = match std::env::args().nth(1).as_deref() {
        Some("allow") => ALLOW,
        Some("deny") => DENY,
        _ => panic!("host must select a fixture policy"),
    };
    let mut bytes = Vec::new();
    std::io::stdin().take(65537).read_to_end(&mut bytes).unwrap();
    assert!(bytes.len() <= 65536, "oversized fixture request");
    let request: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(request["tool"], TOOL);
    let runtime = runtime(policy);
    let actor = start(&runtime, request["context"]["root_id"].as_str().unwrap()).await;
    let call_id = request["context"]["call_id"].as_str().unwrap();
    let decision = hooks::handle(&runtime, HookEvent::ToolCall {
        actor, call_id: Some(call_id.into()),
        call: ProposedCall {
            tool: TOOL.into(), cwd: None,
            arguments: serde_json::value::RawValue::from_string(
                request["arguments"].to_string()).unwrap(),
        },
        spawn: None, prompt: None, ruling: None,
    }).await;
    let outcome = match decision {
        HookDecision::AllowCall { spawn: None } => "allow",
        HookDecision::DenyCall { .. } => "deny",
        _ => "refuse",
    };
    let response = serde_json::json!({
        "request_sha256": format!("{:x}", Sha256::digest(&bytes)),
        "outcome": outcome,
    });
    writeln!(std::io::stdout(), "{response}").unwrap();
}
