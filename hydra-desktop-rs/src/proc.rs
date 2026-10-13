//! Every subprocess the app starts goes through here, so none can open a console window on Windows.

use std::process::Command;

/// CREATE_NO_WINDOW. A console program started by a GUI process otherwise gets a visible console.
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// A command that never shows a window. Use this instead of `Command::new`.
pub fn command(program: impl AsRef<std::ffi::OsStr>) -> Command {
    let mut cmd = Command::new(program);
    hide(&mut cmd);
    cmd
}

/// Apply the no-window flag to a command built elsewhere.
pub fn hide(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = cmd;
    }
}

/// The flags a hidden command carries on Windows (0 elsewhere), for tests.
pub fn flags_for_tests() -> u32 {
    if cfg!(windows) {
        CREATE_NO_WINDOW
    } else {
        0
    }
}
