//! Parallel threads: several turns run at once and every event lands in its own thread. Uses tests/fake_agent, a
//! conformant stdio agent that runs turns in parallel (the real adapter joins in when it supports it).

use hydra_desktop::backend::Backend;
use hydra_desktop::model::*;
use std::time::{Duration, Instant};

fn setup(name: &str, projects: usize) -> (Model, Backend, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!("hydra-conc-{name}-{}", std::process::id()));
    let mut first = dir.join("p0");
    std::fs::create_dir_all(&first).unwrap();
    let mut m = Model::new(None, first.display().to_string());
    for i in 1..projects {
        first = dir.join(format!("p{i}"));
        std::fs::create_dir_all(&first).unwrap();
        m.open_project(first.to_str().unwrap()).unwrap();
    }
    std::env::set_var("HYDRA_REPO", format!("{}/tests/fake_agent", env!("CARGO_MANIFEST_DIR")));
    std::env::set_var("HYDRA_PYTHON", std::env::var("HYDRA_PYTHON").unwrap_or_else(|_| "python3".into()));
    let be = Backend::spawn(|| {}).expect("fake agent starts");
    (m, be, dir)
}

fn pump(m: &mut Model, be: &Backend, secs: u64, mut until: impl FnMut(&Model) -> bool) -> bool {
    let end = Instant::now() + Duration::from_secs(secs);
    loop {
        while let Ok(e) = be.rx.try_recv() {
            m.apply(e);
        }
        if until(m) {
            return true;
        }
        if Instant::now() > end {
            return false;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn two_projects_run_in_parallel_with_their_own_cwd_and_approval() {
    let (mut m, mut be, dir) = setup("two", 2);
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    let b = m.active;
    assert!(m.submit(&mut be, "ASK please"));
    let a = m.thread_indices(m.projects[0].id)[0];
    m.select(a);
    assert!(m.submit(&mut be, "SLOW work"));
    assert!(pump(&mut m, &be, 10, |m| !m.approvals.is_empty() && m.runs.len() == 2));
    assert_eq!(m.status(b), Status::NeedsApproval);
    assert_eq!(m.status(a), Status::Running);
    assert!(m.approval_for_active().is_none(), "the approval belongs to the other thread, not the one in front");
    m.select(b);
    assert!(m.approval_for_active().is_some());
    m.answer_approval(&mut be, "allow");
    assert!(pump(&mut m, &be, 10, |m| !m.is_running(m.sessions[b].id)));
    assert!(m.is_running(m.sessions[a].id), "other thread still running");
    m.select(a);
    m.stop(&mut be);
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    let (ta, tb) = (m.sessions[a].messages.last().unwrap().clone(), m.sessions[b].messages.last().unwrap().clone());
    let (pa, pb) = (m.projects[0].path.clone(), m.projects[1].path.clone());
    assert!(ta.text.contains(&pa) && !ta.text.contains(&pb), "{}", ta.text);
    assert!(tb.text.contains(&pb) && !tb.text.contains(&pa), "{}", tb.text);
    assert!(ta.actions.iter().any(|x| x.contains("cancelled")));
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn stress_200_rounds_of_four_overlapping_turns_never_cross_talk() {
    let (mut m, mut be, dir) = setup("stress", 1);
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    for _ in 0..3 {
        m.new_session();
    }
    let n = m.sessions.len();
    let mut seed = 0x9e3779b97f4a7c15u64;
    let mut next = move || {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        seed
    };
    for round in 0..200 {
        let mut asking = vec![];
        for i in 0..n {
            m.select(i);
            let ask = next() % 3 == 0;
            let word = if ask { "ASK" } else if next() % 2 == 0 { "WRITE" } else { "go" };
            if ask {
                asking.push(i);
            }
            assert!(m.submit(&mut be, &format!("{word} round {round}")), "round {round} thread {i}");
        }
        let mut order = asking.clone();
        order.sort_by_key(|_| next());
        for i in order {
            assert!(pump(&mut m, &be, 10, |m| m.approvals.iter().any(|a| a.sid == m.sessions[i].id)), "round {round}: approval for thread {i}");
            m.select(i);
            m.answer_approval(&mut be, if next() % 2 == 0 { "allow" } else { "skip" });
        }
        assert!(pump(&mut m, &be, 15, |m| !m.any_busy()), "round {round} never finished");
        for i in 0..n {
            let tag = format!("{}.", m.sessions[i].thread);
            let last = m.sessions[i].messages.last().unwrap();
            for j in 0..n {
                if j != i {
                    assert!(!last.text.contains(&format!("{}.", m.sessions[j].thread)), "cross talk: thread {i} got thread {j}'s tokens: {}", last.text);
                }
            }
            assert!(last.text.contains(&tag), "thread {i} lost its own tokens: {}", last.text);
        }
    }
    assert!(m.approvals.is_empty());
    let _ = std::fs::remove_dir_all(dir);
}

fn git_repo(dir: &std::path::Path) {
    std::fs::create_dir_all(dir).unwrap();
    for a in [&["init", "-q", "-b", "w"][..], &["config", "user.email", "t@t"], &["config", "user.name", "t"], &["config", "commit.gpgsign", "false"]] {
        assert!(std::process::Command::new("git").args(a).current_dir(dir).output().unwrap().status.success());
    }
    std::fs::write(dir.join("a.txt"), "original\n").unwrap();
    for a in [&["add", "."][..], &["commit", "-q", "-m", "init"]] {
        assert!(std::process::Command::new("git").args(a).current_dir(dir).output().unwrap().status.success());
    }
}

#[test]
fn rewind_restores_files_changed_by_a_turn_and_can_be_undone() {
    let (mut m, mut be, dir) = setup("rewind", 1);
    let repo = dir.join("p0");
    git_repo(&repo);
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    assert!(m.submit(&mut be, "TOUCH the files"));
    assert!(m.sessions[m.active].checkpoints.len() == 1, "a checkpoint is taken before the turn");
    assert!(m.rewind_code(None, 0).unwrap_err().contains("stop the running turn"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert_eq!(std::fs::read_to_string(repo.join("a.txt")).unwrap(), "agent was here\n");
    assert!(repo.join("touched.txt").exists());
    let at = m.sessions[m.active].checkpoints[0].at;
    assert_eq!(m.checkpoint_for(at), Some(0));
    assert!(m.sessions[m.active].messages[at].user, "the checkpoint sits before the user message");
    m.rewind_code(None, 0).unwrap();
    assert_eq!(std::fs::read_to_string(repo.join("a.txt")).unwrap(), "original\n");
    assert!(!repo.join("touched.txt").exists());
    m.undo_rewind(None).unwrap();
    assert_eq!(std::fs::read_to_string(repo.join("a.txt")).unwrap(), "agent was here\n");
    assert!(repo.join("touched.txt").exists());
    assert!(m.undo_rewind(None).is_err(), "only one level of undo");
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn at_mentions_attach_file_text_to_the_prompt_but_not_to_the_bubble() {
    let (mut m, mut be, dir) = setup("mention", 1);
    std::fs::write(dir.join("p0/notes.txt"), "remember the milk").unwrap();
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    assert!(m.submit(&mut be, "ECHO see @notes.txt please"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    let msgs = &m.sessions[m.active].messages;
    let user = &msgs[msgs.len() - 2];
    assert_eq!(user.text, "ECHO see @notes.txt please");
    assert!(user.actions.iter().any(|a| a == "attached notes.txt"));
    assert!(msgs.last().unwrap().text.contains("remember the milk"), "the agent got the file text: {}", msgs.last().unwrap().text);
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn a_thread_with_a_worktree_runs_its_turn_there_and_other_threads_stay_put() {
    let (mut m, mut be, dir) = setup("wt", 1);
    let repo = dir.join("p0");
    git_repo(&repo);
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    let first = m.active;
    m.new_session();
    let second = m.active;
    let wt = m.start_worktree().unwrap();
    assert!(std::path::Path::new(&wt).join("a.txt").is_file());
    assert!(m.start_worktree().is_err(), "one worktree per thread");
    assert!(m.submit(&mut be, "TOUCH in the worktree"));
    m.select(first);
    assert!(m.submit(&mut be, "go here"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert_eq!(std::fs::read_to_string(std::path::Path::new(&wt).join("a.txt")).unwrap(), "agent was here\n", "edit landed in the worktree");
    assert_eq!(std::fs::read_to_string(repo.join("a.txt")).unwrap(), "original\n", "main checkout untouched");
    let t2 = m.sessions[second].messages.last().unwrap().text.clone();
    let t1 = m.sessions[first].messages.last().unwrap().text.clone();
    assert!(t2.contains(&wt), "{t2}");
    assert!(!t1.contains(".hydra"), "{t1}");
    m.select(second);
    m.remove_worktree().unwrap();
    assert!(!std::path::Path::new(&wt).exists());
    assert!(m.sessions[second].worktree.is_none());
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn turns_send_cwd_model_permission_mode_only_when_they_change() {
    let (mut m, mut be, dir) = setup("fields", 1);
    assert!(pump(&mut m, &be, 10, |m| m.agent_ready));
    let last_text = |m: &Model| m.sessions[m.active].messages.last().unwrap().text.clone();
    assert!(m.submit(&mut be, "FIELDS one"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert!(last_text(&m).contains(r#"["cwd", "mode", "model", "permission"]"#), "a new thread gets everything: {}", last_text(&m));
    assert!(m.submit(&mut be, "FIELDS two"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert!(last_text(&m).starts_with("[]["), "nothing changed, nothing resent: {}", last_text(&m));
    m.cycle_permission();
    assert!(m.submit(&mut be, "FIELDS three"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert!(last_text(&m).starts_with(r#"["permission"]"#), "{}", last_text(&m));
    // a restarted agent process gets the full set again
    m.apply(hydra_desktop::backend::Ev::Hello { caps: vec![] });
    assert!(m.submit(&mut be, "FIELDS four"));
    assert!(pump(&mut m, &be, 10, |m| !m.any_busy()));
    assert!(last_text(&m).contains(r#""cwd""#), "{}", last_text(&m));
    let _ = std::fs::remove_dir_all(dir);
}
