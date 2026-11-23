# Back-translation round

Two agents, both on a different model from the translator, and never the same agent
for both jobs.

## 1. Back-translator

Give it only `<out-dir>/translation-only/blocks_tr.txt` from `export_blocks.py`. It must
not be able to read the English page, the English blocks file or the glossary.

Prompt:

> Translate each numbered block below into English, including text in quotation marks
> and brackets; only labels already in English stay as they are. Translate what the text
> says, not what you think the author meant. Keep the block numbers and tags exactly. Do not
> smooth, correct or complete anything. Where a sentence is ambiguous, give the most
> literal reading and add "[ambiguous: <other reading>]". Table cells are separated by
> ` | `; keep them in order.

Never give the back-translator the glossary or the modal mappings (for example "must not
= não devem"): that tells it the answer on exactly the point being checked.

## 2. Comparator

Give it `blocks_en.txt` and the back-translation. For every block, compare and report:

- numbers, units, comparators (at least, up to) and their order
- negation, and which word it sits on
- modal strength: must, must not, should, may, need not
- scope words: all, only, some, every, any, never
- qualifiers and hedges added or dropped (about, probably, just, only)
- swapped table labels or columns
- anything added that the English does not say, or said in the English and missing

Modal strength is settled by `scripts/languages/<lang>.json`, not by the comparator's
feel for the language (Portuguese "deve" is must; "deveria" is should).

Verdict per block: `MATCH`, or `MISMATCH` with the English, the back-translated text and the
reason. Wording differences that keep the meaning are a MATCH; do not report style.

## Record

One record per block per round, in a JSON list passed to the checker with
`--back-translation`:

    [{"block": 1, "round": 1, "verdict": "MATCH", "model": "<comparator model>", "sha256": "<from blocks_sha256.json>"},
     {"block": 1, "round": 2, "verdict": "MATCH", "model": "<comparator model>", "sha256": "<from blocks_sha256.json>"}]

`sha256` is copied from `<out-dir>/blocks_sha256.json`, which `export_blocks.py` writes
beside the block files. The page title and description get records too, with `"block":
"title"` and `"block": "description"`.

Each hash covers the English and the translated text of that block together. Edit either
page afterwards (humanising, outside feedback, a correction to the English) and the
checker blocks until that block is exported and back-translated again.

Round two runs after round one's fixes, on the whole page again, and re-checks those
fixes itself. The checker blocks any block without a round 2 MATCH from a model other
than `--translator-model`.
