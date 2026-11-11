//! Runs the `pdf-textlayer` binary on the sample scan in `examples/`.
#![cfg(feature = "cli")]

use std::path::{Path, PathBuf};
use std::process::Command;

fn example(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples").join(name)
}

fn pdf_textlayer(args: &[&Path]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_pdf-textlayer"))
        .args(args)
        .output()
        .expect("run pdf-textlayer")
}

#[test]
fn writes_a_text_layer_from_liteparse_json() {
    let output_pdf = std::env::temp_dir().join(format!("pdf-textlayer-cli-{}.pdf", std::process::id()));

    let output = pdf_textlayer(&[&example("scan.pdf"), &example("parsed.json"), &output_pdf]);

    let stdout = String::from_utf8(output.stdout).unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(stdout.contains("Pages: 1\n"), "{stdout}");
    assert!(stdout.contains("Text layer added on 1 page(s): 1\n"), "{stdout}");
    assert!(stdout.contains("Items drawn: 15\n"), "{stdout}");

    let written = std::fs::read(&output_pdf).unwrap();
    std::fs::remove_file(&output_pdf).unwrap();
    assert!(written.starts_with(b"%PDF-"));
    assert!(written.len() > std::fs::metadata(example("scan.pdf")).unwrap().len() as usize);
}

#[test]
fn a_missing_input_fails_with_the_path_on_stderr() {
    let missing = example("missing.pdf");
    let output_pdf = std::env::temp_dir().join("pdf-textlayer-cli-never-written.pdf");

    let output = pdf_textlayer(&[&missing, &example("parsed.json"), &output_pdf]);

    assert_eq!(output.status.code(), Some(1));
    let stderr = String::from_utf8(output.stderr).unwrap();
    assert!(
        stderr.starts_with("error: ") && stderr.contains("missing.pdf"),
        "{stderr}"
    );
    assert!(!output_pdf.exists());
}
