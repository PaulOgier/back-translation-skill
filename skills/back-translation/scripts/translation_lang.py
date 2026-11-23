"""
translation_lang.py: the language model behind verify_translation.py.

Loads languages/<code>.json and turns text into the facts a translation must keep:
numbers (value, written form, unit, comparator), dates (day, month, year) and modal
kinds (must, must-not, should, should-not, may, may-not, need-not), with negation read
around each modal. Standard library only, and Python 3.9 compatible, so it runs with
the system python.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import MAX_EMAX, MAX_PREC, MIN_EMIN, Decimal, localcontext
from pathlib import Path

LANG_DIR = Path(__file__).resolve().parent / "languages"
MARKER_RE = re.compile(r"\[(\d{1,6})\]")
SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
KINDS = {"must", "must-not", "should", "should-not", "may", "may-not", "need-not"}
CMP_KINDS = {"min", "max", "gt", "lt", "before", "after"}
# Page text wraps attribute text (alt, title) and verbatim text (<code>) in these
# private characters, so numbers can read the one and claim matching skip it.
ATTR_OPEN, ATTR_CLOSE = "￹", "￻"
VERB_OPEN, VERB_CLOSE = "", ""
ATTR_RE = re.compile(f"{ATTR_OPEN}[^{ATTR_CLOSE}]*{ATTR_CLOSE}")
VERB_RE = re.compile(f"{VERB_OPEN}[^{VERB_CLOSE}]*{VERB_CLOSE}")
FILLER = r"(?=[^\s\d.,;:!?]*[^\W\d_])[^\s\d.,;:!?]+"  # a word with a letter, no digit, not ending a sentence
DEFAULT_UNIT_GAP = r"[\s-]*(?:" + FILLER + r"\s+){0,2}?"
ABBREV_RE = re.compile(r"(?<!\w)[^\W\d_]\.\s?[^\W\d_]\.")  # z. B., e.g., d. h.
SENT_END_RE = re.compile(r"[.!?。！？](?=\s|$)|[。！？]")
ABBREVS = {"etc", "ca", "vs", "ex", "dr", "sr", "sra", "nr", "bzw", "usw", "uu", "ee", "approx", "no", "pp", "vol", "fig", "cf"}
CURRENCY_BEFORE = re.compile(r"(?:(?<![A-Za-z])(?P<c>US\$|R\$|A\$|C\$|\$|€|£|¥|USD|EUR|GBP|ZAR|BRL|JPY)|(?<![A-Za-z])(?P<r>R))\s?$")
CURRENCY_AFTER = re.compile(r"\s?(?P<c>€|USD|EUR|GBP|ZAR|BRL|JPY)(?![A-Za-z])")
CURRENCY_NAMES = {"US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "R": "ZAR", "R$": "BRL", "A$": "AUD", "C$": "CAD"}
STRICT_UNITS = ("percent", "currency:")  # compared both ways: a % or a currency the English lacks blocks
SYMBOL_CMP_BEFORE = {"≥": "min", "≤": "max", ">": "gt", "<": "lt"}
SYMBOL_CMP_AFTER = {"+": "min"}


def nfc(s: str) -> str:
    """NFC-normalise, so a decomposed ã or ü matches its composed form."""
    return unicodedata.normalize("NFC", s)


def norm_space(s: str) -> str:
    """Collapse whitespace and unify quotes, so a sentence matches across line breaks."""
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def for_numbers(text: str) -> str:
    """Page text as numbers read it: attribute text kept, verbatim spans blanked
    (they are compared exactly elsewhere, and a locale must not re-read 0.250)."""
    return VERB_RE.sub(" ", text).replace(ATTR_OPEN, " ").replace(ATTR_CLOSE, " ")


def for_matching(text: str) -> str:
    """Page text as claim sentences are matched: attribute text dropped, verbatim kept."""
    return ATTR_RE.sub(" ", text).replace(VERB_OPEN, "").replace(VERB_CLOSE, "")


def plain(text: str) -> str:
    """Page text with every marker character removed and all content kept."""
    for c in (ATTR_OPEN, ATTR_CLOSE, VERB_OPEN, VERB_CLOSE):
        text = text.replace(c, " ")
    return text


def _phrase(word: str) -> str:
    """One form as a regex: escaped, any run of spaces matching any whitespace, and an
    apostrophe matching a straight or curly one (don't, don\u2019t)."""
    return r"\s+".join(re.escape(p).replace("'", "['\u2018\u2019]") for p in word.split())


def _body(words) -> str:
    """An alternation of forms, longest first."""
    return "|".join(_phrase(w) for w in sorted(words, key=len, reverse=True)) or "(?!)"


def alt_forms(words, lang: dict, end: str = r"(?![\w-])") -> re.Pattern:
    """Compile an alternation, word-bounded where the script has word gaps.

    A hyphen counts as part of a word, so German "Soll-Zustand" is not "soll"."""
    if lang["word_boundaries"]:
        return re.compile(rf"(?<![\w-])(?:{_body(words)}){end}", re.I)
    return re.compile(f"(?:{_body(words)})", re.I)


def _key(s: str) -> str:
    """Lookup key for a matched form: spaces collapsed and case folded."""
    return norm_space(nfc(s)).casefold()


class Forms:
    """A language's fixed forms (scale words, units, modals) mapped to values: one
    regex to find them and a case-folded table to look a match up."""

    def __init__(self, mapping: dict, lang: dict, regex: re.Pattern | None = None):
        self.orig = dict(mapping)
        self.keys = {_key(k): v for k, v in self.orig.items()}
        self.re = regex or alt_forms(self.orig, lang)

    def get(self, matched: str):
        """The value for a matched form; falls back to a regex compare for letters
        such as İ that re.I matches but casefold does not map to the same key."""
        k = _key(matched)
        if k in self.keys:
            return self.keys[k]
        for form, v in self.orig.items():
            if re.fullmatch(_phrase(form), matched, re.I):
                return v
        raise ValueError(f"matched form {matched!r} has no entry")

    def __contains__(self, matched: str) -> bool:
        try:
            self.get(matched)
            return True
        except ValueError:
            return False


def _scale_forms(lang: dict, code: str) -> dict:
    """Scale words as powers of ten (thousand is 3)."""
    out = {}
    for k, v in lang.get("scale_words", {}).items():
        d = Decimal(str(v))
        if d != Decimal(10) ** d.adjusted():
            raise ValueError(f"{code}: scale word {k} is not a power of ten")
        out[k] = d.adjusted()
    return out


def _word_forms(lang: dict) -> dict:
    """Number words, with the tens-and-units compounds (twenty-five, vinte e cinco)."""
    words = {k: Decimal(v) for k, v in lang.get("number_words", {}).items()}
    for t, tv in lang.get("tens", {}).items():
        for u, uv in lang.get("compound_units", {}).items():
            for tpl in lang.get("compound", []):
                words[tpl.format(t=t, u=u)] = Decimal(tv + uv)
    return words


def _comparators(lang: dict, side: str) -> dict:
    """Comparator forms that sit before (at least 8) or after (10 or more) a number."""
    out = {}
    for kind, forms in lang.get("comparators", {}).get(side, {}).items():
        if kind not in CMP_KINDS:
            raise ValueError(f"{lang['lang']}: unknown comparator kind {kind}")
        out.update({f: kind for f in forms})
    return out


def _modal_forms(lang: dict, code: str) -> dict:
    """Modal forms mapped to their entry, after checking every kind is known."""
    out = {}
    for e in lang["normative"]:
        for k in ("kind", "negated", "bare"):
            if e.get(k) is not None and e[k] not in KINDS:
                raise ValueError(f"{code}: unknown modal kind {e[k]}")
        out.update({f: e for f in e["forms"]})
    return out


def load_lang(code: str) -> dict:
    """Load languages/<code>.json and compile its number, date, comparator and modal
    rules. Raises ValueError for an unknown or malformed language."""
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,8})*", code or ""):
        raise ValueError(f"bad language code {code!r}")
    path = LANG_DIR / f"{code}.json"
    if not path.exists():
        raise ValueError(f"no language file {path}")
    lang = json.loads(path.read_text(encoding="utf-8"))
    wb = lang["word_boundaries"]
    lang["scales"] = Forms(_scale_forms(lang, code), lang)
    nb = lang.get("scale_not_before")
    lang["scale_follow_re"] = re.compile(r"\s*(?:" + lang["scales"].re.pattern + ")" + (f"(?!{nb})" if nb else ""), re.I)
    words = _word_forms(lang)
    follow = lang.get("number_word_follow", "")
    word_re = (re.compile(rf"(?<![\w-])(?:{_body(words)})(?!\w){follow}", re.I) if wb
               else re.compile(f"(?:{_body(words)}){follow}"))
    lang["words"] = Forms(words, lang, word_re) if words else None
    units = {f: u for u, forms in lang.get("units", {}).items() for f in forms}
    gap = lang.get("unit_gap", DEFAULT_UNIT_GAP)
    end = r"(?![\w-])" if wb else ""
    lang["unit_forms"] = Forms(units, lang, re.compile(gap + "(?P<u>" + _body(units) + ")" + end, re.I))
    before, after = _comparators(lang, "before"), _comparators(lang, "after")
    adjacent = _comparators(lang, "before_adjacent")
    filler = rf"(?:\s*{FILLER}){{0,3}}?" if wb else r"[^\s\d]{0,4}?"
    sym = "|".join(re.escape(k) for k in SYMBOL_CMP_BEFORE)
    lang["cmp_before"] = Forms({**before, **adjacent, **SYMBOL_CMP_BEFORE}, lang, re.compile(
        (rf"(?:(?<![\w-])(?P<c>{_body(before)}){filler}|(?<![\w-])(?P<a>{_body(adjacent)})\s*|(?P<s>{sym})\s*)[\s\"'“”‘’(]*$" if wb
         else rf"(?:(?P<c>{_body(before)}){filler}|(?P<a>(?!))|(?P<s>{sym}))\s*$"), re.I))
    lang["cmp_after"] = Forms({**after, **SYMBOL_CMP_AFTER}, lang, re.compile(
        rf"\s*(?:(?P<c>{_body(after)})" + (r"(?![\w-])" if wb else "") + r"|(?P<s>\+)(?!\d))", re.I))
    lang["unit_before_re"] = re.compile(lang["unit_before"]) if lang.get("unit_before") else None
    bare_only = {k: Decimal(str(v)).adjusted() for k, v in lang.get("bare_scale_words", {}).items()}
    lang["bare_only"] = Forms(bare_only, lang) if bare_only else None
    lang["relative_words"] = {w.casefold() for w in lang.get("relative_words", [])}
    lang["modals"] = Forms(_modal_forms(lang, code), lang)
    lang["negator_re"] = alt_forms(lang["negators"], lang) if lang.get("negators") else None
    lang["negation_skip_re"] = alt_forms(lang["negation_skip"], lang) if lang.get("negation_skip") else None
    lang["relative_re"] = re.compile(lang["relative_clause"], re.I) if lang.get("relative_clause") else None
    breaks = _body(lang.get("clause_breaks", [])) if lang.get("clause_breaks") else ""
    lang["clause_re"] = re.compile(r"[,;:!?()。，；：！？]|(?<![0-9])\.(?![0-9])" + (rf"|(?<!\w)(?:{breaks})(?!\w)" if breaks else ""), re.I)
    aliases = lang.get("month_aliases", {})
    months = "|".join([_phrase(m) for m in sorted(lang["months"], key=len, reverse=True)]
                      + [_phrase(a) + r"\.?" for a in sorted(aliases, key=len, reverse=True)]) or "(?!)"
    flags = 0 if lang.get("dates_case_sensitive") else re.I  # English May is a month, may a modal
    lang["date_res"] = [re.compile(p.replace("{months}", months), flags) for p in lang["date_patterns"]]
    month_map = {m: i + 1 for i, m in enumerate(lang["months"])}
    month_map.update(aliases)
    lang["month_index"] = Forms(month_map, lang)
    lang["unknown_scale_re"] = (re.compile(r"[0-9]\s*(?P<w>" + lang["unknown_scale"] + ")", re.I)
                                if lang.get("unknown_scale") else None)
    lang["ambiguous_scale_re"] = alt_forms(lang["ambiguous_scale"], lang) if lang.get("ambiguous_scale") else None
    lang["run_re"] = _run_re(lang)
    return lang


