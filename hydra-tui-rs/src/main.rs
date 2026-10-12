mod app;
mod endpoints;
mod layout;

use std::io::{self, stdout};
use std::time::Duration;

use crossterm::event::{self, Event, KeyCode, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::layout::{Constraint, Layout};
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Paragraph, Wrap};
use ratatui::Terminal;

use app::{log_layout, App};
use layout::fit_status;

fn main() -> io::Result<()> {
    let mut args = std::env::args().skip(1);
    if args.any(|a| a == "--probe") {
        return probe();
    }
    let mut app = App::new();
    enable_raw_mode()?;
    let mut out = stdout();
    execute!(out, EnterAlternateScreen)?;
    let backend = ratatui::backend::CrosstermBackend::new(out);
    let mut terminal = Terminal::new(backend)?;
    let result = run(&mut terminal, &mut app);
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    result
}

fn run(terminal: &mut Terminal<ratatui::backend::CrosstermBackend<io::Stdout>>, app: &mut App) -> io::Result<()> {
    loop {
        terminal.draw(|frame| draw(frame, app))?;
        if event::poll(Duration::from_millis(80))? {
            if let Event::Key(key) = event::read()? {
                match key.code {
                    KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => break,
                    KeyCode::Char(c) => app.on_char(c),
                    KeyCode::Backspace => app.backspace(),
                    KeyCode::Enter => app.submit(),
                    KeyCode::Esc => app.cancel(),
                    KeyCode::Up => app.scroll_by(-1),
                    KeyCode::Down => app.scroll_by(1),
                    KeyCode::PageUp => app.scroll_by(-10),
                    KeyCode::PageDown => app.scroll_by(10),
                    KeyCode::Home => {
                        app.autoscroll = false;
                        app.scroll = 0;
                    }
                    KeyCode::End => app.autoscroll = true,
                    _ => {}
                }
            }
        }
        app.tick();
        if app.quit_requested() {
            break;
        }
    }
    Ok(())
}

fn draw(frame: &mut ratatui::Frame, app: &mut App) {
    let area = frame.area();
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(1),
        Constraint::Length(3),
        Constraint::Length(1),
    ])
    .split(area);
    let width = chunks[1].width as usize;
    let fit = fit_status(width, app.frame(), &app.status_label);
    let overflow = layout::status_overflow(width, &fit);
    let thinking_in_status = fit.label.contains('\n');
    if !app.log_once {
        log_layout(chunks[1].width, fit.spin_cols, fit.label_cols, overflow, thinking_in_status, chunks[1].y == chunks[3].y);
        app.log_once = true;
    }
    let mut body: Vec<Line> = Vec::new();
    for s in &app.transcript {
        for sub in s.split('\n') {
            body.push(Line::from(sub));
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
    let transcript = Paragraph::new(body)
        .block(Block::bordered().title("hydra"))
        .wrap(Wrap { trim: false })
        .scroll((scroll_y as u16, 0));
    frame.render_widget(transcript, chunks[0]);
    let mut spans = vec![Span::styled(fit.spin.clone(), Style::default().fg(Color::Green))];
    if !fit.label.is_empty() {
        spans.push(Span::raw(" "));
        spans.push(Span::raw(fit.label.clone()));
    }
    let status = Paragraph::new(Line::from(spans));
    frame.render_widget(status, chunks[1]);
    let input = Paragraph::new(Line::from(format!("> {}", app.input))).block(Block::bordered().title("prompt"));
    frame.render_widget(input, chunks[2]);
    let footer_text = layout::clip_cols(&app.footer(), chunks[3].width as usize);
    frame.render_widget(Paragraph::new(footer_text), chunks[3]);
}

fn probe() -> io::Result<()> {
    for width in [20usize, 40, 80] {
        let fit = fit_status(width, '|', "working");
        let overflow = layout::status_overflow(width, &fit);
        log_layout(width as u16, fit.spin_cols, fit.label_cols, overflow, false, false);
        println!("width {width} spin {} label {} overflow {overflow}", fit.spin_cols, fit.label_cols);
    }
    println!("endpoints");
    for row in endpoints::local_endpoints() {
        let state = if row.open { "open" } else { "closed" };
        println!("{} {} {state}", row.name, row.target);
    }
    Ok(())
}
