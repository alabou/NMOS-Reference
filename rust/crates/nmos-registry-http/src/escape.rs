// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! HTML escaping, spelled the way Python spells it.
//!
//! Hand-written rather than pulled from a crate, and the reason is one
//! character. `html.escape(quote=True)` -- which is the default, and what
//! `nmos/api/response.py` calls -- maps the apostrophe to `&#x27;`, while the
//! escaping crates differ on it: some emit `&#39;`, some `&apos;`, some leave
//! it alone. `test_html_links.py` asserts exact markup, so a different spelling
//! of the same character is a failing test rather than a cosmetic difference.
//!
//! The full mapping, from CPython's `html/__init__.py`:
//!
//! | character | becomes |
//! |---|---|
//! | `&` | `&amp;` |
//! | `<` | `&lt;` |
//! | `>` | `&gt;` |
//! | `"` | `&quot;` |
//! | `'` | `&#x27;` |
//!
//! Nothing else is touched -- non-ASCII passes through as itself, because the
//! page declares `<meta charset="utf-8">`.
//!
//! The ampersand must be replaced **first**, or the `&` introduced by every
//! other replacement would be escaped again and `<` would render as `&amp;lt;`.
//! Doing it in one pass, as below, makes that ordering impossible to get wrong.

/// Escape a string for HTML, the way `html.escape(s, quote=True)` does.
#[must_use]
pub fn escape(text: &str) -> String {
    // Most values contain nothing to escape, so the common case must not
    // allocate a second time or scan twice.
    if !text
        .bytes()
        .any(|byte| matches!(byte, b'&' | b'<' | b'>' | b'"' | b'\''))
    {
        return text.to_owned();
    }

    let mut out = String::with_capacity(text.len().saturating_add(16));
    for character in text.chars() {
        match character {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            // `&#x27;`, not `&#39;` and not `&apos;`. See the module docs.
            '\'' => out.push_str("&#x27;"),
            other => out.push(other),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_five_characters_python_escapes_are_escaped() {
        assert_eq!(escape("&"), "&amp;");
        assert_eq!(escape("<"), "&lt;");
        assert_eq!(escape(">"), "&gt;");
        assert_eq!(escape("\""), "&quot;");
        assert_eq!(escape("'"), "&#x27;");
    }

    #[test]
    fn the_apostrophe_uses_pythons_hex_spelling() {
        // The whole reason this is hand-written: the escaping crates disagree
        // here, and `test_html_links.py` asserts exact markup.
        assert_eq!(escape("it's"), "it&#x27;s");
        assert!(!escape("'").contains("&#39;"));
        assert!(!escape("'").contains("&apos;"));
    }

    #[test]
    fn an_ampersand_introduced_by_escaping_is_not_escaped_again() {
        // A two-pass implementation that replaces `&` after `<` produces
        // `&amp;lt;` here.
        assert_eq!(escape("<a>"), "&lt;a&gt;");
        assert_eq!(escape("&lt;"), "&amp;lt;");
        assert_eq!(escape("a&b<c"), "a&amp;b&lt;c");
    }

    #[test]
    fn nothing_else_is_touched() {
        // The page declares UTF-8, so non-ASCII stays as itself.
        assert_eq!(escape("café"), "café");
        assert_eq!(escape("日本語"), "日本語");
        assert_eq!(escape("plain text 123"), "plain text 123");
        assert_eq!(escape(""), "");
    }

    #[test]
    fn a_script_tag_cannot_survive() {
        assert_eq!(
            escape(r#"<script>alert("x")</script>"#),
            "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;",
        );
    }
}
