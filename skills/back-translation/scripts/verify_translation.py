#!/usr/bin/env python3
"""
verify_translation.py: deterministic checks for a translated page against its
English source. Standard library only and Python 3.9 compatible, so it runs with
the system python. The language rules live in translation_lang.py and
languages/<lang>.json.

Two modes:
  sidecar mode     verify_translation.py <slug>.<lang>.translation.json [--root DIR]
                   The translation JSON names an English fact-check sidecar
                   (approval, body_sha256, claims). Every check below runs.
  no-sidecar mode  verify_translation.py --en EN --tr TR --lang CODE
                   [--back-translation RECORDS.json] [--translator-model NAME] [--root DIR]
                   Two pages only. The checks marked (sidecar) are skipped; the
                   modal and negation comparison runs per aligned block instead of
                   per claim, and the back-translation is checked per block
                   (numbered as export_blocks.py numbers them, each record tied
                   to its block's text by sha256). Without --back-translation it
                   warns once. The no-sidecar block checks live in block_checks.py.

It compares the English page with one translation and blocks on:
  - (sidecar) English changed since translation (stale)
  - a page without exactly one <main>, an untranslated page or title, or a page
    whose <html lang> is not the translation's language
  - numbers that differ block by block, in order, with their written form, unit
    (days, years, characters, %) and comparator (at least, up to, before), after
    locale separators, scale words (billion = Milliarde = mil millones = 10亿) and
    number words (three = três) are resolved; numbers of different kinds (5%,
    2021) in another order only warn
  - a date whose day, month or year differs
  - [n] citation markers that differ block by block
  - <code>, <pre>, <kbd> and <samp> text, links and media sources not carried over
    unchanged and in the same order (a localised file may add the page's own
    language folder just before its filename, and a #fragment link may change);
    PHP inside <main> that differs; ordered-list start numbers, <li value> and
    checkbox states that differ
  - a different sequence of paragraphs, list items, headings and table cells, or
    a different number of text runs outside block tags (a dropped <div> paragraph)
  - a <script>, <style> or <template> that is never closed, or an unclosed comment
    or attribute quote, as everything after it is hidden
  - title, meta, og/twitter and JSON-LD text whose numbers differ, a duplicated
    meta tag, or a changed og:image (same localised-file allowance)
  - (sidecar) a claim sentence missing, too short, not followed in its block by its
    [n] marker, or sharing its page sentence with text the English does not have
  - a claim (no-sidecar mode: a block) whose modal sequence (must, must not,
    should, may, need not, ...) differs, negation included (no-sidecar mode: a
    negation-only difference warns)
  - a forbidden ambiguous rendering (German "muss nicht", "soll")
  - a claim (no-sidecar mode: a block, the title and the description) without a
    passing round-2 back-translation by a different model whose sha256 still matches:
    sidecar mode the translated sentence and the English claim, no-sidecar mode the
    English and translated text together as export_blocks.py wrote them
  - data-i18n-skip on anything but <aside> or <nav>, skipped text longer than page
    furniture, or skipped text that carries a number or a modal
  - (sidecar) an English sidecar that is not approved, has pending corrections, or
    holds a claim that is not CONFIRMED or AS-REPORTED
  - <textarea>, <xmp> and the like, a <title> in the body, or a <base> or meta
    refresh that differs from English

Threat model: an honest translator (a model) making mistakes, not an adversary
crafting HTML to slip past the gate. Accepted limits are listed as known_gap cases
in tests/fixtures/translation_cases_round2.json and round3.json.

Exit codes: 0 clean, 1 blockers, 2 script or input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from block_checks import (  # noqa: E402
    BT_PASS, aligned_blocks, check_block_back_translation, check_block_modals, negated, same_model,
    validate_back_translation)
from markdown_page import markdown_to_html  # noqa: E402
from translation_lang import (  # noqa: E402
    ATTR_CLOSE, ATTR_OPEN, MARKER_RE, VERB_CLOSE, VERB_OPEN, MarkedBlock, alt_forms, extract_dates, fmt_tokens,
    for_matching, for_numbers, load_lang, match_norm, modal_kinds, nfc, norm_space, number_tokens,
    plain, same_numbers, scale_problems, sentence_sha256, sentences)

MAX_INPUT_BYTES = 5_000_000
MARKDOWN_SUFFIXES = {".md", ".mdx", ".markdown"}
MAX_SKIPPED_CHARS = 2_000
MAX_CLAIMS = 2_000
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "th", "dt", "dd",
              "caption", "figcaption", "blockquote", "pre"}
VERBATIM_TAGS = {"code", "pre", "kbd", "samp"}
INLINE_TAGS = {"a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em", "i", "kbd", "mark",
               "q", "s", "samp", "small", "span", "strong", "time", "u", "var", "wbr"}
SPACED_INLINE_TAGS = {"br", "img", "sub", "sup", "del", "ins", "label", "button"}  # inline, kept apart: 10<sup>12</sup>
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
             "track", "wbr"}
MEDIA_TAGS = {"img", "source", "iframe", "video", "audio", "embed", "track", "object"}
MEDIA_ATTRS = ("src", "srcset", "poster", "data")
SKIP_ALLOWED = {"aside", "nav"}
HEAD_META = {"description", "og:title", "og:description", "og:image:alt", "twitter:title",
             "twitter:description", "twitter:image:alt", "x-front-matter"}  # x-front-matter: markdown_page.py
EXACT_META = {"og:image", "twitter:image"}
MEDIA_EXT = "jpe?g|png|webp|avif|gif|svg|mp4|webm"
UNSUPPORTED_TAGS = {"textarea", "xmp", "plaintext", "noembed", "noframes"}  # parsers disagree on their content
PRIVATE_CHARS = {**dict.fromkeys(map(ord, ATTR_OPEN + ATTR_CLOSE + VERB_OPEN + VERB_CLOSE), " "),
                 0xAD: None}  # the checker's own markers become spaces; a soft hyphen (&shy;) goes, as a reader sees it
MAX_SENTENCE_CHARS = 5_000
OK_VERDICTS = {"CONFIRMED", "AS-REPORTED"}
TEXT_ATTRS = ("alt", "title", "aria-label", "placeholder", "value")
CODE_BRACKET = "⟦"  # stands in for "[" inside code, so $a[1] is never read as a claim marker


def _list_number(v: str) -> str:
    """An <ol start> or <li value> as the number a browser shows, so 01 reads as 1."""
    v = v.strip()
    return str(int(v)) if v.isdigit() else v


class Block:
    """One block of text (a paragraph, list item, cell, heading) in one region of the page."""

    def __init__(self, region: str, tag: str):
        self.region, self.tag, self.parts = region, tag, []

    @property
    def text(self) -> str:
        return "".join(self.parts)


class PageText(HTMLParser):
    """Split a page into blocks per region (main, outside main), plus its head
    text, JSON-LD text, verbatim spans, links and media sources in <main>.

    Elements carrying data-i18n-skip (the translation notice, the switcher) are
    left out of the blocks on both sides but kept as skipped text, which must stay
    short and carry no numbers or modals. Only <aside> and <nav> may carry it.
    Attribute text (alt, title, aria-label) is kept, wrapped so claim matching can
    leave it out: an infographic's alt text repeats the article's figures."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[dict] = []
        self.open = Counter()
        self.mains = self.in_main = self.in_head = 0
        self.seen_body = False
        self.skip_depth = self.script_depth = 0
        self.skipped: list[str] = []
        self.skipped_chars = 0
        self.skip_violations: list[str] = []
        self.html_lang: str | None = None
        self.blocks: list[Block] = []
        self.block_stack: list[Block] = []
        self.loose = {"main": Block("main", "#loose"), "outside": Block("outside", "#loose")}
        self.head: dict[str, str] = {}
        self.meta_counts = Counter()
        self.title_buf: list[str] | None = None
        self.ld_buf: list[str] | None = None
        self.ld_text: list[tuple[str, str]] = []
        self.ld_errors = 0
        self.verbatim: list[str] = []
        self.vparts: list[str] = []
        self.vstarts: list[int] = []
        self.hrefs: list[str] = []
        self.srcs: list[str] = []
        self.php_main: list[str] = []
        self.unsupported: list[str] = []
        self.redirects: list[str] = []
        self.unclosed_raw: list[str] = []
        self.list_marks: list[str] = []
        self.loose_runs = Counter()
        self.loose_break = True
        self.unclosed_markup = False

    @property
    def region(self) -> str:
        return "main" if self.in_main else "outside"

    def _emit(self, s: str):
        if self.in_head or self.skip_depth:
            return
        if self.block_stack:
            self.block_stack[-1].parts.append(s)
            return
        if self.loose_break and s.strip():  # a new run of loose text, so a dropped <div> paragraph shows
            self.loose_runs[self.region] += 1
            self.loose_break = False
        self.loose[self.region].parts.append(s)

    def _break(self):
        self._emit(" ")
        self.loose_break = True

    def _skipped(self, s: str):
        self.skipped_chars += len(s.strip())
        if self.skipped_chars <= 2 * MAX_SKIPPED_CHARS:
            self.skipped.append(s)

    def handle_starttag(self, tag, attrs):
        a = {}
        for k, v in attrs:
            a.setdefault(k, (v or "").translate(PRIVATE_CHARS))  # browsers keep the first of a duplicated attribute
        if tag in UNSUPPORTED_TAGS or (tag == "title" and not self.in_head):
            self.unsupported.append(tag)
        if tag == "base" or (tag == "meta" and a.get("http-equiv", "").lower() == "refresh"):
            self.redirects.append(f"{tag} {a.get('href', '')}{a.get('content', '')}")
        if tag == "html" and self.html_lang is None:
            self.html_lang = a.get("lang")
        if tag in ("body", "main") or tag in BLOCK_TAGS:
            self.seen_body = True
            self.in_head = 0  # </head> is optional
        if tag == "head" and not self.seen_body:
            self.in_head += 1
        if tag == "meta" and self.in_head:
            name = (a.get("name") or a.get("property") or "").lower()
            if name in HEAD_META | EXACT_META | {"og:url"}:
                self.meta_counts[name] += 1
                self.head.setdefault(name, a.get("content", ""))
        script = tag in ("script", "style", "template")
        skip = script
        if "data-i18n-skip" in a:
            skip = True
            if tag not in SKIP_ALLOWED:
                self.skip_violations.append(tag)
        if tag in SPACED_INLINE_TAGS:
            self._emit(" ")
        elif tag not in INLINE_TAGS:
            self._break()
        if tag in VOID_TAGS:
            self._attrs(tag, a)
            return
        entry = {"tag": tag, "skip": skip, "script": script, "block": None, "verbatim": False,
                 "title": False, "ld": False}
        self.stack.append(entry)
        self.open[tag] += 1
        self.skip_depth += skip
        self.script_depth += script
        if tag == "main":
            self.mains += 1
            self.in_main += 1
        if tag == "title" and self.in_head:
            self.title_buf, entry["title"] = [], True
        if tag == "script" and a.get("type", "").strip().lower().startswith("application/ld+json"):
            self.ld_buf, entry["ld"] = [], True
        self._attrs(tag, a)
        if self.in_head or self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            b = Block(self.region, tag)
            self.blocks.append(b)
            self.block_stack.append(b)
            entry["block"] = b
        if tag in VERBATIM_TAGS and not self.vstarts:  # only the outermost span: <pre><code> is one
            self.vstarts.append(len(self.vparts))
            entry["verbatim"] = True

    def _attrs(self, tag, a):
        if self.in_head:
            return
        texts = [a[k] for k in TEXT_ATTRS if a.get(k) and not (tag == "li" and k == "value")]  # a list number
        if self.skip_depth:
            if not self.script_depth:
                for t in texts:
                    self._skipped(" " + t + " ")
            return
        if self.in_main and tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        if self.in_main and tag in MEDIA_TAGS:
            self.srcs += [f"{k}={a[k]}" for k in MEDIA_ATTRS if k in a]
        if tag == "ol" and _list_number(a.get("start", "1")) != "1":
            self.list_marks.append(f"ol start={_list_number(a['start'])}")
        if tag == "li" and "value" in a:
            self.list_marks.append(f"li value={_list_number(a['value'])}")
        if tag == "input" and a.get("type", "").lower() == "checkbox":
            self.list_marks.append("checkbox " + ("checked" if "checked" in a else "unchecked"))
        for t in texts:
            self._emit(f" {ATTR_OPEN}{t}{ATTR_CLOSE} ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or not self.open[tag]:
            return  # a stray end tag closes nothing
        while self.stack:
            e = self.stack.pop()
            self.open[e["tag"]] -= 1
            if e["script"] and e["tag"] != tag:
                self.unclosed_raw.append(e["tag"])
            self._close(e)
            if e["tag"] == tag:
                break
        if tag in SPACED_INLINE_TAGS:
            self._emit(" ")
        elif tag not in INLINE_TAGS:
            self._break()

    def _close(self, e):
        if e["block"] is not None:
            self.block_stack.pop()  # blocks open and close in element order, so it is always the last
        if e["verbatim"]:
            start = self.vstarts.pop()
            self.verbatim.append(norm_space("".join(self.vparts[start:])))
            if not self.vstarts:
                self.vparts.clear()
        if e["title"]:
            self.meta_counts["title"] += 1
            self.head.setdefault("title", "".join(self.title_buf or []))
            self.title_buf = None
        if e["ld"]:
            self._read_ld("".join(self.ld_buf or []))
            self.ld_buf = None
        self.skip_depth -= e["skip"]
        self.script_depth -= e["script"]
        if e["tag"] == "main":
            self.in_main -= 1
        if e["tag"] == "head" and self.in_head:
            self.in_head -= 1

    def _read_ld(self, src: str):
        """Collect every text value of a JSON-LD block in order, leaving out @ keys,
        dates (a translation has its own) and URLs."""
        try:
            data = json.loads(src)
        except ValueError:
            self.ld_errors += 1
            return
        todo = [("", data)]
        while todo:
            key, x = todo.pop()
            if isinstance(x, str):
                if not re.match(r"https?://", x):
                    self.ld_text.append((key, x))
            elif isinstance(x, dict):
                todo.extend((k, x[k]) for k in sorted(x, reverse=True)
                            if k == "@value" or not (k.startswith("@") or k.lower().startswith("date")))
            elif isinstance(x, list):
                todo.extend((key, v) for v in reversed(x))

    def handle_data(self, data):
        data = data.translate(PRIVATE_CHARS)
        if self.title_buf is not None:
            self.title_buf.append(data)
        if self.ld_buf is not None:
            self.ld_buf.append(data)
        if self.skip_depth:
            if not self.script_depth:
                self._skipped(data)
            return
        if self.in_head:
            return
        if self.vstarts:
            self.vparts.append(data)
            data = VERB_OPEN + data.replace("[", CODE_BRACKET) + VERB_CLOSE
        self._emit(data)

    def close(self):
        super().close()
        while self.stack:
            e = self.stack.pop()
            self.open[e["tag"]] -= 1
            if e["script"]:
                self.unclosed_raw.append(e["tag"])
            self._close(e)

    def region_blocks(self, region: str) -> list[Block]:
        """Blocks of one region in document order, its loose text last."""
        return [b for b in self.blocks if b.region == region] + [self.loose[region]]

    def region_text(self, region: str) -> str:
        """All text of one region, blocks separated by newlines, markup characters removed."""
        return plain("\n".join(b.text for b in self.region_blocks(region)))


def strip_php(src: str) -> tuple[str, list[tuple[int, str]]]:
    """Remove PHP blocks in one linear pass; an unclosed block runs to the end, as in
    PHP. Returns the text and each block as (offset in the source, code)."""
    out, blocks, i = [], [], 0
    opener = re.compile(r"<\?(?:php\b|=)", re.I)
    while True:
        m = opener.search(src, i)
        if not m:
            out.append(src[i:])
            return "".join(out), blocks
        out.append(src[i:m.start()])
        j = src.find("?>", m.end())
        blocks.append((m.start(), norm_space(src[m.start():(j + 2 if j >= 0 else len(src))])))
        if j < 0:
            return "".join(out), blocks
        i = j + 2


def parse_page(html: str) -> PageText:
    """Parse a page after NFC normalisation, with PHP blocks stripped so they never
    read as text. The PHP blocks inside <main> are kept on the result as php_main."""
    src = nfc(html)
    if src.startswith("\ufeff"):
        src = src[1:]
    text, php = strip_php(src)
    # Where html.parser and a browser disagree, rewrite to what the browser does: <!--> and <!---> end at once,
    # --!> ends a comment, <![CDATA[ in HTML is text up to the first >, and </script x> is </script>.
    text = re.sub(r"<!---?>", "<!---->", text).replace("--!>", "-->").replace("<![CDATA[", "&lt;![CDATA[")
    text = re.sub(r"</(script|style)\b[^>]*>", r"</\1>", text, flags=re.I)
    p = PageText()
    p.feed(text)
    # html.parser holds back an unclosed comment, tag or attribute quote: newer versions then drop the rest
    p.unclosed_markup = p.rawdata.startswith("<")
    p.close()
    m, end = re.search(r"<main\b", src, re.I), src.lower().rfind("</main")
    p.php_main = [code for pos, code in php if m and m.start() < pos < end]
    return p


def _block_facts(text: str, lang: dict, en: dict | None) -> tuple[list, list, list, list]:
    """(dates, number tokens, scale problems, markers) of one block."""
    markers = MARKER_RE.findall(text)
    dates, rest = extract_dates(norm_space(MARKER_RE.sub(" ", for_numbers(text))), lang)
    return dates, number_tokens(rest, lang, en), scale_problems(rest, lang), markers


def _by_kind(tokens: list) -> list:
    """Number tokens grouped by unit and comparator, each group in its own order, so a
    clause moved past a number of another kind (5% in 2021, em 2021 ... 5%) compares
    equal while two numbers of one kind swapped (8 and 12 characters) still differ."""
    return sorted(tokens, key=lambda t: (str(t[2]), str(t[3])))


def _compare_blocks(ctx, region: str, a: list[tuple[str, str]], b: list[tuple[str, str]]) -> list[tuple]:
    """Pair (tag, text) blocks one to one and compare their dates, numbers and markers
    in order. When the block sequences differ, that blocks, and the region is compared as one text."""
    out = []
    if [t for t, _ in a] != [t for t, _ in b]:
        ta, tb = Counter(t for t, _ in a), Counter(t for t, _ in b)
        diff = ", ".join(f"<{t}> {ta[t]} vs {tb[t]}" for t in sorted(set(ta) | set(tb)) if ta[t] != tb[t])
        out.append(("blocker", "structure", None, f"{region}: blocks differ from English ({diff or 'same counts, different order'})"))
        a, b = [("#all", "\n".join(x for _, x in a))], [("#all", "\n".join(x for _, x in b))]
    for n, ((_, x), (tag, y)) in enumerate(zip(a, b), 1):
        da, na, _, ma = _block_facts(x, ctx.en_lang, None)
        db, nb, pb, mb = _block_facts(y, ctx.lang, ctx.en_lang)
        where = f"{region} <{tag}> {n}"
        out += [("blocker", "numbers", None, f"{where}: {p}") for p in pb]
        if same_numbers(na, nb):
            pass
        elif same_numbers(_by_kind(na), _by_kind(nb)):
            out.append(("warning", "numbers", None,
                        f"{where}: same numbers in a different order ({fmt_tokens(na)}; translation {fmt_tokens(nb)}); confirm the reordering"))
        else:
            out.append(("blocker", "numbers", None,
                        f"{where}: English has {fmt_tokens(na) or 'none'}; translation has {fmt_tokens(nb) or 'none'}"))
        if da != db:
            out.append(("blocker", "dates", None, f"{where}: dates {da} in English, {db} in the translation"))
        if ma != mb:
            out.append(("blocker", "claims", None, f"{where}: citation markers {ma} in English, {mb} in the translation"))
    return out


def check_page(ctx) -> list[tuple]:
    """Whole-page rules: one <main> each, translated, the right <html lang>, one of
    each meta tag, readable JSON-LD, the same PHP in <main>, and allowed skips."""
    out = []
    for name, p in (("English", ctx.en), ("translation", ctx.tp)):
        if p.mains != 1:
            out.append(("blocker", "page", None, f"{name} page has {p.mains} <main> elements; exactly one is required"))
        for k, n in sorted(p.meta_counts.items()):
            if n > 1:
                out.append(("blocker", "page", None, f"{name} page has {n} {k} tags; one is allowed"))
        if p.ld_errors:
            out.append(("blocker", "page", None, f"{name} page has JSON-LD that does not parse"))
        for t in p.skip_violations:
            out.append(("blocker", "skip", None, f"{name}: data-i18n-skip on <{t}>; only <aside> and <nav> may be skipped"))
        if p.skipped_chars > MAX_SKIPPED_CHARS:
            out.append(("blocker", "skip", None, f"{name}: {p.skipped_chars} characters are skipped; only the notice and switcher may be"))
        if p.unclosed_markup:
            out.append(("blocker", "page", None, f"{name}: unclosed comment or tag; everything after it is unchecked"))
        for t in sorted(set(p.unclosed_raw)):
            out.append(("blocker", "page", None, f"{name}: <{t}> never closed; everything after it is unchecked"))
    skipped = norm_space(" ".join(ctx.tp.skipped))
    _, rest = extract_dates(skipped, ctx.lang)
    if number_tokens(rest, ctx.lang, ctx.en_lang) or modal_kinds(rest, ctx.lang):
        out.append(("blocker", "skip", None, "skipped text carries a number or a modal; only page furniture may be skipped"))
    if ctx.lang["lang"].casefold() == "en":
        out.append(("blocker", "page", None, "translation language is the source language"))
    if norm_space(ctx.en.region_text("main")) == norm_space(ctx.tp.region_text("main")):
        out.append(("blocker", "page", None, "translated <main> text is identical to the English"))
    for k in ("title", "description"):
        if ctx.en.head.get(k) and norm_space(ctx.en.head[k]) == norm_space(ctx.tp.head.get(k, "")):
            out.append(("blocker", "page", None, f"{k} is not translated"))
    code, page_lang = ctx.lang["lang"].casefold(), (ctx.tp.html_lang or "").casefold()
    tags = [t.casefold() for t in ctx.lang.get("lang_tags") or [code]]
    # a tag shorter than the code (zh for zh-Hans) matches only exactly, so zh-TW is not read as zh-Hans
    if not any(page_lang == t or (page_lang.startswith(t + "-") and not code.startswith(t + "-")) for t in tags):
        out.append(("blocker", "page", None, f"<html lang=\"{ctx.tp.html_lang}\"> does not match translation language {ctx.lang['lang']}"))
    if ctx.tp.unsupported:
        out.append(("blocker", "page", None, f"translation uses {sorted(set(ctx.tp.unsupported))}, whose content parsers read differently"))
    if ctx.en.redirects != ctx.tp.redirects:
        out.append(("blocker", "page", None, "a <base> or meta refresh differs from English"))
    if ctx.en.php_main != ctx.tp.php_main:
        out.append(("blocker", "tokens", None, "PHP inside <main> differs from English"))
    return out


def check_sidecar(ctx) -> list[tuple]:
    """Sidecar mode: translate only approved English with no pending corrections and
    every claim CONFIRMED or AS-REPORTED."""
    art, out = ctx.en_sc["article"], []
    if "approval" in art and art["approval"] != "approved":
        out.append(("blocker", "page", None, f"English sidecar approval is {art['approval']!r}; translate only approved English"))
    if art.get("pending_corrections"):
        out.append(("blocker", "page", None, "English sidecar has pending corrections; apply them to the English first"))
    for c in ctx.en_sc["claims"]:
        if "verdict" in c and c["verdict"] not in OK_VERDICTS:
            out.append(("blocker", "page", c["id"], f"English claim verdict is {c['verdict']}; only CONFIRMED or AS-REPORTED claims are translated"))
    return out


def check_stale(ctx) -> list[tuple]:
    """The translation must come from the English version that was last verified."""
    want = ctx.en_sc["article"].get("body_sha256")
    out = []
    if ctx.tr["source_body_sha256"] != want:
        out.append(("blocker", "stale", None, "translation was made from a different English version than the sidecar records"))
    if hashlib.sha256(ctx.en_text.encode("utf-8")).hexdigest() != want:
        out.append(("blocker", "stale", None, "English article changed since its own verification; re-verify the English first"))
    return out


def _host(url: str) -> str:
    """The lower-cased host of an absolute or protocol-relative URL, or ''."""
    m = re.match(r"(?:https?:)?//([^/?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def _same_list(what: str, a: list[str], b: list[str], also_ok=None) -> list[tuple]:
    """One blocker when two ordered lists differ; also_ok(i) may accept a differing item."""
    if len(a) == len(b) and all(x == y or (also_ok and also_ok(i)) for i, (x, y) in enumerate(zip(a, b))):
        return []
    sa, sb = set(a), set(b)
    missing, extra = [x for x in a if x not in sb][:3], [x for x in b if x not in sa][:3]
    detail = f"missing {missing}, added {extra}" if missing or extra else "same items in a different order"
    return [("blocker", "tokens", None, f"{what} differ from English: {detail}")]


def _front_matter_values(content: str) -> dict[str, str]:
    """The x-front-matter meta as key to text: markdown_page.py writes a JSON object;
    any other content is one value under the key ""."""
    try:
        d = json.loads(content)
    except ValueError:
        d = None
    if isinstance(d, dict) and all(isinstance(v, str) for v in d.values()):
        return d
    return {"": content} if content else {}


def check_tokens(ctx) -> list[tuple]:
    """Numbers, dates and markers block by block in <main>, outside it, in the head
    and in JSON-LD; verbatim spans, links and media sources in order."""
    out = []
    for region in ("main", "outside"):
        pairs = [[(x.tag, x.text) for x in p.region_blocks(region)] for p in (ctx.en, ctx.tp)]
        out += _compare_blocks(ctx, region, *pairs)
        ra, rb = ctx.en.loose_runs[region], ctx.tp.loose_runs[region]
        # outside <main> is template chrome, which a translated template often renders from code
        if region == "main" and ra != rb:
            out.append(("blocker", "structure", None, f"{region}: {ra} runs of text outside block tags in English, {rb} in the translation"))
    for k in sorted({"title", *HEAD_META} - {"x-front-matter"}):
        out += _compare_blocks(ctx, "head", [(k, ctx.en.head.get(k, ""))], [(k, ctx.tp.head.get(k, ""))])
    fa, fb = (_front_matter_values(p.head.get("x-front-matter", "")) for p in (ctx.en, ctx.tp))
    for k in sorted(fa):  # every English prose key; a translation-only key is bookkeeping
        out += _compare_blocks(ctx, "head", [(f"x-front-matter {k}".strip(), fa[k])], [(f"x-front-matter {k}".strip(), fb.get(k, ""))])
    out += _compare_blocks(ctx, "json-ld", ctx.en.ld_text, ctx.tp.ld_text)
    site = {_host(ctx.en.head.get(k, "")) for k in ("og:url", *EXACT_META)} | {_host(ctx.tp.head.get("og:url", ""))}
    site -= {""}
    code = re.escape(ctx.lang["lang"])
    prefix = re.compile(rf"^((?:https?://[^/?#]+)?/?){code}(?:/|$)", re.I)
    # the page's own folder (case as written), after at least one path segment, before a media file: /img/pt/02-og.jpg
    asset = re.compile(rf"(?<=[^/])/{code}/([^/]+\.(?i:{MEDIA_EXT}))$")

    def ours(u: str) -> bool:
        return not _host(u) or _host(u) in site  # another site's /pt/ path is part of its address

    def unasset_url(u: str) -> str:
        if not ours(u):
            return u
        host, path, rest = re.match(r"((?:https?:)?//[^/?#]+)?([^?#]*)(.*)", u, re.I | re.S).groups()
        return (host or "") + asset.sub(r"/\1", path, count=1) + rest

    def unasset(h: str) -> str:
        return re.sub(r"[^\s,]+", lambda m: unasset_url(m.group()), h)  # each srcset candidate on its own

    def localised(h: str) -> str:
        p = prefix.sub(r"\1", h, count=1) if ours(h) else h
        return p if p != h else unasset(h)  # a page prefix or an asset folder, never both

    def same_src(e: str, t: str) -> bool:
        """Media is "attr=url"; only the asset folder is allowed, never a /pt/ page prefix (/pt/01.jpg blocks)."""
        (ek, _, ev), (tk, _, tv) = e.partition("="), t.partition("=")
        return ek == tk and unasset(tv) == ev

    def pairwise(en: list[str], tp: list[str], ok) -> callable:
        return lambda i: tp[i] == en[i] or ok(en[i], tp[i])

    for k in sorted(EXACT_META):
        en_v, tp_v = ctx.en.head.get(k, ""), ctx.tp.head.get(k, "")
        if tp_v != en_v and unasset(tp_v) != en_v:
            out.append(("blocker", "tokens", None, f"{k} differs from English"))
    en, tp = ctx.en, ctx.tp
    out += _same_list("verbatim text", en.verbatim, tp.verbatim)
    out += _same_list("links", en.hrefs, tp.hrefs, pairwise(
        en.hrefs, tp.hrefs, lambda e, t: localised(t) == e or (e[:1] == t[:1] == "#")))  # a fragment id may be translated
    out += _same_list("list numbers and checkboxes", en.list_marks, tp.list_marks)
    out += _same_list("media sources", en.srcs, tp.srcs, pairwise(en.srcs, tp.srcs, same_src))
    return out


def check_structure(ctx) -> list[tuple]:
    """The sentence count may drift a little, so that only warns (blocks are compared in check_tokens)."""
    n_en = len(sentences(ctx.en.region_text("main"), ctx.en_lang))
    n_tr = len(sentences(ctx.tp.region_text("main"), ctx.lang))
    if abs(n_en - n_tr) > max(2, n_en // 20):
        return [("warning", "structure", None, f"{n_en} sentences in English, {n_tr} in the translation")]
    return []


def check_forbidden(ctx) -> list[tuple]:
    """Ambiguous renderings the language file bans outright, anywhere in <main>."""
    text = " | ".join(norm_space(plain(b.text)) for b in ctx.tp.region_blocks("main"))
    if ctx.lang["negation_skip_re"]:
        text = ctx.lang["negation_skip_re"].sub(" ", text)  # müssen nicht nur is not müssen nicht
    return [("blocker", "normative", None, f"forbidden rendering '{f['form']}': {f['why']}")
            for f in ctx.lang.get("forbidden", []) if alt_forms([f["form"]], ctx.lang).search(text)]


def _sentence_remainders(blocks: list[MarkedBlock], claims: dict[int, str]) -> dict[int, str]:
    """For each claim id, the text of its page sentence that no claim covers."""
    out = {}
    for b in blocks:
        done: dict[tuple[int, int], str] = {}
        for cid in b.first:
            span = b.sentence_of(cid)
            if span and cid not in out:
                if span not in done:
                    done[span] = b.remainder(span, claims)  # one sentence can carry many markers
                out[cid] = done[span]
    return out


def check_claims(ctx) -> list[tuple]:
    """Each English claim: translated sentence long enough and present in a <main>
    block that carries its marker, no text added beside it in its page sentence,
    same modal sequence, same dates, and a round-2 MATCH back-translation by a
    different model of the translated sentence and English claim as they stand."""
    out = []
    if not ctx.en_sc["claims"]:
        out.append(("blocker", "claims", None, "English sidecar has no claims"))
    counts = Counter(c["id"] for c in ctx.tr["claims"])
    out += [("blocker", "claims", d, "claim id appears more than once in the translation sidecar")
            for d in sorted(i for i, n in counts.items() if n > 1)]
    by_id = {c["id"]: c["sentence"] for c in ctx.tr["claims"]}
    en_by_id = {c["id"]: c["claim"] for c in ctx.en_sc["claims"]}
    latest = {}
    for r in ctx.tr.get("back_translation", []):
        if r["round"] >= latest.get(r["id"], {}).get("round", 0):
            latest[r["id"]] = r
    tr_blocks = [MarkedBlock(b.text) for b in ctx.tp.region_blocks("main")]
    en_blocks = [MarkedBlock(b.text) for b in ctx.en.region_blocks("main")]
    en_rest, tr_rest = _sentence_remainders(en_blocks, en_by_id), _sentence_remainders(tr_blocks, by_id)
    by_marker: dict[int, list[MarkedBlock]] = {}
    for b in tr_blocks:
        for mid in {mid for _, mid in b.marks}:
            by_marker.setdefault(mid, []).append(b)
    all_flat = "\x00".join(b.flat for b in tr_blocks)
    ratio = ctx.lang.get("min_length_ratio", 0.5)
    for c in ctx.en_sc["claims"]:
        cid, t = c["id"], by_id.get(c["id"])
        if t is None:
            out.append(("blocker", "claims", cid, "claim has no translated sentence in the translation sidecar"))
            continue
        if len(match_norm(t)) < max(6, ratio * len(match_norm(c["claim"]))):
            out.append(("blocker", "claims", cid, "translated sentence is too short to be the claim"))
        if not any(b.binds(t, cid) for b in by_marker.get(cid, [])):
            if match_norm(t) in all_flat:
                out.append(("blocker", "claims", cid, f"marker [{cid}] does not follow the translated sentence in its block"))
            else:
                out.append(("blocker", "claims", cid, "translated claim sentence not found in a <main> block"))
        ra, rb = en_rest.get(cid, ""), tr_rest.get(cid, "")
        if rb and not ra:
            out.append(("blocker", "claims", cid, f"its page sentence has text the English does not: '{rb[:80]}'"))
        elif rb and (modal_kinds(ra, ctx.en_lang) != modal_kinds(rb, ctx.lang)
                     or (ctx.lang["negator_re"] and negated(rb, ctx.lang) != negated(ra, ctx.en_lang))):
            out.append(("blocker", "normative", cid, "the rest of its page sentence has different modals or negation from the English"))
        ka, kb = modal_kinds(c["claim"], ctx.en_lang), modal_kinds(t, ctx.lang)
        if ka != kb:
            out.append(("blocker", "normative", cid, f"modals {ka or 'none'} in English, {kb or 'none'} in the translation"))
        da, _ = extract_dates(norm_space(c["claim"]), ctx.en_lang)
        db, _ = extract_dates(norm_space(nfc(t)), ctx.lang)
        if da != db:
            out.append(("blocker", "dates", cid, f"dates {da} in English, {db} in the translation"))
        r = latest.get(cid)
        if not r:
            out.append(("blocker", "back-translation", cid, "no back-translation recorded"))
        elif r["round"] < 2 or r["verdict"] != BT_PASS:
            out.append(("blocker", "back-translation", cid, f"latest is round {r['round']} {r['verdict']}; needs round 2 MATCH"))
        elif same_model(r["model"], ctx.tr["translator_model"]):
            out.append(("blocker", "back-translation", cid, "back-translated by the same model that translated"))
        elif r.get("sentence_sha256") != sentence_sha256(t):
            out.append(("blocker", "back-translation", cid, "back-translation was made for a different sentence; redo it"))
        elif r.get("claim_sha256") != sentence_sha256(c["claim"]):
            out.append(("blocker", "back-translation", cid, "back-translation was made against a different English claim; redo it"))
    page_markers = {mid for b in tr_blocks for _, mid in b.marks}
    for m in sorted(page_markers - set(en_by_id)):
        out.append(("blocker", "claims", None, f"marker [{m}] in the translation has no English claim"))
    return out


CHECKS = (check_page, check_sidecar, check_stale, check_tokens, check_structure, check_forbidden, check_claims)
PAGE_CHECKS = (check_page, check_tokens, check_structure, check_forbidden, check_block_modals,
               check_block_back_translation)


def validate(en_sc: dict, tr: dict) -> None:
    """Raise ValueError when either sidecar does not have the shape the checks read."""
    def need(cond, what):
        if not cond:
            raise ValueError(what)
    need(isinstance(en_sc, dict) and isinstance(en_sc.get("article"), dict), "English sidecar: article must be an object")
    need(isinstance(en_sc.get("claims"), list), "English sidecar: claims must be a list")
    need(len(en_sc["claims"]) <= MAX_CLAIMS and len(tr.get("claims") or []) <= MAX_CLAIMS,
         f"more than {MAX_CLAIMS} claims; no article has that many")
    for c in en_sc["claims"]:
        need(isinstance(c, dict) and type(c.get("id")) is int and isinstance(c.get("claim"), str)
             and len(c["claim"]) <= MAX_SENTENCE_CHARS,
             f"English sidecar: each claim needs an int id and a claim string of at most {MAX_SENTENCE_CHARS} characters")
    need(isinstance(tr, dict), "translation sidecar must be an object")
    for k in ("lang", "source_body_sha256", "translator_model"):
        need(isinstance(tr.get(k), str) and tr[k].strip(), f"translation sidecar: {k} must be a non-empty string")
    need(isinstance(tr.get("claims"), list), "translation sidecar: claims must be a list")
    for c in tr["claims"]:
        need(isinstance(c, dict) and type(c.get("id")) is int and isinstance(c.get("sentence"), str)
             and len(c["sentence"]) <= MAX_SENTENCE_CHARS,
             f"translation sidecar: each claim needs an int id and a sentence string of at most {MAX_SENTENCE_CHARS} characters")
    need(isinstance(tr.get("back_translation", []), list), "translation sidecar: back_translation must be a list")
    for r in tr.get("back_translation", []):
        need(isinstance(r, dict) and type(r.get("id")) is int and type(r.get("round")) is int
             and isinstance(r.get("verdict"), str) and isinstance(r.get("model"), str) and r["model"].strip(),
             "translation sidecar: each back_translation record needs int id and round, verdict and model strings")


class Context:
    """Everything a check reads: both sidecars (None in no-sidecar mode), both parsed
    pages, both language files, and in no-sidecar mode the back-translation records."""

    def __init__(self, en_sc: dict | None, en_text: str, tr: dict | None, tr_html: str, lang: dict,
                 bt: list | None = None, translator_model: str | None = None, en_html: str | None = None):
        self.en_sc, self.en_text, self.tr, self.lang = en_sc, en_text, tr, lang
        self.bt, self.translator_model = bt, translator_model
        self.en_lang = load_lang("en")
        self.en, self.tp = parse_page(en_text if en_html is None else en_html), parse_page(tr_html)


def check(en_sc: dict, en_text: str, tr: dict, tr_html: str, lang: dict, en_html: str | None = None) -> list[dict]:
    """Run every check; return findings as dicts with level, check, claim_id and message.

    en_text is the English file as text, read the way verify_sources.py reads it
    (universal newlines), so its hash matches the sidecar's body_sha256. en_html is
    the page the checks parse when it differs from the file (a Markdown source)."""
    validate(en_sc, tr)
    ctx = Context(en_sc, en_text, tr, tr_html, lang, en_html=en_html)
    keys = ("level", "check", "claim_id", "message")
    return [dict(zip(keys, f)) for chk in CHECKS for f in chk(ctx)]


def check_pages(en_text: str, tr_html: str, lang: dict, bt: list | None = None,
                translator_model: str | None = None) -> list[dict]:
    """No-sidecar mode: run every page-level check on two pages; return findings as
    check() does. bt is the back-translation record list, or None if none was made."""
    if bt is not None:
        validate_back_translation(bt)
    ctx = Context(None, en_text, None, tr_html, lang, bt, translator_model)
    keys = ("level", "check", "claim_id", "message")
    return [dict(zip(keys, f)) for chk in PAGE_CHECKS for f in chk(ctx)]


def read_page(path: Path, default_lang: str | None = None) -> str:
    """Read a page as the HTML text the checks parse. The one place page text is read,
    so Markdown (.md, .mdx, .markdown) is converted here and everything else is kept.
    default_lang fills a Markdown page's language when its front matter has none."""
    return page_html(path, read_capped(path), default_lang)


def page_html(path: Path, text: str, default_lang: str | None = None) -> str:
    """The HTML the checks parse for a file's text: converted from Markdown by its
    suffix, else the text itself. Split from read_page so sidecar mode can hash the
    raw text and parse the converted page from one read."""
    if path.suffix.lower() in MARKDOWN_SUFFIXES:
        return markdown_to_html(text, default_lang=default_lang)
    return text


def read_capped(path: Path) -> str:
    """Read a regular file once, as text with universal newlines like Path.read_text.

    Refuses anything that is not a regular file (a FIFO would hang or stream
    without end), a symlink swapped in after the path was resolved, and anything
    over MAX_INPUT_BYTES, checked on the open file itself."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{path} is not a regular file")
        with os.fdopen(fd, "rb") as f:
            fd = -1
            data = f.read(MAX_INPUT_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f"{path} is larger than {MAX_INPUT_BYTES} bytes")
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _inside(root: Path, rel: str) -> Path:
    """Resolve a sidecar path under root; refuse one that escapes it."""
    if not isinstance(rel, str):
        raise ValueError(f"path {rel!r} must be a string")
    p = (root / rel).resolve()
    if not p.is_relative_to(root):
        raise ValueError(f"{rel} is outside {root}")
    return p


def report(findings: list[dict], page: str, lang: str, as_json: bool) -> tuple[int, str]:
    """Render findings as the CLI prints them; returns (exit code, report)."""
    blockers = [f for f in findings if f["level"] == "blocker"]
    if as_json:
        text = json.dumps(findings, ensure_ascii=False, indent=2)
    else:
        lines = [f"Translation check: {page} ({lang})  Blockers: {len(blockers)}  "
                 f"Warnings: {len(findings) - len(blockers)}"]
        for f in findings:
            cid = f"#{f['claim_id']} " if f["claim_id"] else ""
            lines.append(f"  [{f['level']}] {f['check']}: {cid}{f['message']}")
        text = "\n".join(lines)
    return (1 if blockers else 0), text


def run(args) -> tuple[int, str]:
    """Load the inputs, run the checks and render the report; returns (exit code, report)."""
    root = Path(args.root).resolve()
    if args.translation is None:
        bt = None
        if args.back_translation is not None:
            bt = json.loads(read_capped(_inside(root, args.back_translation)))
        lang = load_lang(args.lang)
        findings = check_pages(read_page(_inside(root, args.en), "en"), read_page(_inside(root, args.tr), args.lang), lang, bt,
                               args.translator_model)
        return report(findings, args.tr, args.lang, args.json)
    tr = json.loads(read_capped(Path(args.translation)))
    if not isinstance(tr, dict):
        raise ValueError("translation sidecar must be an object")
    for k in ("lang", "source_sidecar", "article", "source_body_sha256", "translator_model", "claims"):
        if k not in tr:
            raise KeyError(k)
    en_sc = json.loads(read_capped(_inside(root, tr["source_sidecar"])))
    validate(en_sc, tr)
    en_path = _inside(root, en_sc["article"]["path"])
    en_text = read_capped(en_path)  # body_sha256 is the hash of the raw file text, never the converted page
    tr_html = read_page(_inside(root, tr["article"]), tr["lang"])
    findings = check(en_sc, en_text, tr, tr_html, load_lang(tr["lang"]), page_html(en_path, en_text, "en"))
    return report(findings, tr["article"], tr["lang"], args.json)


def main(argv=None) -> int:
    """CLI for both modes (see the module docstring). All paths resolve under --root."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("translation", nargs="?", help="sidecar mode: <slug>.<lang>.translation.json")
    ap.add_argument("--en", help="no-sidecar mode: the English page")
    ap.add_argument("--tr", help="no-sidecar mode: the translated page")
    ap.add_argument("--lang", help="no-sidecar mode: the translation's language code")
    ap.add_argument("--back-translation", help="no-sidecar mode: JSON list of {block, round, verdict, model, sha256}")
    ap.add_argument("--translator-model", help="no-sidecar mode: the model that translated")
    ap.add_argument("--root", default=".", help="repo root the paths are relative to")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    args = ap.parse_args(argv)
    page_args = (args.en, args.tr, args.lang, args.back_translation, args.translator_model)
    if args.translation is not None and any(a is not None for a in page_args):
        ap.error("give either a translation JSON or --en/--tr/--lang, not both")
    if args.translation is None and None in (args.en, args.tr, args.lang):
        ap.error("give a translation JSON, or all of --en, --tr and --lang")
    try:
        code, report = run(args)
        out = sys.stdout
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")
        print(report, file=out)
        return code
    except (OSError, KeyError, TypeError, ValueError, UnicodeError) as exc:
        print(f"input error: {exc!r}".encode("ascii", "backslashreplace").decode(), file=sys.stderr)
        return 2
    except Exception as exc:  # a crash must never read as a pass or as plain blockers
        print(f"script error: {exc!r}".encode("ascii", "backslashreplace").decode(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