def _ascii_digits(text: str) -> str:
    """Fullwidth and other Unicode digits to ASCII; superscript digits become ^n
    exponents and a vulgar fraction (½) its digits, so 8½ is not read as 8."""
    text = re.sub("[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: f" ^{m.group().translate(SUPERSCRIPT)} ", text)
    text = re.sub("[¼½¾⅐-⅞]", lambda m: " " + unicodedata.normalize("NFKC", m.group()).replace("⁄", " / ") + " ", text)
    return re.sub(r"(?![0-9])\d", lambda m: str(unicodedata.decimal(m.group())), text)


def _parse_run(run: str, lang: dict) -> tuple[Decimal, str] | None:
    """A digit run read by one language's rules as (value, written form), or None
    if it does not fit them. The form keeps leading zeros and trailing decimal
    zeros (0042, 3.10), which the value loses.

    A group separator must be the same throughout and sit before exactly three digits."""
    dec = re.escape(lang["decimal"])
    for g in lang["group"]:
        m = re.fullmatch(rf"([0-9]{{1,3}}(?:{re.escape(g)}[0-9]{{3}})+)(?:{dec}([0-9]+))?", run)
        if m:
            form = m.group(1).replace(g, "") + ("." + m.group(2) if m.group(2) else "")
            return Decimal(form), form
    m = re.fullmatch(rf"([0-9]*)(?:{dec}([0-9]+))?", run)
    if m and (m.group(1) or m.group(2)):
        form = (m.group(1) or "0") + ("." + m.group(2) if m.group(2) else "")
        return Decimal(form), form
    return None


def _run_re(lang: dict) -> re.Pattern:
    """Maximal digit runs: digits joined by . or , always, and by a space only before a three-digit group."""
    ws = "".join(re.escape(g) for g in lang["group"] if g.isspace())
    punct = "".join(re.escape(c) for c in {".", ",", lang["decimal"], *(g for g in lang["group"] if not g.isspace())})
    joiner = rf"[{punct}]" + (rf"|[{ws}](?=[0-9]{{3}}(?![0-9]))" if ws else "")
    return re.compile(rf"(?P<sign>(?<![\w.,])[-−])?(?P<run>(?<![\w.,])[{punct}](?=[0-9])[0-9]+|[0-9]+(?:(?:{joiner})[0-9]+)*)"
                      rf"|\^(?P<exp>[0-9]+)")


@dataclass
class _Num:
    """One number found in text: its span, value, written form and scale exponent."""
    start: int
    end: int
    value: object  # Decimal, or the raw run when no rule reads it
    form: str | None = None
    scale: int | None = None


def _raw_numbers(text: str, lang: dict, en: dict | None) -> list[_Num]:
    """Digit runs, exponents and number words, in text order."""
    found = []
    for m in lang["run_re"].finditer(text):
        if m.group("exp"):
            found.append(_Num(m.start(), m.end(), "^" + m.group("exp")))
            continue
        run = m.group("run").rstrip(".,")
        parsed = _parse_run(run, lang) or (_parse_run(run, en) if en is not None else None)
        if parsed is None:
            found.append(_Num(m.start(), m.start("run") + len(run), run))
            continue
        val, form = parsed
        if m.group("sign"):
            val, form = -val, "-" + form
        found.append(_Num(m.start(), m.start("run") + len(run), val, form))
    if lang["words"]:
        found += [_Num(m.start(), m.end(), lang["words"].get(m.group())) for m in lang["words"].re.finditer(text)]
    return sorted(found, key=lambda n: n.start)


def _apply_scales(text: str, lang: dict, found: list[_Num]) -> list[_Num]:
    """Attach a following scale word (10.7 billion), join Chinese 1亿2000万 into one
    number, and add bare scale words (billions of years) as numbers of their own."""
    out: list[_Num] = []
    for n in found:
        sm = lang["scale_follow_re"].match(text, n.end)
        if sm:
            n.scale = lang["scales"].get(sm.group().strip())
            n.value = n.value.scaleb(n.scale) if isinstance(n.value, Decimal) else f"{n.value} 1e{n.scale}"
            n.form, n.end = None, sm.end()
        prev = out[-1] if out else None
        if (lang.get("combine_scales") and prev and prev.end == n.start and prev.scale is not None
                and isinstance(prev.value, Decimal) and isinstance(n.value, Decimal) and (n.scale or 0) < prev.scale):
            prev.value, prev.end, prev.scale = prev.value + n.value, n.end, n.scale
            continue
        out.append(n)
    bare = lang["scales"] if lang.get("bare_scales", True) else lang["bare_only"]
    if bare:
        starts = [n.start for n in out]
        for m in bare.re.finditer(text):
            i = bisect.bisect_right(starts, m.start()) - 1
            if i >= 0 and out[i].end > m.start():
                continue  # already part of a number
            exp = bare.get(m.group())
            out.append(_Num(m.start(), m.end(), Decimal(1).scaleb(exp), None, exp))
        out.sort(key=lambda n: n.start)
    return out


def number_tokens(text: str, lang: dict, en: dict | None = None) -> list[tuple]:
    """Every number in the text as (value, form, unit, comparator), in order.

    value is an exact Decimal with scale words applied, or a string for a run that
    no rule reads (192.168.1.1) or a ^n exponent. A run the language cannot read is
    read by English rules when en is given, so TLS 1.3 kept verbatim compares equal.
    form is the written digits when no scale word applies. unit (day, year,
    character, ...) may sit up to a few words after the number. comparator is min,
    max, gt, lt, before or after when one qualifies the number."""
    text = _ascii_digits(MARKER_RE.sub(" ", text))
    tokens, prev_end = [], 0
    with localcontext() as ctx:
        ctx.prec, ctx.Emax, ctx.Emin = MAX_PREC, MAX_EMAX, MIN_EMIN
        for n in _apply_scales(text, lang, _raw_numbers(text, lang, en)):
            am = lang["cmp_after"].re.match(text, n.end)  # 15 or more characters
            um = lang["unit_forms"].re.match(text, am.end() if am else n.end)
            unit = lang["unit_forms"].get(um.group("u")) if um else None
            if not am:
                am = lang["cmp_after"].re.match(text, um.end() if um else n.end)  # 15 characters or more
            window = text[max(prev_end, n.start - 80):n.start]
            bm = lang["cmp_before"].re.search(window)
            cmp = _cmp(lang["cmp_before"], bm) if bm else (_cmp(lang["cmp_after"], am) if am else None)
            unit = _currency(window, text, n.end) or unit
            ub = lang["unit_before_re"].search(window) if lang["unit_before_re"] else None
            if ub and not unit:
                unit = lang["unit_forms"].get(ub.group("u"))  # zh 每秒3401亿次
            val = n.value.normalize() if isinstance(n.value, Decimal) else n.value
            tokens.append((val, n.form, unit, cmp))
            prev_end = max(prev_end, n.end)
    return tokens


def _cmp(forms: Forms, m: re.Match) -> str:
    """The comparator kind of a before- or after-number match."""
    return forms.get(next(g for g in m.groups() if g))


def _currency(window: str, text: str, end: int) -> str | None:
    """A currency symbol or code right before ($500, R$ 500) or after (500 €) a number."""
    m = CURRENCY_BEFORE.search(window)
    if m:
        c = m.group("c") or m.group("r")
        return "currency:" + CURRENCY_NAMES.get(c, c)
    m = CURRENCY_AFTER.match(text, end)
    return "currency:" + CURRENCY_NAMES.get(m.group("c"), m.group("c")) if m else None


def _strict(unit) -> bool:
    return bool(unit) and unit.startswith(STRICT_UNITS)


def same_numbers(en_tokens: list, tr_tokens: list) -> bool:
    """Same numbers in the same order. Values and comparators must match; the written
    form must match when both sides have one; a unit must match when English names
    one, and a percent or currency must match either way."""
    return len(en_tokens) == len(tr_tokens) and all(
        va == vb and (fa is None or fb is None or fa == fb) and ca == cb
        and (ua == ub or (ua is None and not _strict(ub)))
        for (va, fa, ua, ca), (vb, fb, ub, cb) in zip(en_tokens, tr_tokens))


def fmt_tokens(tokens) -> str:
    """Tokens for a message, shortened."""
    def one(t):
        v, _, u, c = t
        s = (f"{v:f}" if isinstance(v, Decimal) else str(v))[:40]
        return (f"{c} " if c else "") + s + (f" {u}" if u else "")
    return ", ".join(one(t) for t in tokens[:12]) + (", ..." if len(tokens) > 12 else "")


def scale_problems(text: str, lang: dict) -> list[str]:
    """Scale words right after a number that the language file does not know (an
    -illion it has no value for), and ones it marks ambiguous (Chinese 兆)."""
    out = []
    if lang["unknown_scale_re"]:
        out += [f"unknown scale word '{m.group('w')}'" for m in lang["unknown_scale_re"].finditer(text)
                if m.group("w") not in lang["scales"]]
    if lang["ambiguous_scale_re"]:
        out += [f"ambiguous scale word '{m.group()}': {lang.get('ambiguous_scale_why', 'write it another way')}"
                for m in lang["ambiguous_scale_re"].finditer(text)]
    return out


def extract_dates(text: str, lang: dict) -> tuple[list[tuple], str]:
    """Dates as (day, month, year, comparator), None for a part not written, plus the
    text with the dates blanked so their digits are not read again as numbers.
    The comparator is a before/after word in front of the date (antes de 2021)."""
    found = []

    def take(m):
        g = m.groupdict()
        month = int(g["m"]) if g["m"].isdigit() else lang["month_index"].get(g["m"].rstrip("."))
        bm = lang["cmp_before"].re.search(cur[max(0, m.start() - 60):m.start()])
        found.append((m.start(), (int(g["d"]) if g.get("d") else None, month, int(g["y"]) if g.get("y") else None,
                                  _cmp(lang["cmp_before"], bm) if bm else None)))
        return " " * len(m.group())

    for rx in lang["date_res"]:
        cur = text
        text = rx.sub(take, text)
    return [d for _, d in sorted(found)], text


def _blank(rx: re.Pattern | None, text: str) -> str:
    """Replace every match with spaces of the same length, so positions still line up."""
    return rx.sub(lambda m: " " * len(m.group()), text) if rx else text


def modal_kinds(sentence: str, lang: dict) -> list[str]:
    """The sentence's normative renderings in order, as kinds (must, must-not, should,
    should-not, may, may-not, need-not).

    Dates are blanked first, so the month May is not a modal. A modal takes its
    entry's negated kind when a negator sits near it: within the language's word
    window (en, pt, es: "O NIST não exige" is need-not) or anywhere in its clause
    (German, which puts nicht late: "darf ... nicht" is must-not). "not only" never
    negates. A form with kind null ("be required") counts for nothing after a modal
    in its clause, and as its bare kind otherwise ("will be required" is must)."""
    _, text = extract_dates(norm_space(nfc(sentence)), lang)
    scan = _blank(lang["negation_skip_re"], text)
    neg_scan = _blank(lang["relative_re"], _blank(ABBREV_RE, scan))
    bounds = [(b.start(), b.end()) for b in lang["clause_re"].finditer(neg_scan)]
    starts, ends = [b for b, _ in bounds], [e for _, e in bounds]
    negs = negator_positions(neg_scan, lang)
    word_spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", neg_scan)]
    wstarts = [s for s, _ in word_spans]
    window = lang.get("negation_window")
    out, last_clause = [], None
    for m in lang["modals"].re.finditer(scan):
        e = lang["modals"].get(m.group())
        ci = bisect.bisect_right(ends, m.start())
        kind = e.get("kind")
        if kind is None:
            if last_clause == ci or not e.get("bare"):
                continue
            kind = e["bare"]
        lo = ends[ci - 1] if ci else 0
        nxt = bisect.bisect_left(starts, m.end())
        hi = starts[nxt] if nxt < len(starts) else len(text)
        if window and word_spans:
            first = bisect.bisect_right(wstarts, m.start()) - 1
            last = bisect.bisect_right(wstarts, m.end() - 1) - 1
            before = window[0]
            if ci and neg_scan[bounds[ci - 1][0]:bounds[ci - 1][1]].strip().casefold() in lang["relative_words"]:
                before = lang.get("relative_window", 1)  # "that are not unique must": the not is the clause's
            lo = max(lo, word_spans[max(0, first - before)][0])
            hi = min(hi, word_spans[min(len(word_spans) - 1, last + window[1])][1])
        if e.get("negated") and negs:
            in_range = bisect.bisect_left(negs, hi) - bisect.bisect_left(negs, lo)
            in_form = bisect.bisect_left(negs, m.end()) - bisect.bisect_left(negs, m.start())
            if in_range > in_form:
                kind = e["negated"]
        out.append(kind)
        last_clause = ci
    return out


def negator_positions(text: str, lang: dict) -> list[int]:
    """Where the language's negators start in the text."""
    return [m.start() for m in lang["negator_re"].finditer(text)] if lang["negator_re"] else []


def sentences(text: str, lang: dict) -> list[str]:
    """Split text into sentences; CJK ends a sentence on 。！？ with no space after."""
    ends = lang.get("sentence_end")
    parts = re.split(rf"(?<=[{re.escape(ends)}])", text) if ends else re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def sentence_sha256(sentence: str) -> str:
    """Hash a sentence as a back-translation record stores it (NFC, spaces collapsed).
    Used for the translated sentence (sentence_sha256) and the English claim (claim_sha256)."""
    return hashlib.sha256(norm_space(nfc(sentence)).encode("utf-8")).hexdigest()


def match_norm(s: str) -> str:
    """A sentence for matching: NFC, spaces collapsed, no space before punctuation."""
    return re.sub(r"\s+(?=[,.;:!?)\]。，；：！？])", "", norm_space(nfc(s)))


PUNCT = set(" ,.;:!?)\"'。，；：！？")


class MarkedBlock:
    """A block's text flattened for claim matching, with the position of each [n]
    marker and the sentence spans. Built once per block."""

    def __init__(self, block_text: str):
        parts, size, marks = [], 0, []
        for i, c in enumerate(MARKER_RE.split(norm_space(for_matching(block_text)))):
            if i % 2:
                marks.append((size, int(c)))
                continue
            piece = match_norm(c)
            if parts and piece and c[:1].isspace() and piece[0] not in PUNCT:
                piece = " " + piece
            parts.append(piece)
            size += len(piece)
        self.flat = "".join(parts)
        self.marks = marks
        self.positions = [p for p, _ in marks]
        self.first, self.at = {}, {}
        for k, (p, mid) in enumerate(marks):
            self.first.setdefault(mid, p)
            self.at.setdefault(mid, []).append(k)
        # where the run of punctuation and spaces that ends at each marker begins
        self.pstart, prev = [], (-1, 0)
        for p, _ in marks:
            if p == prev[0]:
                self.pstart.append(prev[1])  # markers side by side share one run
                continue
            q = p
            while q > 0 and self.flat[q - 1] in PUNCT:
                q -= 1
            self.pstart.append(q)
            prev = (p, q)
        self.sent_ends = [m.end() for m in SENT_END_RE.finditer(self.flat) if not self._abbreviation(m.start())]
        self.sent_ends.append(len(self.flat))

    def _abbreviation(self, dot: int) -> bool:
        """A full stop that does not end a sentence: after a digit (German 17. September),
        a single letter (z. B.) or a short abbreviation (etc., ca., p. ex.)."""
        if self.flat[dot] != "." or dot == 0:
            return False
        word = re.search(r"([^\W_]+)$", self.flat[max(0, dot - 12):dot])
        return bool(word) and (word.group(1)[-1].isdigit() or len(word.group(1)) == 1
                               or word.group(1).casefold() in ABBREVS)

    def binds(self, sentence: str, cid: int) -> bool:
        """True when marker [cid] is bound to the sentence: inside an occurrence of it,
        or right after one with only punctuation or space between. Looks only around
        the marker, so a short sentence repeated across a long block stays cheap."""
        target = match_norm(sentence)
        n = len(target)
        if not n:
            return False
        for k in self.at.get(cid, []):
            pos, q = self.positions[k], self.pstart[k]
            if self.flat.find(target, max(0, q - n), pos) >= 0:
                return True  # an occurrence ends in the punctuation run before the marker
            i = self.flat.find(target, max(0, pos - n + 1), pos + n)
            if 0 <= i < pos:
                return True  # the marker sits inside an occurrence
        return False

    def sentence_of(self, cid: int) -> tuple[int, int] | None:
        """The (start, end) of the page sentence that carries marker [cid]."""
        pos = self.first.get(cid)
        if pos is None:
            return None
        k = min(bisect.bisect_left(self.sent_ends, pos), len(self.sent_ends) - 1)
        return (self.sent_ends[k - 1] if k else 0), self.sent_ends[k]

    def remainder(self, span: tuple[int, int], claim_texts: dict[int, str]) -> str:
        """Text of a page sentence that no claim covers: the sentence minus every claim
        whose marker sits in it, letters, digits and in-word apostrophes only."""
        s, e = span
        text = self.flat[s:e]
        ids = {mid for _, mid in self.marks[bisect.bisect_left(self.positions, s):bisect.bisect_right(self.positions, e)]}
        for mid in sorted(ids):
            if mid in claim_texts:
                text = text.replace(match_norm(claim_texts[mid]), " ", 1)
        text = re.sub(r"[^\w']+|_+", " ", text)
        return re.sub(r"\s+", " ", re.sub(r"(?<!\w)'|'(?!\w)", " ", text)).strip()  # keep don't, drop stray quotes
