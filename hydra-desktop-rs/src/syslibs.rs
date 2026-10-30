//! D19: on Linux the window libraries are opened at run time, not linked. A missing one used to end the app in a
//! panic deep inside winit ("Library libxkbcommon-x11.so could not be loaded") or an OsError dump. Check them before
//! the window opens and say which package to install.

#[derive(Debug, PartialEq, Eq)]
pub struct Lib {
    pub soname: &'static str,
    pub what: &'static str,
    pub debian: &'static str,
    pub fedora: &'static str,
}

const fn lib(soname: &'static str, what: &'static str, debian: &'static str, fedora: &'static str) -> Lib {
    Lib { soname, what, debian, fedora }
}

const XKB: Lib = lib("libxkbcommon.so.0", "keyboard input", "libxkbcommon0", "libxkbcommon");
/// What winit 0.30 opens for an X11 window (x11-dl in xdisplay.rs, xkbcommon-dl).
pub const X11: &[Lib] = &[
    lib("libX11.so.6", "X11 windows", "libx11-6", "libX11"),
    lib("libX11-xcb.so.1", "X11 windows", "libx11-xcb1", "libX11-xcb"),
    lib("libXcursor.so.1", "mouse cursors", "libxcursor1", "libXcursor"),
    lib("libXi.so.6", "mouse and touch input", "libxi6", "libXi"),
    XKB,
    lib("libxkbcommon-x11.so.0", "keyboard input on X11", "libxkbcommon-x11-0", "libxkbcommon-x11"),
];
pub const WAYLAND: &[Lib] = &[
    lib("libwayland-client.so.0", "Wayland windows", "libwayland-client0", "libwayland-client"),
    lib("libwayland-egl.so.1", "Wayland drawing", "libwayland-egl1", "libwayland-egl"),
    XKB,
    lib("libEGL.so.1", "OpenGL drawing", "libegl1", "mesa-libEGL"),
];
/// X11 draws through EGL or GLX; either one is enough.
pub const GL: &[Lib] = &[lib("libEGL.so.1", "OpenGL drawing", "libegl1", "mesa-libEGL"), lib("libGL.so.1", "OpenGL drawing", "libgl1", "mesa-libGL")];

fn set(v: Option<String>) -> bool {
    v.is_some_and(|s| !s.is_empty())
}

/// Libraries the window needs that `have` cannot open. Wayland is tried first when it is offered, like winit does;
/// a complete Wayland set means nothing is missing even if X11 libraries are absent. No display at all is left to winit.
pub fn missing(env: impl Fn(&str) -> Option<String>, have: impl Fn(&str) -> bool) -> Vec<&'static Lib> {
    let gaps = |libs: &'static [Lib]| libs.iter().filter(|l| !have(l.soname)).collect::<Vec<_>>();
    let wayland = set(env("WAYLAND_DISPLAY"));
    if wayland {
        let w = gaps(WAYLAND);
        if w.is_empty() || !set(env("DISPLAY")) {
            return w;
        }
    }
    if !set(env("DISPLAY")) {
        return Vec::new();
    }
    let mut x = gaps(X11);
    if GL.iter().all(|l| !have(l.soname)) {
        x.push(&GL[1]);
    }
    x
}

/// The message for a terminal and the log folder.
pub fn report(missing: &[&Lib]) -> String {
    let mut s = String::from("Hydra can't open its window because these system libraries are missing:\n");
    for l in missing {
        s += &format!("  {} ({})\n", l.soname, l.what);
    }
    let pkgs = |f: fn(&Lib) -> &'static str| {
        let mut v: Vec<&str> = Vec::new();
        for l in missing {
            if !v.contains(&f(l)) {
                v.push(f(l));
            }
        }
        v.join(" ")
    };
    s += &format!("Install them, then start Hydra again:\n  Debian or Ubuntu: sudo apt install {}\n  Fedora: sudo dnf install {}\n", pkgs(|l| l.debian), pkgs(|l| l.fedora));
    s
}

