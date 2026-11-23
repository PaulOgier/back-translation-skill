---
name: back-translation
description: Translate a finished English article (HTML, PHP or Markdown) into another language without adding, dropping or changing a fact, then prove it. A different model writes the translation, a second model back-translates every block without seeing the English, a third compares, and a stdlib Python checker blocks on any number, date, citation, code span, link, table cell or must/may change. Also wires the page in for search (own URL, self-canonical, hreflang, lang, disclosure). Use when asked to translate an article, blog post or documentation page, add a language version of a page, check an existing translation against its English, or set up hreflang for a translated page.
---

# Back-translation: translate an article, add no facts

The English article is the source of truth. A translation adds nothing, drops nothing,
keeps every number, date, citation, code span and link, and keeps the strength of every
"must", "should" and "may". Nobody who speaks the target language has to review it,
which is exactly why the checks below exist: a model translation reads fluently even
when it is wrong.

Scripts live in `scripts/` next to this file. They are standard-library Python and run
on Python 3.9 or later. Check with both the newest Python you have and the oldest you
support: HTML parsing changed between versions.

## Before translating

1. **Is there demand?** Check search data for the target language and country before
   translating (Search Console by country, and keyword volume for the native-language
   query). In many markets people search the English technical terms. Include a control
   term you know has volume: a "none" from a keyword tool is a floor, not a zero.
2. **Is the English finished?** Translate only English that is final. A correction is
   made in English first, then translated again. Figures are never updated during
   translation.
3. **Images with text.** List every image that carries words (infographics, share
   cards). Each language needs its own version before it ships.
4. **Register.** Pick the form of address once per language (du/Sie, tu/você,
   tú/usted) and record it in the glossary.

## Build the glossary

Copy `references/glossary-template.md` into the project. Terms come from the vendor's own
localised help pages: use them for terminology only, never for facts. Every vendor term
records its **Source** (full URL, date read, and MT or HT where the vendor marks a page
as machine or human translated) and a **Confidence**:

- **high**: two independent vendor pages agree
- **medium**: one human-translated vendor page
- **low**: a machine-translated page, or no localised page so the choice is ours

Low terms are marked in the translator prompt ("flag if you would choose differently")
and re-checked on the next run. Terms kept only for consistency with an earlier
translation are marked "house" and carry no confidence. Console and UI labels stay in
English with a translation in brackets unless the vendor's localised help names the
label.

## Hand the translation over

1. Fill `references/translator-prompt.md`: language, register, number format and modal
   forms from `scripts/languages/<lang>.json`, the glossary, an earlier translation as a
   wiring example if there is one, and the full English source.
2. Give it to a **different model** from the one that will review it: another tool, or a
   subagent on another model. The prompt carries raw page source; hand it over as a
   file, not pasted through Markdown, which eats tags.
