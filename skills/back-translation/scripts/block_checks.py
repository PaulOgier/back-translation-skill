#!/usr/bin/env python3
"""
block_checks.py: the no-sidecar checks of verify_translation.py, run per aligned
block rather than per claim: block numbering (shared with export_blocks.py), the
block text hash a back-translation record is tied to, the modal and negation
comparison, and the back-translation records themselves. Standard library only
and Python 3.9 compatible, like the checker.
"""

from __future__ import annotations

import hashlib

from translation_lang import ATTR_RE, VERB_RE, modal_kinds, negator_positions, norm_space, plain

BT_PASS = "MATCH"
HEAD_UNITS = ("title", "description")  # back-translated alongside the blocks, under these names


def block_text(text: str) -> str:
    """A block's text as export_blocks.py writes it and as its hash is taken: markup
    characters removed, whitespace collapsed."""
    return norm_space(plain(text))


def block_sha256(en_text: str, tr_text: str) -> str:
    """sha256 of a block's normalised English and translated text, so a back-translation
    record is tied to both texts it was made for: editing either side voids it."""
    return hashlib.sha256((block_text(en_text) + "\n" + block_text(tr_text)).encode("utf-8")).hexdigest()


def negated(text: str, lang: dict) -> bool:
    """Whether text negates anything: a negator, or a negated modal such as can't or não pode.
    A language without negators (zh-Hans) is skipped by the caller, as it would always differ.
    shortcut: a yes/no per remainder, so a negated modal (prohibited, é proibido) hides a second
    negation in the same remainder; counting negators would break pt/es double negation, and the
    every-block back-translation covers it."""
    return bool(negator_positions(text, lang)) or any(k.endswith("-not") for k in modal_kinds(text, lang))


def same_model(a: str, b: str) -> bool:
    """Whether two model names are the same model, ignoring case and outer space."""
    return a.strip().casefold() == b.strip().casefold()


def aligned_blocks(en, tp) -> list[tuple] | None:
    """The non-empty block pairs numbered B1, B2, ... exactly as export_blocks.py
    numbers them (outside region then main), so a back-translation record's block
    number means the same block here. None when the block sequences differ, which
    check_tokens already blocks."""
    pairs = [p.region_blocks("outside") + p.region_blocks("main") for p in (en, tp)]
    if [b.tag for b in pairs[0]] != [b.tag for b in pairs[1]]:
        return None
    out = []
    for a, b in zip(*pairs):
        if block_text(a.text) or block_text(b.text):
            out.append((len(out) + 1, a, b))
    return out


def bt_units(en, tp) -> list[tuple] | None:
    """Every unit a back-translation record names, as (name, record key, English text,
    translated text): the aligned blocks (B1 keyed 1, ...) then the title and the
    description when either page has one. None when the block sequences differ."""
    pairs = aligned_blocks(en, tp)
    if pairs is None:
        return None
    units = [(f"B{n}", n, a.text, b.text) for n, a, b in pairs]
    for k in HEAD_UNITS:
        e, t = en.head.get(k, ""), tp.head.get(k, "")
        if block_text(e) or block_text(t):
            units.append((k, k, e, t))
    return units


def _prose(text: str) -> str:
    """Block text as modals read it: attribute text and verbatim spans dropped, since an
    English word inside <code> would read as an English modal on both sides."""
    return norm_space(VERB_RE.sub(" ", ATTR_RE.sub(" ", text)))


def _ability_cannot(ka: list, kb: list, ra: str, rb: str, lang: dict, en_lang: dict) -> bool:
    """Whether the only modal difference is an English may-not (can't, cannot) that the
    translation renders as a plain negation, as it does for inability ("can't remember",
    pt "não conseguem lembrar"). The translation needs one negator per may-not on top of
    one per English negator, so a dropped "not" keeps blocking even when another negation
    sits in the same block."""
    return bool(lang["negator_re"]) and "may-not" in ka and [k for k in ka if k != "may-not"] == kb \
        and len(negator_positions(rb, lang)) >= ka.count("may-not") + len(negator_positions(ra, en_lang))


