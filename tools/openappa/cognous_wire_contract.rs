//! Codec-only harness for a synthetic existing host, not a Cognous adapter.
use appa_runtime_api::{Adapter, AdapterName, CanonicalTool, HookDecision, HookEvent,
    IdentifiedTool, ParseRefusal, WireDecision, WireEvent};
use serde_json::Value;

fn fixture() -> Value {
    serde_json::from_str(&std::fs::read_to_string(
        std::env::var("COGNOUS_OPENAPPA_FIXTURE").unwrap()).unwrap()).unwrap()
}

fn identify(raw: &str) -> Result<IdentifiedTool, ParseRefusal> {
    if raw != "refund" { return Err(ParseRefusal::Malformed { detail: "unknown fixture tool".into() }); }
    Ok(IdentifiedTool { canonical: CanonicalTool::parse("host/cognous_fixture/refund").unwrap(), spawn: None })
}

const HOST: Adapter = Adapter {
    name: AdapterName::Kagent, identify_tool: identify, names_children: |_, _| vec![],
    spell: |_| Some("refund".into()), wildcard_covers_spawn: false, spells_server: |_| false,
};

#[test]
fn actual_upstream_codec_preserves_call_identity() {
    let f = fixture();
    let accepted = WireEvent::read(f["request_json"].as_str().unwrap().as_bytes())
        .unwrap().into_event(&HOST).unwrap().unwrap();
    match accepted.event {
        HookEvent::ToolCall { actor, call, call_id, spawn, .. } => {
            assert_eq!(actor.root.0, "kagent:synthetic-root");
            assert_eq!(call_id.as_deref(), Some("synthetic-call"));
            assert_eq!(call.tool, "host/cognous_fixture/refund");
            let args: Value = serde_json::from_str(call.arguments.get()).unwrap();
            assert_eq!(args["amount"], 50);
            assert_eq!(args["target"], "refund.sqlite");
            assert!(spawn.is_none());
        },
        other => panic!("unexpected event: {other:?}"),
    }
}

#[test]
fn actual_decisions_remain_distinct() {
    let f = fixture();
    let decisions: Vec<HookDecision> = f["decisions"].as_array().unwrap().iter()
        .map(|v| serde_json::from_value::<WireDecision>(v.clone()).unwrap().into_decision().unwrap()).collect();
    assert!(matches!(decisions[0], HookDecision::AllowCall { .. }));
    assert!(matches!(decisions[1], HookDecision::DenyCall { .. }));
    assert!(matches!(decisions[2], HookDecision::PassControl));
    assert!(matches!(decisions[3], HookDecision::Block { .. }));
}

#[test]
fn actual_codec_rejects_wrong_protocol_and_missing_correlation() {
    let f = fixture();
    for field in ["protocol", "root_id"] {
        let mut event: Value = serde_json::from_str(f["request_json"].as_str().unwrap()).unwrap();
        if field == "protocol" { event[field] = 2.into(); }
        else { event.as_object_mut().unwrap().remove(field); }
        let wire = serde_json::to_vec(&event).unwrap();
        assert!(WireEvent::read(&wire).unwrap().into_event(&HOST).is_err());
    }
    let bad: WireDecision = serde_json::from_str(r#"{"protocol":1,"decision":"deny_call"}"#).unwrap();
    assert!(bad.into_decision().is_err());
}
