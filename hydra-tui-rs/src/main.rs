mod app;
mod banner;
mod endpoints;
mod layout;

use std::io::{self, stdout};
use std::time::Duration;

use crossterm::event::{
    self, DisableBracketedPaste, DisableMouseCapture, EnableBracketedPaste, EnableMouseCapture,
    Event, KeyCode, KeyEventKind, KeyModifiers, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Clear, Paragraph, Wrap};
use ratatui::Terminal;

use app::{read_clipboard_text, App};
use banner::{
    to_color, ACCENT_RGB, AMBER_RGB, BG_DARK, BRAILLE_SPIN, DIM_RGB, HOT_RGB,
    MARK_FROM_RGB, MARK_TO_RGB, SCALE_STOPS,
};
use layout::fit_status;

/// Ticker cadence matching Python `hydra_cli/tui.py::_ticker` (0.08s = 80ms).
const TICK_INTERVAL: Duration = Duration::from_millis(80);

fn main() -> io::Result<()> {
    let mut args = std::env::args().skip(1);
    if args.any(|a| a == "--probe") {
        return probe();
    }
    let mut app = App::new();
    enable_raw_mode()?;
    let mut out = stdout();
    execute!(out, EnterAlternateScreen, EnableMouseCapture, EnableBracketedPaste)?;
    let backend = ratatui::backend::CrosstermBackend::new(out);
    let mut terminal = Terminal::new(backend)?;
    let result = run(&mut terminal, &mut app);
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        DisableBracketedPaste,
        DisableMouseCapture,
        LeaveAlternateScreen
    )?;
    terminal.show_cursor()?;
    result
}

/// Press and Repeat act; Release never does. Release-reporting terminals
/// deliver both for one keystroke, and acting on both doubled every character.
fn handles_key(kind: KeyEventKind) -> bool {
    kind != KeyEventKind::Release
}

fn run(terminal: &mut Terminal<ratatui::backend::CrosstermBackend<io::Stdout>>, app: &mut App) -> io::Result<()> {
    loop {
        terminal.draw(|frame| draw(frame, app))?;
        if event::poll(TICK_INTERVAL)? {
            match event::read()? {
                Event::Paste(text) => {
                    app.paste_text(&text);
                }
                Event::Key(key) if handles_key(key.kind) => {
                    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
                    let alt = key.modifiers.contains(KeyModifiers::ALT);
                    let shift = key.modifiers.contains(KeyModifiers::SHIFT);

                    if app.approval.is_some() && app.input.is_empty() && !ctrl && !alt {
                        match key.code {
                            KeyCode::Char('y') | KeyCode::Char('Y') | KeyCode::Enter => {
                                app.decide("allow");
                                continue;
                            }
                            KeyCode::Char('a') | KeyCode::Char('A') => {
                                app.decide_always("thread", None);
                                continue;
                            }
                            KeyCode::Char('p') | KeyCode::Char('P') => {
                                app.decide_always("project", None);
                                continue;
                            }
                            KeyCode::Char('n') | KeyCode::Char('N') | KeyCode::Char('s') | KeyCode::Char('S') => {
                                app.decide("skip");
                                continue;
                            }
                            KeyCode::Esc => {
                                app.cancel();
                                continue;
                            }
                            _ => {}
                        }
                    }

                    match key.code {
                        KeyCode::Char('c') if ctrl => break,
                        KeyCode::Char('d') if ctrl => {
                            if app.input.is_empty() {
                                break;
                            } else {
                                app.delete();
                            }
                        }
                        KeyCode::Char('l') if ctrl => {
                            app.transcript.clear();
                        }
                        KeyCode::Char('v') if ctrl => {
                            if let Some(txt) = read_clipboard_text() {
                                app.paste_text(&txt);
                            }
                        }
                        KeyCode::Char('j') if ctrl => app.insert_newline(),
                        KeyCode::Char('a') if ctrl => app.cursor_home(),
                        KeyCode::Char('e') if ctrl => app.cursor_end(),
                        KeyCode::Char('u') if ctrl => app.delete_to_line_start(),
                        KeyCode::Char('k') if ctrl => app.delete_to_line_end(),
                        KeyCode::Char('w') if ctrl => app.delete_word_back(),
                        KeyCode::Char('b') if alt => app.cursor_word_left(),
                        KeyCode::Char('f') if alt => app.cursor_word_right(),
                        KeyCode::Tab => app.complete(),
                        KeyCode::Enter if alt => app.insert_newline(),
                        KeyCode::Enter if ctrl => app.steer(),
                        KeyCode::Enter => app.submit(),
                        KeyCode::Backspace if alt => app.delete_word_back(),
                        KeyCode::Backspace => app.backspace(),
                        KeyCode::Delete => app.delete(),
                        KeyCode::Left if ctrl || alt => app.cursor_word_left(),
                        KeyCode::Right if ctrl || alt => app.cursor_word_right(),
                        KeyCode::Left => app.cursor_left(),
                        KeyCode::Right => app.cursor_right(),
                        KeyCode::Esc => app.cancel(),
                        KeyCode::Up if shift => app.scroll_by(-3),
                        KeyCode::Down if shift => app.scroll_by(3),
                        KeyCode::Up => app.on_up(),
                        KeyCode::Down => app.on_down(),
                        KeyCode::PageUp => app.scroll_by(-10),
                        KeyCode::PageDown => app.scroll_by(10),
                        KeyCode::Home if ctrl => {
                            app.autoscroll = false;
                            app.scroll = 0;
                        }
                        KeyCode::End if ctrl => {
                            app.autoscroll = true;
                        }
                        KeyCode::Home => app.cursor_home(),
                        KeyCode::End => app.cursor_end(),
                        KeyCode::Char(c) if !ctrl && !alt => app.on_char(c),
                        _ => {}
                    }
                }
                Event::Mouse(mouse) => match mouse.kind {
                    MouseEventKind::ScrollUp => app.scroll_by(-3),
                    MouseEventKind::ScrollDown => app.scroll_by(3),
                    _ => {}
                },
                _ => {}
            }
        }
        app.tick();
        if app.quit_requested() {
            break;
        }
    }
    Ok(())
}

