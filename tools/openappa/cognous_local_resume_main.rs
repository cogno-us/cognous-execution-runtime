// Appended to the existing trajectory harness for a synthetic end-to-end probe.
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 4, "mode, absolute path and root required");
    let path = std::path::PathBuf::from(&args[2]);
    assert!(path.is_absolute());
    assert_eq!(args[3], actor().root.0, "this probe supports only the fixture root");
    if args[1] == "bootstrap" {
        assert!(!path.exists(), "bootstrap never replaces an existing store");
        let runtime = open(&path);
        start(&runtime).await;
        public_allowed(&runtime, "before-read").await;
        narrow(&runtime).await;
        public_denied(&runtime, "after-read").await;
    } else {
        assert_eq!(args[1], "resume");
        let mut uri = url::Url::from_file_path(&path).unwrap();
        uri.set_query(Some("mode=rw"));
        // SQLite URI mode=rw forbids creation by the actual upstream opener.
        let runtime = open(std::path::Path::new(uri.as_str()));
        runtime.try_status(&TrajectoryId(args[3].clone())).expect("existing root required");
        public_denied(&runtime, "resumed-public-send").await;
        println!("existing-restricted-root");
    }
}
