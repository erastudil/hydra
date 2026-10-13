//! Hydra terminal banner: creature ASCII art with green phosphor gradients,
//! glowing ember and gold eyes, bone fangs, marsh water, and 3D emerald-to-cyan wordmark.

use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

pub type Rgb = (u8, u8, u8);

pub const SCALE_STOPS: &[Rgb] = &[(170, 255, 214), (52, 236, 146), (14, 186, 124), (8, 120, 104)];
pub const SCALE_SHADE: Rgb = (6, 78, 72);
pub const EYE_RGB: Rgb = (255, 70, 40);
pub const EYE_IMMORTAL_RGB: Rgb = (255, 212, 60);
pub const FANG_RGB: Rgb = (246, 242, 226);
pub const WATER_RGB: Rgb = (30, 104, 170);
pub const WATER_CREST_RGB: Rgb = (70, 170, 220);
pub const MARK_FROM_RGB: Rgb = (64, 255, 166);
pub const MARK_TO_RGB: Rgb = (0, 190, 255);
pub const MARK_SHADOW_RGB: Rgb = (12, 92, 84);
pub const HOT_RGB: Rgb = (236, 255, 245);
pub const DIM_RGB: Rgb = (112, 140, 130);
pub const ACCENT_RGB: Rgb = (52, 236, 146);
pub const AMBER_RGB: Rgb = (255, 184, 48);
pub const BG_DARK: Rgb = (10, 15, 13);
pub const BRAILLE_SPIN: &[char] = &['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

pub const HYDRA_ART_LARGE: &str = r#"                            _/\_     /\               /\     _/\_
                         _-'  _ `.  /  \   _.---._   /  \  .' _  `-_
                       <'_  -@'   \ \   `-'       `-'   / /   `@-  _`>
                        \vv.      |  \  `-._     _.-'  /  |      .vv/
                       <_ )       |  |   (@)     (@)   |  |       ( _>
                        `^'      /   \   `-.     .-'   /   \      `^'
                       _/\_/\_   |    \  \vVVVVVVVv/  /    |   _/\_/\_
                   _.-'  _    `-.\     \  )       (  /     /.-'    _  `-._
                 <'_   -@'       \\     \  `^^^^^'  /     //       `@-   _`>
                  \vVv.          | \     `.       .'     / |          .vVv/
      _/\_/\_    <__  )          | \       |     |       / |          (  __>    _/\_/\_
  _.-'  _    `-.  `^^^'          |  \      |     |      /  |          `^^^'  .-'    _  `-._
<'_   -@'       \      `.       /\   \     |     |     /   /\       .'      /       `@-   _`>
 \vVv.          |       |       | \   \    |     |    /   / |       |       |          .vVv/
<__  )          |       \       \  \   \   |     |   /   /  /       /       |          (  __>
 `^^^'          |        \       \  \   \  |     |  /   /  /       /        |          `^^^'
      `.       /          \       \  \   \ |     | /   /  /       /          \       .'
       \       \           \       \ \   \ |     | /   / /       /           /       /
        \       \           \       \ \   \|     |/   / /       /           /       /
         \       \           \       \ \   |     |   / /       /           /       /
          \       \           \       \|   |     |   |/       /           /       /
           \       \   _.--~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~--._   /       /
            \     _.-~~                                               ~~-._     /
             \_.-~     .--~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~--.     ~-._/
           .-~      .-~                                               ~-.      ~-.
         .'       .'   .-~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~-.   `.       `.
        (        (    (                                               )    )        )
~^~^~^~^~`-.____ `-.__ `-._________________________________________.-' __.-' ____.-'~^~^~^~^~"#;

pub const HYDRA_ART_MEDIUM: &str = r#"                     /\           /\
                    /  \ _.---._ /  \
      _/\_/\        \   `       '   /        /\_/\_
   _-'  _   `.       |  (@)   (@)  |       .'   _  `-_
 <'_  -@'     \      \   `-. .-'   /      /     `@-  _`>
  \vv.        |       \ \vVVVVVv/ /       |        .vv/
 <_  )        |        \ `^^^^^' /        |        (  _>
  `^^'        |         `.     .'         |        `^^'
      `.     /            |   |            \     .'
      \     \             |   |             /     /
       \     \            |   |            /     /
        \     \           |   |           /     /
         \     \          |   |          /     /
          \     \         |   |         /     /
           \     \        |   |        /     /
            \ _.--~~~~~~~~~~~~~~~~~~~~~--._ /
         _.-~~  .--~~~~~~~~~~~~~~~~~~~--.  ~~-._
      .-~     .~   .-~~~~~~~~~~~~~~~-.   ~.     ~-.
~^~^~^`-.___ `-.__ `-._____________.-' __.-' ___.-'^~^~^~"#;

pub const HYDRA_ART_COMPACT: &str = r#"             /\       /\             
            /  \ _._ /  \            
      _/\_  \   `   '   /  _/\_      
   _-' _  `. | (@) (@) | .'  _ `-_   
 <'_ -@'   \ \  `-.-'  / /   `@- _`> 
  \vv.     |  \ \vVv/ /  |     .vv/  
 <_ )      |   \ `^' /   |      ( _> 
  `^'      |    `. .'    |      `^'  
      `.  /       |       \  .'      
       \  \ _.--~~~~~--._ /  /       
     _.-~~ .--~~~~~~~~~--. ~~-._     
  .-~   .~  .-~~~~~~~~~-.  ~.   ~-.  
~^~^`-._ `-.____.-'-.____.-' _.-'^~^~"#;

pub const HYDRA_WORDMARK: &str = "██╗  ██╗██╗   ██╗██████╗ ██████╗  █████╗ \n██║  ██║╚██╗ ██╔╝██╔══██╗██╔══██╗██╔══██╗\n███████║ ╚████╔╝ ██║  ██║██████╔╝███████║\n██╔══██║  ╚██╔╝  ██║  ██║██╔══██╗██╔══██║\n██║  ██║   ██║   ██████╔╝██║  ██║██║  ██║\n╚═╝  ╚═╝   ╚═╝   ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝";

pub const HYDRA_LOGO_ASCII: &str = r#"  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ "#;

pub fn to_color(rgb: Rgb) -> Color {
    Color::Rgb(rgb.0, rgb.1, rgb.2)
}

pub fn lerp_rgb(a: Rgb, b: Rgb, t: f32) -> Rgb {
    let t = t.clamp(0.0, 1.0);
    (
        (a.0 as f32 + (b.0 as f32 - a.0 as f32) * t).round() as u8,
        (a.1 as f32 + (b.1 as f32 - a.1 as f32) * t).round() as u8,
        (a.2 as f32 + (b.2 as f32 - a.2 as f32) * t).round() as u8,
    )
}

pub fn gradient(stops: &[Rgb], t: f32) -> Rgb {
    if stops.is_empty() {
        return (0, 0, 0);
    }
    if stops.len() == 1 {
        return stops[0];
    }
    let t = t.clamp(0.0, 1.0);
    let n = (stops.len() - 1) as f32;
    let idx = (t * n).floor() as usize;
    if idx >= stops.len() - 1 {
        return stops[stops.len() - 1];
    }
    let local_t = (t * n) - idx as f32;
    lerp_rgb(stops[idx], stops[idx + 1], local_t)
}

pub fn paint_art(art: &str) -> Vec<Vec<Span<'static>>> {
    let raw_lines: Vec<&str> = art.lines().collect();
    let height = raw_lines.len();
    let width = raw_lines.iter().map(|l| l.chars().count()).max().unwrap_or(0);
    let axis = (width as f32 - 1.0) / 2.0;

    let mut result = Vec::new();
    for (y, line) in raw_lines.iter().enumerate() {
        let t = y as f32 / (height.saturating_sub(1).max(1) as f32);
        let base = gradient(SCALE_STOPS, t);
        let water_row = y == height - 1;

        let mut spans = Vec::new();
        let mut curr_text = String::new();
        let mut curr_color: Option<Rgb> = None;
        let mut curr_bold = false;

        for (x, ch) in line.chars().enumerate() {
            if ch == ' ' {
                if !curr_text.is_empty() {
                    let mut s = Style::default();
                    if let Some(c) = curr_color {
                        s = s.fg(to_color(c));
                    }
                    if curr_bold {
                        s = s.add_modifier(Modifier::BOLD);
                    }
                    spans.push(Span::styled(std::mem::take(&mut curr_text), s));
                }
                spans.push(Span::raw(" "));
                curr_color = None;
                curr_bold = false;
                continue;
            }

            let mut bold = false;
            let rgb = if water_row && (ch == '~' || ch == '^') {
                if ch == '~' {
                    WATER_RGB
                } else {
                    WATER_CREST_RGB
                }
            } else if ch == '@' {
                bold = true;
                if (x as f32 - axis).abs() < 9.0 {
                    EYE_IMMORTAL_RGB
                } else {
                    EYE_RGB
                }
            } else if ch == 'v' || ch == 'V' || ch == '^' {
                FANG_RGB
            } else {
                let dist = (x as f32 - axis).abs() / axis.max(1.0);
                lerp_rgb(base, SCALE_SHADE, dist * 0.45)
            };

            if curr_color != Some(rgb) || curr_bold != bold {
                if !curr_text.is_empty() {
                    let mut s = Style::default();
                    if let Some(c) = curr_color {
                        s = s.fg(to_color(c));
                    }
                    if curr_bold {
                        s = s.add_modifier(Modifier::BOLD);
                    }
                    spans.push(Span::styled(std::mem::take(&mut curr_text), s));
                }
                curr_color = Some(rgb);
                curr_bold = bold;
            }
            curr_text.push(ch);
        }

        if !curr_text.is_empty() {
            let mut s = Style::default();
            if let Some(c) = curr_color {
                s = s.fg(to_color(c));
            }
            if curr_bold {
                s = s.add_modifier(Modifier::BOLD);
            }
            spans.push(Span::styled(curr_text, s));
        }

        result.push(spans);
    }
    result
}

pub fn paint_wordmark() -> Vec<Vec<Span<'static>>> {
    let raw_lines: Vec<&str> = HYDRA_WORDMARK.lines().collect();
    let width = raw_lines.iter().map(|l| l.chars().count()).max().unwrap_or(0);

    let mut result = Vec::new();
    for line in raw_lines {
        let mut spans = Vec::new();
        let mut curr_text = String::new();
        let mut curr_color: Option<Rgb> = None;
        let mut curr_bold = false;

        for (x, ch) in line.chars().enumerate() {
            if ch == ' ' {
                if !curr_text.is_empty() {
                    let mut s = Style::default();
                    if let Some(c) = curr_color {
                        s = s.fg(to_color(c));
                    }
                    if curr_bold {
                        s = s.add_modifier(Modifier::BOLD);
                    }
                    spans.push(Span::styled(std::mem::take(&mut curr_text), s));
                }
                spans.push(Span::raw(" "));
                curr_color = None;
                curr_bold = false;
                continue;
            }

            let (rgb, bold) = if ch == '█' {
                let t = x as f32 / (width.saturating_sub(1).max(1) as f32);
                (lerp_rgb(MARK_FROM_RGB, MARK_TO_RGB, t), true)
            } else {
                (MARK_SHADOW_RGB, false)
            };

            if curr_color != Some(rgb) || curr_bold != bold {
                if !curr_text.is_empty() {
                    let mut s = Style::default();
                    if let Some(c) = curr_color {
                        s = s.fg(to_color(c));
                    }
                    if curr_bold {
                        s = s.add_modifier(Modifier::BOLD);
                    }
                    spans.push(Span::styled(std::mem::take(&mut curr_text), s));
                }
                curr_color = Some(rgb);
                curr_bold = bold;
            }
            curr_text.push(ch);
        }

        if !curr_text.is_empty() {
            let mut s = Style::default();
            if let Some(c) = curr_color {
                s = s.fg(to_color(c));
            }
            if curr_bold {
                s = s.add_modifier(Modifier::BOLD);
            }
            spans.push(Span::styled(curr_text, s));
        }

        result.push(spans);
    }
    result
}

pub fn render_session_card(info: &[(&str, &str)]) -> Vec<Vec<Span<'static>>> {
    let key_w = info.iter().map(|(k, _)| k.len()).max().unwrap_or(0);
    let mut rows = Vec::new();
    for (k, v) in info {
        let spans = vec![
            Span::styled("│  ", Style::default().fg(to_color(SCALE_STOPS[2]))),
            Span::styled(format!("{:width$}  ", k, width = key_w), Style::default().fg(to_color(DIM_RGB))),
            Span::styled(v.to_string(), Style::default().fg(to_color(HOT_RGB)).add_modifier(Modifier::BOLD)),
        ];
        rows.push(spans);
    }
    rows
}

fn span_width(spans: &[Span]) -> usize {
    spans.iter().map(|s| unicode_width::UnicodeWidthStr::width(s.content.as_ref())).sum()
}

pub fn build_banner_lines(cols: usize, info: &[(&str, &str)]) -> Vec<Line<'static>> {
    let (art_str, art_w) = if cols >= 144 {
        (HYDRA_ART_LARGE, 96)
    } else if cols >= 104 {
        (HYDRA_ART_MEDIUM, 58)
    } else if cols >= 84 {
        (HYDRA_ART_COMPACT, 38)
    } else {
        ("", 0)
    };

    let wordmark_spans = paint_wordmark();
    let mark_w = 41;
    let gap = 2;

    let tagline_spans = vec![
        Span::styled("Sovereign Multi-Headed AI Shell · ", Style::default().fg(to_color(DIM_RGB))),
        Span::styled("v1.2.2", Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD)),
    ];

    let card_rows = render_session_card(info);

    let side_by_side = !art_str.is_empty() && cols >= art_w + gap + mark_w;

    if side_by_side {
        let art_rows = paint_art(art_str);

        let mut right_rows: Vec<Vec<Span<'static>>> = Vec::new();
        for row in wordmark_spans {
            right_rows.push(row);
        }
        right_rows.push(vec![Span::raw("")]);
        right_rows.push(tagline_spans);
        right_rows.push(vec![Span::raw("")]);
        for row in card_rows {
            right_rows.push(row);
        }

        let total_rows = art_rows.len().max(right_rows.len());
        let mut lines = Vec::new();

        for i in 0..total_rows {
            let mut line_spans = Vec::new();

            if i < art_rows.len() {
                let left = &art_rows[i];
                let w = span_width(left);
                line_spans.extend(left.clone());
                if w < art_w {
                    line_spans.push(Span::raw(" ".repeat(art_w - w)));
                }
            } else {
                line_spans.push(Span::raw(" ".repeat(art_w)));
            }

            line_spans.push(Span::raw(" ".repeat(gap)));

            if i < right_rows.len() {
                line_spans.extend(right_rows[i].clone());
            }

            lines.push(Line::from(line_spans));
        }

        lines
    } else {
        // Stacked layout for narrow terminals
        let mut lines = Vec::new();

        let stacked_art = if cols >= 62 {
            HYDRA_ART_MEDIUM
        } else if cols >= 44 {
            HYDRA_ART_COMPACT
        } else {
            ""
        };

        if !stacked_art.is_empty() {
            for row in paint_art(stacked_art) {
                lines.push(Line::from(row));
            }
            lines.push(Line::from(""));
        }

        if cols >= 46 {
            for row in wordmark_spans {
                lines.push(Line::from(row));
            }
        } else {
            for line in HYDRA_LOGO_ASCII.lines() {
                lines.push(Line::from(vec![Span::styled(line.to_string(), Style::default().fg(to_color(ACCENT_RGB)).add_modifier(Modifier::BOLD))]));
            }
        }
        lines.push(Line::from(""));
        lines.push(Line::from(tagline_spans));
        lines.push(Line::from(""));

        for row in card_rows {
            lines.push(Line::from(row));
        }

        lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_paint_art_generates_colored_rows() {
        let rows = paint_art(HYDRA_ART_MEDIUM);
        assert!(!rows.is_empty());
        assert_eq!(rows.len(), 19);
        let first_row = &rows[0];
        assert!(!first_row.is_empty());
    }

    #[test]
    fn test_paint_wordmark_six_rows() {
        let rows = paint_wordmark();
        assert_eq!(rows.len(), 6);
    }

    #[test]
    fn test_build_banner_lines_widths() {
        let info = [("Session", "test"), ("Model", "mimo")];
        for w in [40, 80, 100, 120, 160] {
            let lines = build_banner_lines(w, &info);
            assert!(!lines.is_empty(), "banner generated for width {w}");
        }
    }
}

