//! Visual values live only in assets/design/hydra-desk.tokens.json: a color written as numbers or as an egui named
//! color in src/ (outside test modules) fails here, so the UI cannot drift from the design file again.

/// egui's named colors that are not design choices: no tint on an image, and nothing at all.
const ALLOWED_NAMED: &[&str] = &["WHITE", "TRANSPARENT"];

/// (line number, line) of every hard-coded color in one source file; scanning stops at its test module.
fn hard_coded(src: &str) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for (n, line) in src.lines().enumerate() {
        if line.trim_start().starts_with("#[cfg(test)]") {
            break;
        }
        let mut rest = line;
        while let Some(i) = rest.find("Color32::") {
            rest = &rest[i + "Color32::".len()..];
            let name: String = rest.chars().take_while(|c| c.is_ascii_alphanumeric() || *c == '_').collect();
            let after = rest[name.len()..].trim_start();
            let literal_args = name.starts_with("from_") && after.starts_with('(') && after[1..].trim_start().starts_with(|c: char| c.is_ascii_digit() || c == '"');
            let named = !name.is_empty() && name.chars().all(|c| c.is_ascii_uppercase() || c == '_') && !ALLOWED_NAMED.contains(&name.as_str());
            if literal_args || named {
                out.push((n + 1, line.trim().to_string()));
            }
        }
    }
    out
}

#[test]
fn the_scanner_catches_each_kind_of_hard_coded_color() {
    assert_eq!(hard_coded("let c = Color32::from_rgb(255, 196, 64);").len(), 1);
    assert_eq!(hard_coded("let c = egui::Color32::from_rgba_unmultiplied( 0, 255, 102, a);").len(), 1);
    assert_eq!(hard_coded("let c = Color32::from_hex(\"#ffffff\");").len(), 1);
    assert_eq!(hard_coded("Kind::Primary => Color32::BLACK,").len(), 1);
    assert!(hard_coded("with_alpha(ACCENT, a); Color32::from_rgba_unmultiplied(c.r(), c.g(), c.b(), a); Color32::WHITE; Color32::TRANSPARENT").is_empty());
    assert!(hard_coded("fn f() {}\n#[cfg(test)]\nmod t { const C: Color32 = Color32::from_rgb(1, 2, 3); }").is_empty());
}

#[test]
fn every_color_in_the_app_comes_from_the_design_tokens() {
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut found = Vec::new();
    for entry in std::fs::read_dir(&dir).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|e| e == "rs") {
            for (n, line) in hard_coded(&std::fs::read_to_string(&path).unwrap()) {
                found.push(format!("{}:{n}: {line}", path.file_name().unwrap().to_string_lossy()));
            }
        }
    }
    assert!(found.is_empty(), "add these to assets/design/hydra-desk.tokens.json and use the generated constant:\n{}", found.join("\n"));
}
