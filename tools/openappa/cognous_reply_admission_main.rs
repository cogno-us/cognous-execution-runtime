// Appended to the persistent trajectory fixture. Test-only final public-text sink.
#[tokio::main]
async fn main() {
    use std::io::{Read, Write};
    use sha2::{Digest, Sha256};
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 3);
    let path = std::path::PathBuf::from(&args[2]);
    assert!(path.is_absolute());
    if args[1] == "bootstrap" {
        assert!(!path.exists());
        let runtime = open(&path);
        start(&runtime).await;
        public_allowed(&runtime, "bootstrap-control").await;
        return;
    }
    let mut uri = url::Url::from_file_path(&path).unwrap();
    uri.set_query(Some("mode=rw"));
    let runtime = open(Path::new(uri.as_str()));
    runtime.try_status(&actor().root).expect("existing fixture root required");
    if args[1] == "restrict" {
        narrow(&runtime).await;
        public_denied(&runtime, "restriction-control").await;
        return;
    }
    if args[1] == "audit" {
        println!("{}", serde_json::to_string(&runtime.audit(&actor().root).unwrap()).unwrap());
        return;
    }
    assert!(args[1] == "check" || args[1] == "feedback");
    let mut bytes = Vec::new();
    std::io::stdin().take(131073).read_to_end(&mut bytes).unwrap();
    // JSON escaping can expand a bounded text payload beyond its UTF-8 size.
    assert!(bytes.len() <= 131072);
    let request: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(request["kind"], "final");
    assert_eq!(request["destinationId"], "synthetic-public");
    assert!(request["text"].as_str().unwrap().len() <= 16384);
    let call_id = request["outputId"].as_str().unwrap();
    assert!(!call_id.is_empty());
    let mut proposed = call("send_public");
    proposed.arguments = serde_json::value::RawValue::from_string(
        request.to_string()).unwrap();
    if args[1] == "feedback" {
        // Explicit test-owned success observation, never inferred from allowance.
        let receipt = hooks::handle(&runtime, HookEvent::ToolResult {
            actor: actor(), call: proposed, call_id: Some(call_id.into()),
            outcome: ToolOutcome::Success {
                body: OutcomeBody::Available("synthetic-text-delivered".into()),
            },
        }).await;
        let response = serde_json::json!({
            "requestSha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
            "outcome": if receipt == HookDecision::Ack { "ack" } else { "refuse" },
        });
        writeln!(std::io::stdout(), "{response}").unwrap();
        return;
    }
    let decision = hooks::handle(&runtime, HookEvent::ToolCall {
        actor: actor(), call: proposed, call_id: Some(call_id.into()),
        spawn: None, prompt: None, ruling: None,
    }).await;
    let response = serde_json::json!({
        "requestSha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
        "outcome": match decision {
            HookDecision::AllowCall { spawn: None } => "allow",
            _ => "refuse",
        },
    });
    writeln!(std::io::stdout(), "{response}").unwrap();
}