def check_block_modals(ctx) -> list[tuple]:
    """No-sidecar mode: the per-claim modal and negation guarantee, run per aligned
    block. A different modal sequence blocks; a negation-only difference warns, as the
    every-block back-translation confirms it. Skipped while the block sequences differ,
    which check_tokens blocks."""
    out = []
    for n, a, b in aligned_blocks(ctx.en, ctx.tp) or []:
        where, ra, rb = f"B{n}", _prose(a.text), _prose(b.text)
        ka, kb = modal_kinds(ra, ctx.en_lang), modal_kinds(rb, ctx.lang)
        if ka != kb and _ability_cannot(ka, kb, ra, rb, ctx.lang, ctx.en_lang):
            out.append(("warning", "normative", None, f"{where}: English can't/cannot ({ka}) is a negation without a modal in the "
                        f"translation ({kb or 'none'}); fine for ability, confirm permission in the back-translation"))
        elif ka != kb:
            out.append(("blocker", "normative", None, f"{where}: modals {ka or 'none'} in English, {kb or 'none'} in the translation"))
        elif ctx.lang["negator_re"] and negated(rb, ctx.lang) != negated(ra, ctx.en_lang):
            out.append(("warning", "normative", None, f"{where}: negation differs; confirm in the back-translation"))
    return out


def check_block_back_translation(ctx) -> list[tuple]:
    """No-sidecar mode: every aligned block, the title and the description need their
    latest back-translation record to be round 2 or later, MATCH, by a model other than
    the translator, and made for the English and translated text as they stand (their
    sha256, any case). No records at all is a warning, as a page can be checked before
    its back-translation round."""
    if ctx.bt is None:
        return [("warning", "back-translation", None, "no back-translation recorded")]
    out = []
    if not (ctx.translator_model or "").strip():
        out.append(("blocker", "back-translation", None, "--translator-model is required with --back-translation"))
    units = bt_units(ctx.en, ctx.tp)
    if units is None:
        return out  # block numbers mean nothing until the structure matches
    latest = {}
    for r in ctx.bt:
        if r["round"] >= latest.get(r["block"], {}).get("round", 0):
            latest[r["block"]] = r
    for name, key, e, t in units:
        r = latest.get(key)
        if not r:
            out.append(("blocker", "back-translation", None, f"{name}: no back-translation recorded"))
        elif r["round"] < 2 or r["verdict"] != BT_PASS:
            out.append(("blocker", "back-translation", None, f"{name}: latest is round {r['round']} {r['verdict']}; needs round 2 MATCH"))
        elif same_model(r["model"], ctx.translator_model or ""):
            out.append(("blocker", "back-translation", None, f"{name}: back-translated by the same model that translated"))
        elif r["sha256"].casefold() != block_sha256(e, t):
            out.append(("blocker", "back-translation", None, f"{name} changed since it was back-translated; re-export and redo it"))
    blocks = sum(isinstance(k, int) for _, k, _, _ in units)
    for b in sorted(set(latest) - {k for _, k, _, _ in units}, key=lambda k: (isinstance(k, str), k)):
        where = f"B{b}, but the page has {blocks} blocks" if isinstance(b, int) else f"the {b}, but the pages have none"
        out.append(("blocker", "back-translation", None, f"record for {where}; re-export the blocks"))
    return out


def validate_back_translation(records) -> None:
    """Raise ValueError when no-sidecar back-translation records do not have the shape
    check_block_back_translation reads: a list of {block, round, verdict, model, sha256},
    block being a block number from 1 or "title" or "description"."""
    if not isinstance(records, list):
        raise ValueError("back-translation file must be a list of records")
    for r in records:
        block = r.get("block") if isinstance(r, dict) else None
        if not (((type(block) is int and block >= 1) or block in HEAD_UNITS)
                and type(r.get("round")) is int and isinstance(r.get("verdict"), str)
                and isinstance(r.get("model"), str) and r["model"].strip() and isinstance(r.get("sha256"), str)):
            raise ValueError("back-translation: each record needs block (an int from 1, \"title\" or \"description\"), "
                             "int round, and verdict, model and sha256 strings (sha256 from blocks_sha256.json)")
