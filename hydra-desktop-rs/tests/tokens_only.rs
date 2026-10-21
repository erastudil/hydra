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

/// Type sizes in the design file that no widget reads yet.
// wuwei: input and button sizes are not wired; widget labels still pass literal sizes until a type-size sweep moves them
const TYPE_NOT_WIRED: &[&str] = &["input", "button"];

#[test]
fn every_type_size_in_the_design_file_is_used_by_the_app() {
    // 5S: a size left behind by a removed widget (TYPE_NAV_ITEM after D03 dropped the sidebar mode list) is pruned,
    // not kept as a dead constant
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let tokens: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(root.join("../assets/design/hydra-desk.tokens.json")).unwrap()).unwrap();
    let mut src = String::new();
    for entry in std::fs::read_dir(root.join("src")).unwrap() {
        let path = entry.unwrap().path();
        if path.extension().is_some_and(|e| e == "rs") {
            src += &std::fs::read_to_string(&path).unwrap();
        }
    }
    let unused: Vec<String> = tokens["typography"]["scale"].as_object().unwrap().keys()
        .filter(|k| !TYPE_NOT_WIRED.contains(&k.as_str()))
        .map(|k| format!("TYPE_{}", k.to_uppercase()))
        .filter(|c| !src.contains(c.as_str()))
        .collect();
    assert!(unused.is_empty(), "use these in src/ or prune them from typography.scale: {unused:?}");
    for k in TYPE_NOT_WIRED {
        assert!(tokens["typography"]["scale"].get(*k).is_some(), "{k} is gone from the design file; drop it from TYPE_NOT_WIRED");
    }
}
