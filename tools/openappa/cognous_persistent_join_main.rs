// Appended to the trajectory fixture; synthetic refund maps to its public sink.
#[tokio::main]
async fn main() {
    use std::io::{Read, Write};
    use sha2::{Digest, Sha256};
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 3, "mode and absolute store path required");
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
    runtime.try_status(&actor().root).expect("existing root required");
    if args[1] == "restrict" {
        narrow(&runtime).await;
        public_denied(&runtime, "restriction-control").await;
        return;
    }
    if args[1] == "audit" {
        println!("{}", serde_json::to_string(&runtime.audit(&actor().root).unwrap()).unwrap());
        return;
    }
    let mut bytes = Vec::new();
    std::io::stdin().take(65537).read_to_end(&mut bytes).unwrap();
    assert!(bytes.len() <= 65536);
    let request: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    if args[1] == "status" {
        let root = TrajectoryId(request["root_id"].as_str().unwrap().into());
        runtime.try_status(&root).expect("enrolled root must already exist");
        println!("existing-root");
        return;
    }
    assert!(args[1] == "check" || args[1] == "feedback");
    assert_eq!(request["tool"], "host/cognous_fixture/refund");
    assert_eq!(request["context"]["root_id"], actor().root.0);
    // Test-owned mapping only: no claim that arbitrary refunds are public egress.
    let mut proposed = call("send_public");
    proposed.arguments = serde_json::value::RawValue::from_string(
        request["arguments"].to_string()).unwrap();
    if args[1] == "feedback" {
        let receipt = hooks::handle(&runtime, HookEvent::ToolResult {
            actor: actor(), call: proposed,
            call_id: Some(request["context"]["call_id"].as_str().unwrap().into()),
            outcome: ToolOutcome::Success {
                body: OutcomeBody::Available("synthetic-applied-result".into()),
            },
        }).await;
        let response = serde_json::json!({
            "request_sha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
            "outcome": if receipt == HookDecision::Ack { "ack" } else { "refuse" },
        });
        writeln!(std::io::stdout(), "{response}").unwrap();
        return;
    }
    let decision = hooks::handle(&runtime, HookEvent::ToolCall {
        actor: actor(), call: proposed,
        call_id: Some(request["context"]["call_id"].as_str().unwrap().into()),
        spawn: None, prompt: None, ruling: None,
    }).await;
    let outcome = match decision {
        HookDecision::AllowCall { spawn: None } => "allow",
        HookDecision::DenyCall { .. } => "deny",
        _ => "refuse",
    };
    let response = serde_json::json!({
        "request_sha256": Sha256::digest(&bytes).iter().map(|byte| format!("{byte:02x}")).collect::<String>(),
        "outcome": outcome,
    });
    writeln!(std::io::stdout(), "{response}").unwrap();
}
