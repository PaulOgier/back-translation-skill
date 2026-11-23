# Translator handover prompt

The orchestrating session fills the `{{...}}` fields, then gives the prompt to a
different model from the one that will review the result. The translator writes the
page; the orchestrating session does everything after (SKILL.md "Review the
translation").

How to fill it:
- `{{LANG}}`, `{{LANG_CODE}}`: for example Spanish, `es`. `{{HTML_LANG}}`: the page's `<html lang>`, for example `es`, `pt-BR`, `zh-Hans`.
- `{{REGISTER}}`: the form of address chosen for the language (du/Sie, tu/você, tú/usted), from the glossary.
- `{{NUMBERS}}`: the decimal and group separators and the scale words from `scripts/languages/<lang>.json` (`decimal`, `group`, `scale_words`), plus its `notes`.
- `{{MODALS}}`: one line per English modal with the forms the checker accepts, from the same file's `normative`, and every `forbidden` form with its reason.
- `{{GLOSSARY}}`: the glossary for the language. Mark each low-confidence term "(low: flag under Judgement calls if you would choose differently)".
- `{{VOICE}}`: one or two sentences describing the author's voice (for example: plain, direct, first person, a little dry).
- `{{EXAMPLE}}`: the path or full text of an earlier translation on the same site, if there is one, as a model for the wiring only.
- `{{ENGLISH}}`: the full English page source, attached as a file.
- Remove any section that does not apply. Do not add facts to the prompt.

---

You are translating a published, fact-checked article from English into {{LANG}}. Nobody who speaks {{LANG}} will review it before it is published, so accuracy comes first and readable, natural {{LANG}} a close second. A second model will back-translate your work block by block and a script will compare it with the English, so anything you add, drop, soften or strengthen will be caught.

## What to deliver

1. The complete translated page source: the same file as the English below, with only the human-readable text translated. Change nothing else: tags, attributes other than the ones listed under "Translate", template code, classes, ids, links, image paths, citation markers and their position all stay exactly as they are. In Markdown, keep every heading level, list marker, table row and column, code fence and link target.
2. After the page, a short list headed "Judgement calls": every place where you chose between two readings, rendered a joke, idiom or aside by its meaning instead of its words, kept an English term, or were unsure. One line each, with the English and your choice.

## Translate

- Body text: headings, paragraphs, list items, table cells and captions.
- Title and description (`<title>` and meta description, or the `title` and `description` front matter keys), and og/twitter text and image alt text.
- JSON-LD text values (headline, description, breadcrumb names, step text). Leave every URL and date in JSON-LD as it is.
- `alt`, `title` and `aria-label` attributes, and Markdown image alt text.
- Quotations from sources: translate them in the body, inside the same quotation marks.

## Keep exactly as written

- Every number, with its unit, comparator (at least, up to, more than) and precision. Write numbers in {{LANG}} format: {{NUMBERS}}. English billion is 10^9 and trillion is 10^12: use the {{LANG}} word for that value, not the word that looks the same.
- Dates: same day, month and year, written the {{LANG}} way.
- Code, commands, configuration values, hostnames, file names, product names, standard names and example passwords.
- Citation markers such as `[3]`: each stays at the end of the same claim it ends in the English, in the same block.
- Structure: one translated block for every English block, in the same order. Do not merge or split paragraphs, list items or table rows.
- Anything inside an element marked `data-i18n-skip`.

## Meaning rules

- Translate sentence for sentence. Add no fact, example, explanation or qualifier that is not in the English, and drop none. Watch scope words (all, only, some, every, any, never) and hedges (about, probably, may).
- Modal strength must match exactly. Use these renderings: {{MODALS}}. "Must not" (forbidden) and "need not" (not required) are different and must stay different.
- A pronoun keeps its referent: "out of its hands" about a link must not become the person's hands. "Either mailbox" is not "one of the two mailboxes": that adds a number.
- Ranges: "from X to Y" as a plain range (Spanish "de X a Y", not "desde X hasta Y", which reads as "at most").
- Negation stays on the same word. Do not turn a negative into a positive with an antonym or the reverse.
- Keep the author's person and opinions as the author's own.
- Product UI labels stay in English with a {{LANG}} gloss in brackets the first time, unless the vendor's own {{LANG}} help uses a translated label: {{GLOSSARY}}.

## Tone

{{VOICE}} Write it the way a {{LANG}}-speaking professional would write for colleagues, not the way a manual or a marketing page reads. Address the reader as {{REGISTER}}, consistently. Prefer the natural {{LANG}} phrase over a literal one when the meaning is identical. Avoid the stock phrases that make text sound machine-written in {{LANG}}: inflated adjectives, "it is important to note", tidy triplets, and dashes used as decoration. Do not make it more formal, more emphatic or friendlier than the English.

## Page wiring (only if the page source shows it)

Set `<html lang="{{HTML_LANG}}">` (or the `lang` front matter key), the canonical and og:url to the `/{{LANG_CODE}}/` URL, the BreadcrumbList item to match, and keep everything else the template does. Do not write the notice that the page was translated with AI; it is added afterwards. Follow the structure of {{EXAMPLE}} if given. If the English page has an image with text in it, leave its path alone and say so under "Judgement calls"; a localised image is made separately.

## The English page

{{ENGLISH}}
