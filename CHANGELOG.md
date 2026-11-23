# Changelog

## 0.1.3 (2026-11-23)

- Pages generated from Markdown carry a Content-Security-Policy that blocks scripts, event handlers, javascript: URLs, plugins and frames when the page is opened. This is the guarantee.
- As a best-effort second layer, the converter removes on* event handlers and srcdoc from tags in ordinary markup, and puts "unsafe:" in front of any link, image or other URL attribute whose scheme is not http, https or mailto. The checker ignores the prefix, so a Markdown page still compares with an HTML page.

## 0.1.2 (2026-11-23)

- Icon re-encoded as a smaller palette PNG.
- Design source files moved out of the repository; the plugin folder now holds only file types the Claude plugin directory accepts.

## 0.1.1 (2026-11-23)

- Plugin icon for directory listings.
- skills.sh install-count badge in the README.
- Speed tests compare run time at size n and 2n instead of a fixed time limit, so they
  pass on slower CI runners and still catch a quadratic loop.
- CI runs the Python 3.9 and 3.13 jobs to completion even when one fails.

## 0.1.0 (2026-11-23)

- First public release: translator prompt, glossary template with confidence levels,
  back-translation round, hreflang wiring notes.
- Checker runs without a fact-check sidecar (`--en/--tr/--lang`) and reads Markdown.
- Language files: en (source), pt, es, de, zh-Hans.
- Lessons from real runs: back-translator translates quotes and never sees the modal
  map; translator keeps pronoun referents and adds no numbers; known checker limits.