/// Can the dynamic loader open this library the way winit will?
pub fn can_open(soname: &str) -> bool {
    // SAFETY: these are the system window libraries winit opens a moment later anyway; their init code is the same.
    unsafe { libloading::Library::new(soname) }.is_ok()
}

/// Run before the window opens. `Some(message)` means the window cannot open; the message is also left in
/// logs/startup-error.txt, which a clean start removes.
pub fn preflight() -> Option<String> {
    let file = crate::crash::log_dir().join("startup-error.txt");
    let gaps = missing(|k| std::env::var(k).ok(), can_open);
    if gaps.is_empty() {
        let _ = std::fs::remove_file(&file);
        return None;
    }
    let msg = report(&gaps);
    let _ = std::fs::create_dir_all(crate::crash::log_dir());
    let _ = std::fs::write(&file, &msg);
    Some(msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn env<'a>(vars: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<String> + 'a {
        move |k| vars.iter().find(|(n, _)| *n == k).map(|(_, v)| v.to_string())
    }
    fn names(v: &[&Lib]) -> Vec<&'static str> {
        v.iter().map(|l| l.soname).collect()
    }

    #[test]
    fn x11_names_the_missing_xkb_x11_library() {
        let m = missing(env(&[("DISPLAY", ":0")]), |s| s != "libxkbcommon-x11.so.0");
        assert_eq!(names(&m), ["libxkbcommon-x11.so.0"]);
        let r = report(&m);
        assert!(r.contains("libxkbcommon-x11.so.0 (keyboard input on X11)"), "{r}");
        assert!(r.contains("sudo apt install libxkbcommon-x11-0\n"), "{r}");
        assert!(r.contains("sudo dnf install libxkbcommon-x11\n"), "{r}");
    }

    #[test]
    fn everything_present_is_nothing_missing() {
        assert!(missing(env(&[("DISPLAY", ":0")]), |_| true).is_empty());
        assert!(missing(env(&[("WAYLAND_DISPLAY", "wayland-0")]), |_| true).is_empty());
    }

    #[test]
    fn gl_needs_only_one_of_egl_or_glx() {
        assert!(missing(env(&[("DISPLAY", ":0")]), |s| s != "libEGL.so.1").is_empty());
        assert!(missing(env(&[("DISPLAY", ":0")]), |s| s != "libGL.so.1").is_empty());
        let m = missing(env(&[("DISPLAY", ":0")]), |s| !s.starts_with("libEGL") && !s.starts_with("libGL"));
        assert_eq!(names(&m), ["libGL.so.1"]);
    }

    #[test]
    fn complete_wayland_does_not_need_x11() {
        let v = [("WAYLAND_DISPLAY", "wayland-0"), ("DISPLAY", ":0")];
        assert!(missing(env(&v), |s| WAYLAND.iter().any(|l| l.soname == s)).is_empty());
    }

    #[test]
    fn broken_wayland_falls_back_to_x11_and_reports_x11_gaps() {
        let v = [("WAYLAND_DISPLAY", "wayland-0"), ("DISPLAY", ":0")];
        let m = missing(env(&v), |s| s != "libwayland-client.so.0" && s != "libXi.so.6");
        assert_eq!(names(&m), ["libXi.so.6"]);
        let only_wayland = missing(env(&[("WAYLAND_DISPLAY", "wayland-0")]), |s| s != "libwayland-client.so.0");
        assert_eq!(names(&only_wayland), ["libwayland-client.so.0"]);
    }

    #[test]
    fn no_display_is_left_to_winit() {
        assert!(missing(env(&[]), |_| false).is_empty());
        assert!(missing(env(&[("DISPLAY", "")]), |_| false).is_empty());
    }

    #[test]
    fn shared_packages_are_listed_once() {
        let m = missing(env(&[("DISPLAY", ":0")]), |s| !s.starts_with("libX11"));
        let r = report(&m);
        assert!(r.contains("sudo apt install libx11-6 libx11-xcb1\n"), "{r}");
    }

    #[test]
    fn the_real_loader_opens_libc_and_not_a_made_up_name() {
        assert!(can_open("libc.so.6"));
        assert!(!can_open("libhydra-no-such-lib.so.9"));
    }
}
