# Changelog

## 0.1.1 (2026-11-23)

- Plugin icon (`.claude-plugin/icon.png`, source `docs/icon.src.svg`) for directory listings.
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