3. Ask for the raw source (a file, or the code block's copy button): a chat tool can hand
   back the rendered page, with links rewritten. Save it beside the English (for example
   `pt/<slug>.md`) and keep its "Judgement calls" list.

## Review the translation

1. **Mechanical check.** From the project root (paths must sit under `--root`, which
   defaults to the current directory), without a fact-check sidecar:

       python3 <skill dir>/scripts/verify_translation.py --en <english> --tr <translation> --lang <code>

   Fix every blocker. With the `article-fact-check` sidecar format (see README), pass the
   translation JSON instead and the per-claim checks run too.
2. **Read it against the English, block by block.** Meaning, scope words (all, only,
   some, never), modal strength, negation, tone, anything stiff or literal. For each
   disagreement give the English, the translation, your wording and why. Rule on each of
   the translator's judgement calls; the meaning-changing ones go to the author.
3. **Back-translation, different model, every block, two rounds.** Export aligned
   blocks:

       python3 <skill dir>/scripts/export_blocks.py <english> <translation> <out-dir>

   Give a fresh agent only `<out-dir>/translation-only/` so it never sees the English.
   A separate comparator agent checks its output against `blocks_en.txt` with
   `references/back-translation.md`. Every block, not only the claims: numbers, negation,
   modal strength, scope, added or dropped qualifiers, swapped table labels. Record each
   verdict (`block`, `round`, `verdict`, `model`, and the block's `sha256` from
   `blocks_sha256.json`; title and description get records too) in a JSON list and pass it with
   `--back-translation`. Round two re-checks round one's fixes.
4. **Humanise in the target language, then repeat steps 1 and 3** on what changed. Tone
   rewriting is where meaning drifts. See README "Humanizers" for vetted per-language
   options; read any third-party skill before installing it.
5. **Outside feedback.** Reject any suggestion that adds a qualifier the English lacks.
   Back-translate edited passages and record them as their own round.

## What the checker covers

Numbers block by block (with units, comparators, scale words and number words), dates,
`[n]` citation markers, code/kbd/samp/pre, links and media, the sequence of paragraphs,
list items, headings and table cells, title and meta and JSON-LD numbers, and modal
strength with negation (per claim with a sidecar, per block without). Quotes are
translated in the body; the English original appears once, labelled, in the sources.
Jokes and asides are where back-translation fails silently: translate the meaning and
flag each one in the handover.

Threat model: an honest translator (a model) making mistakes, not an adversary crafting
markup to pass the gate.

## Localised images

Render each language's image from the same source as the English one, reusing the
page's approved wording and the locale's number format. Translated strings run longer:
measure each text cell after rendering. Save under `<image folder>/<lang>/<same file
name>`; the checker accepts a localised file only in the page's own language folder.
Back-translate image text and alt text separately, with ` | ` between table cells so
swapped columns show.

## Wiring the page in

See `references/hreflang.md` for the full rules from Google Search Central. In short:
own URL per language in a subdirectory, self-canonical, reciprocal hreflang on every
version plus `x-default`, `<html lang>`, translated title and description and chrome,
plain-link language switcher with no auto-redirect, and a disclosure on every
translated page that it was translated with AI from the English original, with a link
to the English and a way to report a translation error.

## Staleness

Each translation records the English version it was made from (`translation_of` and
`source_updated` in front matter, or the sidecar hash). When the English changes, the
page shows "English updated on <date>" with a link until the translation is reviewed
again.

## Known limits of the checker

Each of these has been seen. Back-translation is what catches the misses.

- A unit binds across a noun: "two mailboxes for years" reads as "2 years"; a longer
  translated noun phrase then reads as a bare 2 and blocks. Shorten the phrase.
- Spanish "desde X hasta Y" reads "hasta" as "at most"; "de X a Y" is exact and passes.
- Negation moved between two sentences of one block, and negation in languages without a
  negator list (zh-Hans), are left to the back-translation.
- English "can't" or "cannot" rendered as a plain negation ("não lembram") warns instead of
  blocking in no-sidecar mode, because it is usually inability. Check that it is not a
  permission ("you cannot share it") in the back-translation.
- Loose text outside `<main>` is not counted, because translated templates often render
  the chrome from code.
- Nouns that look like modals ("the need", "a can", "May") can block; reword or accept
  the blocker after checking.
- Attributes other than alt, title, aria-label, placeholder and value (for example MDX
  component props) are not compared.
- JSON-LD step text, and alt text outside a block (og:image:alt, twitter:image:alt), are
  not exported for back-translation yet: export them by hand into `translation-only/`.
- Front matter values other than title and description are compared for numbers only,
  and they are not exported for back-translation: export a prose key such as `summary:`
  by hand. Keys that are bookkeeping (slug, aliases, weight, url, permalink, layout,
  draft, translated_by, translation_*, source_*, and any key containing "date" or
  lastmod) and values that are dates are not compared. A key present only in the
  translation is ignored; one present only in the English blocks.
- `<br>` inside text outside block tags does not count as a new run, so a dropped line
  after a `<br>` in a `<div>` is left to the back-translation.
- In zh-Hans, a number written with a space as the thousands separator (`10 123`) can be
  read as two numbers and block.

## What this cannot catch

Correct but unidiomatic wording, and nuance that both models get wrong in the same way.
The disclosure covers that honestly; say so in every handover.
