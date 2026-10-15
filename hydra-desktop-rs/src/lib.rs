//! Hydra Desktop core: the agent process client, the UI-independent model, and persistence.
pub mod backend;
pub mod model;
pub mod proc;
pub mod crash;
#[cfg(target_os = "linux")]
pub mod syslibs;
pub mod health;
pub mod fsx;
pub mod git;
pub mod workspace;
pub mod app;
pub mod theme;
pub mod widgets;
pub mod secrets;
pub mod layout;
pub mod motion;