fn format_transcript_line(text: &str) -> Line<'static> {
    if let Some(user_text) = text.strip_prefix("you : ") {
        Line::from(vec![
            Span::styled("❯ ", Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(user_text.to_string(), Style::default().fg(to_color(HOT_RGB)).add_modifier(Modifier::BOLD)),
        ])
    } else if let Some(tool_name) = text.strip_prefix("tool : ") {
        Line::from(vec![
            Span::styled("  ▸ ", Style::default().fg(to_color(AMBER_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(tool_name.to_string(), Style::default().fg(to_color(HOT_RGB))),
        ])
    } else if text.starts_with("tool ") && (text.contains(" ok") || text.contains(" error")) {
        let is_err = text.contains(" error");
        let c = if is_err { Color::Rgb(255, 107, 107) } else { to_color(SCALE_STOPS[2]) };
        Line::from(vec![
            Span::styled("  └─ ", Style::default().fg(c)),
            Span::styled(text.to_string(), Style::default().fg(to_color(DIM_RGB))),
        ])
    } else if text.starts_with("… +") && text.ends_with("· /tool expands") {
        Line::from(vec![
            Span::styled(format!("  {text}"), Style::default().fg(to_color(DIM_RGB))),
        ])
    } else if text.starts_with("approval ") {
        Line::from(vec![
            Span::styled("◆ APPROVAL: ", Style::default().fg(to_color(AMBER_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(text.to_string(), Style::default().fg(to_color(HOT_RGB))),
        ])
    } else if text.starts_with("error ") {
        Line::from(vec![
            Span::styled("ERROR: ", Style::default().fg(Color::Rgb(255, 107, 107)).add_modifier(Modifier::BOLD)),
            Span::styled(text.to_string(), Style::default().fg(Color::Rgb(255, 150, 150))),
        ])
    } else if text.starts_with("diff : ") {
        Line::from(vec![
            Span::styled("DIFF: ", Style::default().fg(to_color(MARK_TO_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(text.strip_prefix("diff : ").unwrap().to_string(), Style::default().fg(to_color(HOT_RGB))),
        ])
    } else if text.starts_with("--- ") && text.ends_with(" ---") {
        Line::from(vec![
            Span::styled(text.to_string(), Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
        ])
    } else if text.starts_with("  @@") || text.starts_with("@@") {
        Line::from(vec![Span::styled(text.to_string(), Style::default().fg(to_color(MARK_TO_RGB)))])
    } else if text.starts_with("  +") || text.starts_with('+') {
        Line::from(vec![Span::styled(text.to_string(), Style::default().fg(to_color(ACCENT_RGB)))])
    } else if text.starts_with("  -") || text.starts_with('-') {
        Line::from(vec![Span::styled(text.to_string(), Style::default().fg(Color::Rgb(255, 107, 107)))])
    } else if text.starts_with('#') {
        Line::from(vec![Span::styled(text.to_string(), Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD))])
    } else if let Some((topic, comment)) = text.split_once(" : ") {
        Line::from(vec![
            Span::styled(topic.to_string(), Style::default().fg(to_color(MARK_FROM_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(" : ", Style::default().fg(to_color(DIM_RGB))),
            Span::styled(comment.to_string(), Style::default().fg(to_color(HOT_RGB))),
        ])
    } else {
        Line::from(vec![Span::styled(text.to_string(), Style::default().fg(Color::Rgb(214, 239, 228)))])
    }
}

fn build_composer_lines(input: &str, cursor_pos: usize) -> Vec<Line<'static>> {
    if input.is_empty() {
        return vec![Line::from(vec![
            Span::styled("❯ ", Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(
                "Ask anything, @file to mention, or / for commands",
                Style::default().fg(Color::Rgb(77, 106, 96)).add_modifier(Modifier::ITALIC),
            ),
        ])];
    }

    let pos = cursor_pos.min(input.len());
    let (left, right) = input.split_at(pos);
    let combined = format!("{left}█{right}");
    let mut lines = Vec::new();
    for (idx, raw_line) in combined.split('\n').enumerate() {
        let prefix = if idx == 0 { "❯ " } else { "  " };
        let mut spans = vec![Span::styled(
            prefix,
            Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD),
        )];
        if let Some((before_cur, after_cur)) = raw_line.split_once('█') {
            spans.push(Span::styled(before_cur.to_string(), Style::default().fg(to_color(HOT_RGB))));
            spans.push(Span::styled("█", Style::default().fg(to_color(ACCENT_RGB))));
            spans.push(Span::styled(after_cur.to_string(), Style::default().fg(to_color(HOT_RGB))));
        } else {
            spans.push(Span::styled(raw_line.to_string(), Style::default().fg(to_color(HOT_RGB))));
        }
        lines.push(Line::from(spans));
    }
    lines
}

fn draw(frame: &mut ratatui::Frame, app: &mut App) {
    let area = frame.area();
    let width = area.width as usize;

    frame.render_widget(Block::default().style(Style::default().bg(to_color(BG_DARK))), area);

    let cands = app.candidates();
    let has_popup = !app.input.is_empty() && !cands.is_empty();

    let input_line_count = app.input.split('\n').count().max(1);
    let composer_height = (input_line_count as u16 + 2).clamp(3, 10);

    let chunks = Layout::vertical([
        Constraint::Min(1),                   // 0: Transcript (Banner + Conversation)
        Constraint::Length(1),                // 1: Live Status Line
        Constraint::Length(composer_height),  // 2: Rounded Input Box
        Constraint::Length(1),                // 3: Footer Bar
    ])
    .split(area);

    // ------------------------------------------------------------- 0: Transcript
    let info_raw = app.session_info();
    let info_refs: Vec<(&str, &str)> = info_raw.iter().map(|(k, v)| (*k, v.as_str())).collect();
    let mut body = banner::build_banner_lines(width, &info_refs);
    body.push(Line::from(""));

    for s in &app.transcript {
        for sub in s.split('\n') {
            body.push(format_transcript_line(sub));
        }
    }

    let total_lines = body.len();
    let visible_height = chunks[0].height.saturating_sub(2) as usize;
    let max_scroll = total_lines.saturating_sub(visible_height);
    if !app.autoscroll && app.scroll >= max_scroll {
        app.autoscroll = true;
    }
    let scroll_y = if app.autoscroll {
        app.scroll = max_scroll;
        max_scroll
    } else {
        app.scroll.min(max_scroll)
    };

    let transcript_title = Line::from(vec![
        Span::styled(" hydra ", Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
        Span::styled(format!("· {} · {} ", app.model, app.dialect), Style::default().fg(to_color(DIM_RGB))),
    ]);

    let transcript = Paragraph::new(body)
        .block(
            Block::bordered()
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(Color::Rgb(14, 80, 50)))
                .title(transcript_title)
        )
        .wrap(Wrap { trim: false })
        .scroll((scroll_y as u16, 0));
    frame.render_widget(transcript, chunks[0]);

    // ------------------------------------------------------------- 1: Status Line
    let braille_spin = BRAILLE_SPIN[app.spin_i % BRAILLE_SPIN.len()];

    let status_line = if let Some((_, tool)) = &app.approval {
        Line::from(vec![
            Span::styled(" ◆ ", Style::default().fg(to_color(AMBER_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(format!("Approval required for {tool} "), Style::default().fg(to_color(HOT_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(
                "· [y] allow · [a] always thread · [p] always project · [n] skip · [Esc] abort",
                Style::default().fg(to_color(DIM_RGB)),
            ),
        ])
    } else if app.busy_turn.is_some() {
        let busy_text = app.busy_status_text();
        Line::from(vec![
            Span::styled(format!(" {braille_spin} "), Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
            Span::styled(
                format!("{busy_text} "),
                Style::default().fg(Color::Rgb(169, 201, 188)),
            ),
            Span::styled("· Enter queues · Ctrl+Enter steers · Esc interrupts", Style::default().fg(to_color(DIM_RGB))),
        ])
    } else {
        Line::from(vec![
            Span::styled("   ", Style::default()),
            Span::styled("hydra · idle", Style::default().fg(to_color(DIM_RGB))),
        ])
    };
    frame.render_widget(Paragraph::new(status_line), chunks[1]);

    // ------------------------------------------------------------- 2: Input Box
    let border_color = to_color(app.border_rgb());

    let state_tag = if app.approval.is_some() {
        " hydra · approval required "
    } else if app.busy_turn.is_some() {
        " hydra · working "
    } else {
        " hydra "
    };
    let mut left_spans = vec![Span::styled(
        state_tag,
        Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD),
    )];
    if !app.queued.is_empty() {
        left_spans.push(Span::styled(
            format!("· {} queued ", app.queued.len()),
            Style::default().fg(to_color(AMBER_RGB)).add_modifier(Modifier::BOLD),
        ));
    }
    let title_left = Line::from(left_spans);
    let title_right = Line::from(vec![Span::styled(
        format!(" {} · {} · {} ", app.model, app.permission, app.mode),
        Style::default().fg(to_color(DIM_RGB)),
    )]);

    let input_lines = build_composer_lines(&app.input, app.cursor_pos);
    let input_widget = Paragraph::new(input_lines)
        .block(
            Block::bordered()
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(border_color))
                .title(title_left)
                .title(title_right.alignment(ratatui::layout::Alignment::Right)),
        )
        .wrap(Wrap { trim: false });
    frame.render_widget(input_widget, chunks[2]);

    // ------------------------------------------------------------- 3: Footer Bar
    let used_tokens = app.prompt_tokens;
    let pct = if app.window > 0 { (used_tokens as f32 / app.window as f32).clamp(0.0, 1.0) } else { 0.0 };
    let cells: usize = 8;
    let filled = (pct * cells as f32).round() as usize;
    let empty = cells.saturating_sub(filled);

    let mut footer_spans = vec![
        Span::raw(" "),
        Span::styled(" enter ", Style::default().fg(Color::Rgb(214, 239, 228)).bg(Color::Rgb(28, 45, 37)).add_modifier(Modifier::BOLD)),
        Span::styled(" send   ", Style::default().fg(Color::Rgb(95, 125, 114))),
        Span::styled(" alt+enter ", Style::default().fg(Color::Rgb(214, 239, 228)).bg(Color::Rgb(28, 45, 37)).add_modifier(Modifier::BOLD)),
        Span::styled(" newline   ", Style::default().fg(Color::Rgb(95, 125, 114))),
        Span::styled(" esc ", Style::default().fg(Color::Rgb(214, 239, 228)).bg(Color::Rgb(28, 45, 37)).add_modifier(Modifier::BOLD)),
        Span::styled(" cancel   ", Style::default().fg(Color::Rgb(95, 125, 114))),
        Span::styled(" / ", Style::default().fg(Color::Rgb(214, 239, 228)).bg(Color::Rgb(28, 45, 37)).add_modifier(Modifier::BOLD)),
        Span::styled(" commands   ", Style::default().fg(Color::Rgb(95, 125, 114))),
        Span::styled(" @ ", Style::default().fg(Color::Rgb(214, 239, 228)).bg(Color::Rgb(28, 45, 37)).add_modifier(Modifier::BOLD)),
        Span::styled(" files", Style::default().fg(Color::Rgb(95, 125, 114))),
    ];

    let left_w: usize = footer_spans.iter().map(|s| s.content.len()).sum();
    let right_gauge = format!("ctx [{}{}] {:.0}% · {} turns ", "█".repeat(filled), "░".repeat(empty), pct * 100.0, app.turn_n);
    let right_w = right_gauge.len();

    let spacer_len = width.saturating_sub(left_w + right_w);
    footer_spans.push(Span::raw(" ".repeat(spacer_len)));

    footer_spans.push(Span::styled("ctx [", Style::default().fg(to_color(DIM_RGB))));
    footer_spans.push(Span::styled("█".repeat(filled), Style::default().fg(to_color(ACCENT_RGB))));
    footer_spans.push(Span::styled("░".repeat(empty), Style::default().fg(Color::Rgb(44, 63, 56))));
    footer_spans.push(Span::styled(format!("] {:.0}% · {} turns ", pct * 100.0, app.turn_n), Style::default().fg(Color::Rgb(111, 143, 132))));

    frame.render_widget(Paragraph::new(Line::from(footer_spans)), chunks[3]);

    // ------------------------------------------------------------- Autocomplete Popup
    if has_popup {
        let popup_h = (cands.len() as u16 + 2).min(10);
        let popup_w = 74.min(chunks[2].width.saturating_sub(4));
        let popup_y = chunks[2].y.saturating_sub(popup_h);
        let popup_area = Rect::new(chunks[2].x + 2, popup_y, popup_w, popup_h);

        frame.render_widget(Clear, popup_area);

        let selected = app.popup_idx.min(cands.len().saturating_sub(1));
        let max_rows = (popup_h.saturating_sub(2)) as usize;
        let start = if selected >= max_rows { selected + 1 - max_rows } else { 0 };

        let mut lines = Vec::new();
        for (i, (cmd, desc)) in cands.iter().enumerate().skip(start).take(max_rows) {
            let is_sel = i == selected;
            let spans = vec![
                Span::styled(if is_sel { " ▸ " } else { "   " }, Style::default().fg(to_color(ACCENT_RGB))),
                Span::styled(
                    format!("{cmd:22} "),
                    Style::default()
                        .fg(if is_sel { to_color(ACCENT_RGB) } else { to_color(HOT_RGB) })
                        .add_modifier(if is_sel { Modifier::BOLD } else { Modifier::empty() }),
                ),
                Span::styled(desc.to_string(), Style::default().fg(to_color(DIM_RGB))),
            ];
            lines.push(Line::from(spans));
        }

        let popup_block = Paragraph::new(lines).block(
            Block::bordered()
                .border_type(BorderType::Rounded)
                .border_style(Style::default().fg(to_color(SCALE_STOPS[2])))
                .title(Line::from(vec![Span::styled(
                    " Suggestions (↑/↓ select · Tab complete) ",
                    Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD),
                )])),
        );
        frame.render_widget(popup_block, popup_area);
    }
}

fn probe() -> io::Result<()> {
    for width in [20usize, 40, 80] {
        let fit = fit_status(width, '|', "working");
        let overflow = layout::status_overflow(width, &fit);
        println!("width {width} spin {} label {} overflow {overflow}", fit.spin_cols, fit.label_cols);
    }
    println!("endpoints");
    for row in endpoints::local_endpoints() {
        let state = if row.open { "open" } else { "closed" };
        println!("{} {} {state}", row.name, row.target);
    }
    Ok(())
}

#[cfg(test)]
mod key_kind_tests {
    use super::*;

    #[test]
    fn release_events_are_not_key_actions() {
        assert!(handles_key(KeyEventKind::Press));
        assert!(handles_key(KeyEventKind::Repeat));
        assert!(!handles_key(KeyEventKind::Release));
    }

    #[test]
    fn draw_pulses_prompt_bar_border_only_while_turn_is_busy() {
        use banner::{BORDER_IDLE_RGB, BORDER_PULSE_RGB};
        use hydra_stdio::Ev;
        use ratatui::backend::TestBackend;

        let backend = TestBackend::new(80, 24);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new();

        // Idle: prompt bar top-left border cell at (0, 20) is static (31, 122, 69)
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let idle_fg = terminal.backend().buffer()[(0, 20)].fg;
        let transcript_fg = terminal.backend().buffer()[(0, 0)].fg;
        assert_eq!(idle_fg, to_color(BORDER_IDLE_RGB));
        assert_eq!(transcript_fg, Color::Rgb(14, 80, 50));

        // Busy turn at 0.4s (quarter of 1.6s cycle): prompt bar border peaks at (0, 255, 102),
        // while transcript border at (0, 0) remains static (no full-screen glow).
        app.input = "test prompt".into();
        app.cursor_pos = app.input.len();
        app.submit();
        app.pulse_secs = 0.4;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        assert_eq!(terminal.backend().buffer()[(0, 20)].fg, to_color(BORDER_PULSE_RGB));
        assert_eq!(terminal.backend().buffer()[(0, 21)].fg, to_color(BORDER_PULSE_RGB));
        assert_eq!(terminal.backend().buffer()[(0, 22)].fg, to_color(BORDER_PULSE_RGB));
        assert_eq!(terminal.backend().buffer()[(0, 0)].fg, Color::Rgb(14, 80, 50));

        // Busy turn at 1.2s (three-quarter of 1.6s cycle): trough at (31, 122, 69)
        app.pulse_secs = 1.2;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        assert_eq!(terminal.backend().buffer()[(0, 20)].fg, to_color(BORDER_IDLE_RGB));

        // Turn finishes: prompt bar returns to static (31, 122, 69)
        app.apply(Ev::Done {
            turn: "t1".into(),
            text: "ok".into(),
            ok: true,
            cancelled: false,
            plan: None,
            plan_error: None,
        });
        app.tick();
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        assert_eq!(terminal.backend().buffer()[(0, 20)].fg, to_color(BORDER_IDLE_RGB));
    }

    #[test]
    fn draw_footer_gauge_renders_50_percent_for_65536_over_131072_window() {
        use hydra_stdio::Ev;
        use ratatui::backend::TestBackend;
        use serde_json::json;

        std::env::remove_var("HYDRA_CONTEXT_WINDOW");
        let backend = TestBackend::new(120, 24);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new();

        // Context event with window updates the stored window
        app.apply(Ev::Context {
            turn: "t1".into(),
            rules: None,
            skills: vec![],
            mcp_tools: vec![],
            mcp_errors: json!({}),
            window: Some(131_072),
            compacted: None,
        });
        assert_eq!(app.window, 131_072);

        // Prior turn usage should be replaced by most recent turn's prompt tokens
        app.apply(Ev::Usage {
            model: "local".into(),
            prompt: 30_000,
            completion: 10_000,
            cost: 0.0,
        });
        app.apply(Ev::Usage {
            model: "local".into(),
            prompt: 65_536,
            completion: 8_192,
            cost: 0.0,
        });

        // Context event with compacted produces the transcript line
        app.apply(Ev::Context {
            turn: "t2".into(),
            rules: None,
            skills: vec![],
            mcp_tools: vec![],
            mcp_errors: json!({}),
            window: Some(131_072),
            compacted: Some(json!({
                "turns_folded": 3,
                "chars_before": 95000,
                "chars_after": 12000,
            })),
        });
        assert!(app.transcript.iter().any(|l| l == "context : compacted (3 turns folded)"));

        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let buf = terminal.backend().buffer();
        let footer_line: String = (0..120).map(|x| buf[(x, 23)].symbol()).collect();
        assert!(
            footer_line.contains("ctx [████░░░░] 50%"),
            "expected 50% gauge in footer, got: {footer_line:?}"
        );
    }

    #[test]
    fn draw_renders_hello_local_badge_and_20s_waiting_line_transition() {
        use hydra_stdio::Ev;
        use ratatui::backend::TestBackend;
        use std::time::{Duration, Instant};

        let _guard = app::TEST_ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        std::env::remove_var("HYDRA_DEFAULT_MODEL");
        std::env::remove_var("HYDRA_MODEL");
        let backend = TestBackend::new(120, 24);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = App::new();

        // Initialize from hello carrying mode "local" and default_model "gemma-e2b"
        let hello = hydra_stdio::parse_line(
            r#"{"type":"hello","protocol":1,"agent":"hydra","mode":"local","default_model":"gemma-e2b"}"#,
        )
        .unwrap();
        app.apply(hello);

        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let buf = terminal.backend().buffer();
        let top_border: String = (0..120).map(|x| buf[(x, 0)].symbol()).collect();
        let prompt_border: String = (0..120).map(|x| buf[(x, 20)].symbol()).collect();
        assert!(top_border.contains("gemma-e2b"), "top border badge should show gemma-e2b: {top_border:?}");
        assert!(prompt_border.contains("gemma-e2b"), "prompt border badge should show gemma-e2b: {prompt_border:?}");
        assert!(!top_border.contains("muse spark contributor"));
        assert!(!prompt_border.contains("muse spark contributor"));

        // Start a turn and emit preflight_result for route "local"
        app.input = "check status".into();
        app.cursor_pos = app.input.len();
        app.submit();
        let pf = hydra_stdio::parse_line(
            r#"{"type":"preflight_result","id":"t1","turn":"t1","strip":"[local:PASS]","notice":null,"report":{"mode":"local","default_route":"local","default_route_ok":true,"routes":{"local":{"route":"local","status":"PASS","reason":"ok"}}}}"#,
        )
        .unwrap();
        app.apply(pf);

        // Before 20s: status line (row 19) shows Thinking ...
        app.busy_start = Some(Instant::now() - Duration::from_secs(10));
        app.pulse_secs = 10.0;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let status_10s: String = (0..120).map(|x| terminal.backend().buffer()[(x, 19)].symbol()).collect();
        assert!(status_10s.contains("Thinking 10s · working..."), "got: {status_10s:?}");

        // At 20s with no token yet: status line transitions to `waiting on local… 20s`
        app.busy_start = Some(Instant::now() - Duration::from_secs(20));
        app.pulse_secs = 20.0;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let status_20s: String = (0..120).map(|x| terminal.backend().buffer()[(x, 19)].symbol()).collect();
        assert!(status_20s.contains("waiting on local… 20s"), "got: {status_20s:?}");

        // Once a token arrives, waiting line yields back to Thinking
        app.apply(Ev::Token {
            turn: "t1".into(),
            text: "hi".into(),
        });
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let status_after_token: String = (0..120).map(|x| terminal.backend().buffer()[(x, 19)].symbol()).collect();
        assert!(status_after_token.contains("Thinking 20s · working..."), "got: {status_after_token:?}");
    }
}
