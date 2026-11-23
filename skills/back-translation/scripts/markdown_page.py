#!/usr/bin/env python3
"""
markdown_page.py: convert a Markdown article (Hugo, Astro, Next, Jekyll style) to
the HTML page verify_translation.py parses. Standard library only and Python 3.9
compatible, like the checker.

The output is one page: <html lang> and, in <head>, <title> and
<meta name="description"> from front matter; everything else inside one <main>.
Equivalent Markdown and hand-written HTML parse into the same block structure, so
every check reads a Markdown pair as it reads an HTML pair.

Supported (a practical CommonMark and GFM subset):
  - front matter between --- lines whose first line is a key: line: title,
    description and lang as key: value lines, quoted or not, or as | and > block
    scalars (with chomping and indentation indicators); the text of every other
    prose value goes into <meta name="x-front-matter"> so its numbers are compared
    per key
  - ATX and setext headings, paragraphs, hard line breaks (two trailing spaces or
    a backslash), thematic breaks
  - bullet and ordered lists with nesting, tight and loose, ordered start numbers,
    GFM task items ([ ] and [x], as a disabled checkbox)
  - block quotes, with lazy continuation lines
  - fenced code (``` and ~~~, info string as class="language-x") and indented code
  - inline code with multi-backtick spans, emphasis and strong (* and _, with the
    CommonMark flanking and rule-of-three rules)
  - inline links and images with titles, autolinks (<scheme:...> and
    <user@host>), bare http(s) URLs (trailing punctuation left out), reference
    links (full, collapsed, shortcut) and definitions
  - GFM pipe tables with \\| in cells
  - HTML entities and numeric character references, backslash escapes
  - raw HTML blocks and inline HTML, passed through unchanged; an MDX/JSX
    component line (<Callout ...>, </Callout>) passes through and the Markdown
    around and inside it is still converted; an inline <script>, <style> or
    <template> with no closing tag in its paragraph is kept as text

Not supported (the text is kept, read as plain paragraphs or literal characters):
  - YAML beyond key: value lines and those block scalars (lists and nested maps
    are read as text lines), TOML (+++) and JSON front matter
  - GFM footnotes ([^1]), strikethrough (~~), www. autolinks without a scheme,
    table column alignment (ignored)
  - multi-line link reference definitions (title on the next line)
  - MDX import/export lines and {expressions}, shortcodes ({{< ... >}}, {% ... %})
  - percent-encoding of link destinations (URLs are kept as written so both
    pages compare as written)
  - edge cases of list and lazy-continuation nesting where CommonMark looks at
    what the previous line opened; a table ends at the first line without a pipe

Every regular expression here is anchored or has no overlapping adjacent
quantifiers, and every scan that could restart (code-span closers, comment
closers, brackets, emphasis) is cached or bounded, so a pathological line
converts in linear time.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
from bisect import bisect_right
from html.entities import html5

ASCII_PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
BLOCK_HTML = set(
    "address article aside base basefont blockquote body caption center col colgroup dd details dialog dir div dl dt "
    "fieldset figcaption figure footer form frame frameset h1 h2 h3 h4 h5 h6 head header hr html iframe legend li "
    "link main menu menuitem nav noframes ol optgroup option p param search section summary table tbody td tfoot th "
    "thead title tr track ul".split())
RAW_HTML = ("script", "pre", "style", "textarea")
MAX_NESTING = 64  # deeper quotes or lists read as one paragraph, so recursion stays bounded

_LIST = re.compile(r"( {0,3})([*+-]|[0-9]{1,9}[.)])([ \t]+|$)")
_HTML_START = re.compile(r" {0,3}<(/?)([A-Za-z][A-Za-z0-9-]*)(?=[\s/>]|$)")
_ATTRS = r"(?:\s+[A-Za-z_:][A-Za-z0-9_.:-]*(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*"
_OPEN_TAG = re.compile(r"<[A-Za-z][A-Za-z0-9-]*" + _ATTRS + r"\s*/?>")
_CLOSE_TAG = re.compile(r"</[A-Za-z][A-Za-z0-9-]*\s*>")
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9.+-]{1,31}:[^\s<>]*)>")
_EMAIL = re.compile(r"<([^\s<>@\\]+@[A-Za-z0-9.-]+)>")
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|([A-Za-z][A-Za-z0-9]{0,31}));")
_DEF = re.compile(r" {0,3}\[([^\]]{1,999})\]:[ \t]*(?:<([^<>\n]*)>|([^\s<>]+))"
                  r"(?:[ \t]+(\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*$")
_PUNCT_CLASS = "[!-/:-@\\[-`{-~]"
_DEST_CHAR = r"(?:[^\s()\\\x00-\x1f]|\\" + _PUNCT_CLASS + r"|\\(?!" + _PUNCT_CLASS + r"))"
_DEST_NEST = _DEST_CHAR
for _ in range(3):  # balanced parentheses nest up to 3 deep in a link destination
    _DEST_NEST = r"(?:" + _DEST_CHAR + r"|\((?:" + _DEST_NEST + r")*\))"
_DEST = re.compile(r"(?:" + _DEST_NEST + r")*")
_PLAIN = re.compile(r"[^\\`&<!\[\]*_\n]+")
_DELIM_CELL = re.compile(r":?-+:?")
_FRONT_KEY = re.compile(r"([A-Za-z_][\w-]*)[ \t]*:(?:[ \t]+(.*))?$")
_BLOCK_SCALAR = re.compile(r"[|>](?:[+-][1-9]?|[1-9][+-]?)?")
_DATE_VALUE = re.compile(r"[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}(?:(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]*)?)?"
                         r"(?:[ \t]*(?:Z|[+-][0-9]{1,2}(?::?[0-9]{2})?))?)?")
_NON_PROSE_KEYS = {"slug", "aliases", "weight", "url", "permalink", "layout", "draft", "translated_by", "lastmod"}
_TASK = re.compile(r"\[([ xX])\](?:[ \t]+|$)")
_BARE_URL = re.compile(r"(?<![\w/])https?://[^\x00-\x20\x7f-\U0010ffff<>\[\]\"']+")  # ASCII only: «», 。 and quotes end it
_RAW_INLINE = re.compile(r"<(script|style|template)(?=[\s/>])", re.I)


def markdown_to_html(text: str, lang: str | None = None, default_lang: str | None = None) -> str:
    """Convert a Markdown article to a full HTML page for parse_page.

    Front matter title, description and lang go into <head> and <html lang>; lang,
    when given, overrides the front matter. default_lang is used only when the front
    matter has no lang, because sites such as Hugo set the language by folder. The
    body goes inside one <main>."""
    meta, body = _front_matter(text.lstrip("﻿"))
    if lang is not None:
        meta["lang"] = lang
    elif default_lang and not meta.get("lang"):
        meta["lang"] = default_lang
    refs: dict[str, tuple[str, str | None]] = {}
    out: list = []
    _blocks(body.split("\n"), out, refs, False, 0)
    main = "\n".join(x if isinstance(x, str) else x.render(refs) for x in out)
    head = ['<meta charset="utf-8">']
    if "title" in meta:
        head.append(f"<title>{html.escape(meta['title'], quote=False)}</title>")
    if "description" in meta:
        head.append(f'<meta name="description" content="{html.escape(meta["description"])}">')
    if "other" in meta:
        head.append(f'<meta name="x-front-matter" content="{html.escape(meta["other"])}">')
    lang_attr = f' lang="{html.escape(meta["lang"])}"' if meta.get("lang") else ""
    return (f"<!doctype html>\n<html{lang_attr}>\n<head>\n" + "\n".join(head) + "\n</head>\n<body>\n<main>\n"
            + main + "\n</main>\n</body>\n</html>\n")


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """Split front matter between --- lines from the body: title, description and lang
    as one-line scalars or block scalars (|, >, with chomping and indentation
    indicators), plus every other prose value's text as "other": a JSON object of key
    to text, whose numbers the checker compares key by key for keys both pages have.
    Dates, slugs, weights, URLs, layout and translation bookkeeping are left out, as a
    translation has its own. A leading --- is front matter only when its first
    non-blank line is a key: line, so a page that opens with a thematic break keeps its text."""
    lines = text.split("\n")
    if lines[0].rstrip() != "---":
        return {}, text
    first = next((x for x in lines[1:] if x.strip()), "")
    if not _FRONT_KEY.match(first):
        return {}, text
    meta: dict[str, str] = {}
    other: dict[str, list[str]] = {}
    key, block, folded = None, None, False

    def end_block():
        if key is not None and block is not None:
            meta[key] = (" " if folded else "\n").join(x.strip() for x in block).strip()

    for n, line in enumerate(lines[1:], 1):
        if line.rstrip() in ("---", "..."):
            end_block()
            prose = {k: "\n".join(v).strip() for k, v in other.items()}
            prose = {k: v for k, v in prose.items() if not _DATE_VALUE.fullmatch(_yaml_scalar(v))}
            if prose:
                meta["other"] = json.dumps(prose, ensure_ascii=False)
            return meta, "\n".join(lines[n + 1:])
        m = _FRONT_KEY.match(line)
        if m:
            end_block()
            key, block = m.group(1), None
            value = (m.group(2) or "").strip()
            if key in ("title", "description", "lang"):
                if _BLOCK_SCALAR.fullmatch(value.split(" #", 1)[0].strip()):
                    block, folded = [], value[0] == ">"
                else:
                    meta[key] = _yaml_scalar(value)
                continue
            line = value
        if block is not None:
            block.append(line)
        elif key is not None and _prose_key(key):
            other.setdefault(key, []).append(line)
    return {}, text  # no closing line: not front matter


def _prose_key(key: str) -> bool:
    """Whether a front matter key holds prose a translation renders, rather than a date,
    an address, an ordering or translation bookkeeping that differs by design."""
    k = key.lower()
    return not (k in _NON_PROSE_KEYS or "date" in k or k.startswith(("translation", "source_")))


def _yaml_scalar(v: str) -> str:
    """A one-line YAML scalar: quotes removed and unescaped, or a trailing # comment dropped."""
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v.split(" #", 1)[0].rstrip()


def _indent(line: str) -> int:
    """Columns of leading whitespace, tabs to the next multiple of 4."""
    col = 0
    for ch in line:
        if ch == " ":
            col += 1
        elif ch == "\t":
            col += 4 - col % 4
        else:
            break
    return col


def _dedent(line: str, cols: int) -> str:
    """Remove up to cols columns of leading whitespace, expanding tabs only there."""
    stripped = line.lstrip(" \t")
    lead = line[:len(line) - len(stripped)].expandtabs(4)
    return lead[min(cols, len(lead)):] + stripped


def _is_blank(line: str) -> bool:
    return not line.strip()


def _atx(line: str) -> tuple[int, str] | None:
    """(level, text) of an ATX heading line, closing #s removed, or None."""
    if _indent(line) > 3:
        return None
    s = line.lstrip(" ")
    k = len(s) - len(s.lstrip("#"))
    if not 1 <= k <= 6 or (len(s) > k and s[k] not in " \t"):
        return None
    t = s[k:].strip()
    bare = t.rstrip("#")
    if bare != t and (not bare or bare[-1] in " \t"):
        t = bare.rstrip()
    return k, t


def _fence(line: str) -> tuple[int, str, str] | None:
    """(indent, fence, info) of a code fence opener, or None."""
    ind = _indent(line)
    if ind > 3:
        return None
    s = line.lstrip(" ")
    ch = s[:1]
    if ch not in ("`", "~"):
        return None
    k = len(s) - len(s.lstrip(ch))
    info = s[k:].strip()
    if k < 3 or (ch == "`" and "`" in info):
        return None
    return ind, s[:k], info


def _is_hr(line: str) -> bool:
    if _indent(line) > 3:
        return False
    s = line.replace(" ", "").replace("\t", "")
    return len(s) >= 3 and s[0] in "*-_" and s == s[0] * len(s)


def _setext(line: str) -> int:
    """1 or 2 for a setext underline (=== or ---), else 0."""
    if _indent(line) > 3:
        return 0
    s = line.strip()
    if s and s == "=" * len(s):
        return 1
    if s and s == "-" * len(s):
        return 2
    return 0


def _list_item(line: str) -> tuple[str, int, int | None, str] | None:
    """(kind, content column, start number, first line) of a list item, or None.
    kind is the bullet character or the ordered delimiter."""
    m = _LIST.match(line)
    if not m or _is_hr(line):
        return None
    marker, gap = m.group(2), m.group(3)
    ordered = marker[-1] in ".)" and marker[0].isdigit()
    width = len(m.group(1)) + len(marker)
    rest = line[m.end():]
    if not rest.strip():
        width += 1
        rest = ""
    else:
        g = _indent(" " * width + gap) - width
        if g > 4:  # content indented 5+ is code: the item column is marker + 1
            width += 1
            rest = " " * (g - 1) + rest
        else:
            width += g
    return (marker[-1] if ordered else marker), width, (int(marker[:-1]) if ordered else None), rest


def _html_block(line: str) -> str | None:
    """The kind of HTML block a line opens: a closer string to end at, "blank",
    "jsx" (one component line), "line" (type 7), or None."""
    if _indent(line) > 3:
        return None
    s = line.strip()
    low = s.lower()
    for tag in RAW_HTML:
        if low.startswith("<" + tag) and (len(low) == len(tag) + 1 or low[len(tag) + 1] in " \t>"):
            return "</" + tag + ">"
    if s.startswith("<!--"):
        return "-->"
    if s.startswith("<?"):
        return "?>"
    if s.startswith("<![CDATA["):
        return "]]>"
    if s.startswith("<!") and s[2:3].isalpha():
        return ">"
    if len(s) > 2 and s.endswith(">") and (s[1:2].isupper() or (s[1:2] == "/" and s[2:3].isupper())):
        return "jsx"
    m = _HTML_START.match(line)
    if m and m.group(2).lower() in BLOCK_HTML:
        return "blank"
    if _OPEN_TAG.fullmatch(s) or _CLOSE_TAG.fullmatch(s):
        return "line"
    return None


def _starts_block(line: str) -> bool:
    """True if a line opens a block that interrupts a paragraph."""
    if line.lstrip(" \t")[:1] not in "#`~*-_>+<0123456789":
        return False
    if _is_blank(line) or _atx(line) or _fence(line) or _is_hr(line):
        return True
    if _indent(line) <= 3 and line.lstrip(" ").startswith(">"):
        return True
    item = _list_item(line)
    if item and item[3].strip() and item[2] in (None, 1):
        return True
    kind = _html_block(line)
    return kind is not None and kind != "line"


def _cells(line: str) -> list[str]:
    """Split a table row on unescaped pipes; \\| becomes a literal pipe."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and s[i + 1:i + 2] == "|":
            cur.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    cells.append("".join(cur).strip())
    return cells


def _table_start(lines: list[str], i: int) -> list[str] | None:
    """The header cells if lines[i] and lines[i + 1] open a GFM table, else None."""
    if i + 1 >= len(lines) or "|" not in lines[i] or "|" not in lines[i + 1] or _indent(lines[i]) > 3:
        return None
    head, delim = _cells(lines[i]), _cells(lines[i + 1])
    if len(head) != len(delim) or not all(_DELIM_CELL.fullmatch(c) for c in delim):
        return None
    return head


class _Inline:
    """Inline text whose rendering waits until every link definition is known."""
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text

    def render(self, refs: dict) -> str:
        return _inline(self.text, refs)


def _norm_label(label: str) -> str:
    return " ".join(label.split()).casefold()


def _blocks(lines: list[str], out: list, refs: dict, tight: bool, depth: int) -> None:
    """Parse lines into blocks, appending HTML strings and _Inline parts to out.
    tight drops the <p> around paragraphs, as in a tight list item."""
    if depth > MAX_NESTING:
        out.extend(["<p>", _Inline("\n".join(x.strip() for x in lines).strip()), "</p>"])
        return
    para: list[str] = []

    def take_defs():
        k = 0
        while k < len(para):
            m = _DEF.match(para[k])
            if not m:
                break
            refs.setdefault(_norm_label(m.group(1)), (_unescape(m.group(2) if m.group(2) is not None else m.group(3)),
                                                      _unescape(m.group(4)[1:-1]) if m.group(4) else None))
            k += 1
        del para[:k]

    def flush():
        take_defs()
        if para:
            body = _Inline("\n".join(para).rstrip())
            out.extend([body] if tight else ["<p>", body, "</p>"])
            para.clear()

    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if _is_blank(line):
            flush()
            i += 1
            continue
        if para and _setext(line):
            take_defs()
            if para:
                level = _setext(line)
                out.extend([f"<h{level}>", _Inline("\n".join(para).rstrip()), f"</h{level}>"])
                para.clear()
                i += 1
                continue
        if not para and _indent(line) >= 4:
            code = []
            while i < n and (_is_blank(lines[i]) or _indent(lines[i]) >= 4):
                code.append(_dedent(lines[i], 4))
                i += 1
            while code and _is_blank(code[-1]):
                code.pop()
            out.append("<pre><code>" + _esc_code("\n".join(code) + "\n") + "</code></pre>")
            continue
        head = _table_start(lines, i)
        if head is not None:
            flush()
            out.append("<table>\n<thead>")
            _row(out, "th", head)
            out.append("</thead>")
            i += 2
            if i < n and "|" in lines[i] and not _starts_block(lines[i]):
                out.append("<tbody>")
                while i < n and "|" in lines[i] and not _starts_block(lines[i]):
                    _row(out, "td", (_cells(lines[i]) + [""] * len(head))[:len(head)])
                    i += 1
                out.append("</tbody>")
            out.append("</table>")
            continue
        if para and not _starts_block(line):
            para.append(line.lstrip(" \t"))
            i += 1
            continue
        flush()
        atx = _atx(line)
        if atx:
            out.extend([f"<h{atx[0]}>", _Inline(atx[1]), f"</h{atx[0]}>"])
            i += 1
            continue
        fence = _fence(line)
        if fence:
            ind, mark, info = fence
            i += 1
            code = []
            while i < n:
                s = lines[i]
                if _indent(s) <= 3 and s.strip().startswith(mark) and s.strip() == mark[0] * len(s.strip()):
                    i += 1
                    break
                code.append(_dedent(s, ind))
                i += 1
            lang = info.split()[0] if info else ""
            cls = f' class="language-{_attr(_unescape(lang))}"' if lang else ""
            out.append(f"<pre><code{cls}>" + _esc_code("".join(c + "\n" for c in code)) + "</code></pre>")
            continue
        if _is_hr(line):
            out.append("<hr>")
            i += 1
            continue
        if _indent(line) <= 3 and line.lstrip(" ").startswith(">"):
            quote: list[str] = []
            while i < n:
                s = lines[i]
                if _indent(s) <= 3 and s.lstrip(" ").startswith(">"):
                    s = s.lstrip(" ")[1:]
                    quote.append(_dedent(s, 1) if s[:1] in (" ", "\t") else s)
                elif quote and not _is_blank(quote[-1]) and not _starts_block(s):
                    quote.append(s)
                else:
                    break
                i += 1
            out.append("<blockquote>")
            _blocks(quote, out, refs, False, depth + 1)
            out.append("</blockquote>")
            continue
        item = _list_item(line)
        if item:
            i = _list(lines, i, out, refs, depth)
            continue
        kind = _html_block(line)
        if kind == "jsx":
            out.append(line)
            i += 1
            continue
        if kind is not None:
            raw = []
            while i < n:
                raw.append(lines[i])
                i += 1
                if kind in ("blank", "line"):
                    if i < n and _is_blank(lines[i]):
                        break
                elif kind in raw[-1].lower()[2 if len(raw) == 1 else 0:]:
                    break
            out.append("\n".join(raw))
            continue
        para.append(line.lstrip(" \t"))
        i += 1
    flush()


def _list(lines: list[str], i: int, out: list, refs: dict, depth: int) -> int:
    """Parse one list starting at lines[i]; return the index after it."""
    first = _list_item(lines[i])
    kind, start = first[0], first[2]
    items: list[list[str]] = []
    n = len(lines)
    while i < n:
        item = _list_item(lines[i])
        if not item or item[0] != kind:
            break
        width, body = item[1], [item[3]]
        i += 1
        while i < n:
            s = lines[i]
            if _is_blank(s):
                body.append("")
            elif _indent(s) >= width:
                body.append(_dedent(s, width))
            elif body[-1].strip() and not _starts_block(s) and not _list_item(s):
                body.append(s.lstrip(" \t"))  # lazy continuation of the item's last paragraph
            else:
                break
            i += 1
        items.append(body)
    loose = False
    for k, body in enumerate(items):
        end = len(body)
        while end and _is_blank(body[end - 1]):
            end -= 1
        if end < len(body) and k < len(items) - 1:
            loose = True
        if any(_is_blank(body[j]) for j in range(1, end)):
            loose = True
    while items and items[-1] and _is_blank(items[-1][-1]):
        items[-1].pop()
    tag = "ul" if start is None else "ol"
    out.append(f"<{tag}>" if start in (None, 1) else f'<ol start="{start}">')
    for body in items:
        out.append("<li>")
        task = _TASK.match(body[0]) if body else None  # an empty last item has no lines left
        if task:  # GFM task item: the checkbox state is a token both pages must match
            out.append('<input type="checkbox" disabled checked>' if task.group(1) in "xX" else '<input type="checkbox" disabled>')
            body = [body[0][task.end():]] + body[1:]
        _blocks(body, out, refs, not loose, depth + 1)
        out.append("</li>")
    out.append(f"</{tag}>")
    return i


def _row(out: list, tag: str, cells: list[str]) -> None:
    out.append("<tr>")
    for c in cells:
        out.extend([f"<{tag}>", _Inline(c), f"</{tag}>"])
    out.append("</tr>")


def _esc_code(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_text(s: str) -> str:
    """Escape prose for HTML, keeping valid entity references as written."""
    out, i = [], 0
    while True:
        j = s.find("&", i)
        if j < 0:
            out.append(s[i:].replace("<", "&lt;").replace(">", "&gt;"))
            return "".join(out)
        out.append(s[i:j].replace("<", "&lt;").replace(">", "&gt;"))
        m = _ENTITY.match(s, j)
        if m and (m.group(1) is None or m.group(1) + ";" in html5):
            out.append(m.group(0))
            i = m.end()
        else:
            out.append("&amp;")
            i = j + 1


def _attr(s: str) -> str:
    return _esc_text(s).replace('"', "&quot;")


def _unescape(s: str) -> str:
    """Drop the backslash of each backslash escape (before ASCII punctuation)."""
    if "\\" not in s:
        return s
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and s[i + 1:i + 2] in ASCII_PUNCT:
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _is_punct(ch: str) -> bool:
    if ch < "\x80":
        return ch in ASCII_PUNCT
    return unicodedata.category(ch)[0] in "PS"


class _Delim:
    """A run of * or _ that may open or close emphasis; it renders as the closing
    tags it took, its unused characters, then the opening tags it took."""
    __slots__ = ("ch", "n", "orig", "can_open", "can_close", "prev", "next", "opens", "closes")

    def __init__(self, ch: str, n: int, can_open: bool, can_close: bool):
        self.ch, self.n, self.orig, self.can_open, self.can_close = ch, n, n, can_open, can_close
        self.prev = self.next = None
        self.opens: list[str] = []
        self.closes: list[str] = []

    def __str__(self) -> str:
        return "".join(self.closes) + self.ch * self.n + "".join(reversed(self.opens))


class _Bracket:
    """An open [ or ![ waiting for its ]."""
    __slots__ = ("node", "src", "image", "bottom")

    def __init__(self, node: int, src: int, image: bool, bottom):
        self.node, self.src, self.image, self.bottom = node, src, image, bottom


class _InlineParser:
    """CommonMark inline parsing of one block's text into HTML."""

    def __init__(self, text: str, refs: dict):
        self.text, self.refs = text, refs
        self.nodes: list = []
        self.head = self.tail = None
        self.brackets: list[_Bracket] = []
        self.floor = 0  # [ openers below this index are inactive (no links inside links)
        self.no_closer: set[str] = set()
        self.last_close: dict[str, int] = {}
        self.urls: dict[int, int] = {}
        for m in _BARE_URL.finditer(text):
            self.urls[m.start()] = m.start() + len(_trim_url(m.group()))
        self.url_starts = sorted(self.urls)
        self.ticks: dict[int, list[int]] = {}
        for m in re.finditer(r"`+", text):
            self.ticks.setdefault(len(m.group()), []).append(m.start())

    def _push_delim(self, d: _Delim):
        self.nodes.append(d)
        d.prev = self.tail
        if self.tail is not None:
            self.tail.next = d
        else:
            self.head = d
        self.tail = d

    def _unlink(self, d: _Delim):
        if d.prev is not None:
            d.prev.next = d.next
        else:
            self.head = d.next
        if d.next is not None:
            d.next.prev = d.prev
        else:
            self.tail = d.prev

    def _emphasis(self, bottom):
        """Match emphasis delimiters above bottom (CommonMark "process emphasis"),
        then drop them from the delimiter list."""
        c = bottom.next if bottom is not None else self.head
        openers_bottom: dict = {}
        while c is not None:
            if not c.can_close:
                c = c.next
                continue
            key = (c.ch, c.can_open, c.orig % 3)
            stop = openers_bottom.get(key, bottom)
            o = c.prev
            while o is not None and o is not bottom and o is not stop:
                if o.ch == c.ch and o.can_open and not (
                        (o.can_close or c.can_open) and (o.orig + c.orig) % 3 == 0 and (o.orig % 3 or c.orig % 3)):
                    break
                o = o.prev
            else:
                o = None
            if o is None:
                openers_bottom[key] = c.prev
                nxt = c.next
                if not c.can_open:
                    self._unlink(c)
                c = nxt
                continue
            use = 2 if o.n >= 2 and c.n >= 2 else 1
            tag = "strong" if use == 2 else "em"
            o.n -= use
            c.n -= use
            o.opens.append(f"<{tag}>")
            c.closes.append(f"</{tag}>")
            o.next, c.prev = c, o  # delimiters between them become literal text
            if o.n == 0:
                self._unlink(o)
            if c.n == 0:
                nxt = c.next
                self._unlink(c)
                c = nxt
        if bottom is not None:
            bottom.next = None
            self.tail = bottom
        else:
            self.head = self.tail = None

    def _closer(self, closer: str, start: int) -> int:
        """Index of closer at or after start, remembering a miss: a later start misses too."""
        if closer in self.no_closer:
            return -1
        j = self.text.find(closer, start)
        if j < 0:
            self.no_closer.add(closer)
        return j

    def _html(self, i: int) -> int:
        """Append raw inline HTML or an autolink at text[i] == '<'; return its end, or -1."""
        t = self.text
        m = _AUTOLINK.match(t, i) or _EMAIL.match(t, i)
        if m:
            url = m.group(1)
            href = ("mailto:" + url) if m.re is _EMAIL else url
            self.nodes.append(f'<a href="{_attr(href)}">{_esc_text(url)}</a>')
            return m.end()
        for opener, closer, skip in (("<!--", "-->", 2), ("<?", "?>", 2), ("<![CDATA[", "]]>", 9)):
            if t.startswith(opener, i):
                j = self._closer(closer, i + skip)
                if j < 0:
                    return -1
                self.nodes.append(t[i:j + len(closer)])
                return j + len(closer)
        if t.startswith("<!", i) and t[i + 2:i + 3].isalpha():
            j = self._closer(">", i)
            if j < 0:
                return -1
            self.nodes.append(t[i:j + 1])
            return j + 1
        m = _OPEN_TAG.match(t, i) or _CLOSE_TAG.match(t, i)
        if m:
            raw = _RAW_INLINE.match(t, i)
            if raw and self._last_close(raw.group(1).lower()) < m.end():
                return -1  # unclosed in this paragraph, it would hide the rest of the page: keep it as text
            self.nodes.append(m.group())
            return m.end()
        return -1

    def _last_close(self, tag: str) -> int:
        """Index of the last </tag in the text (any case), or -1; cached per tag."""
        if tag not in self.last_close:
            self.last_close[tag] = self.text.lower().rfind("</" + tag)
        return self.last_close[tag]

    def _link_tail(self, i: int) -> tuple[int, str, str | None] | None:
        """Parse (dest "title") at text[i] == '('; return (end, dest, title) or None."""
        t, n = self.text, len(self.text)
        j = i + 1
        while j < n and t[j] in " \t\n":
            j += 1
        if j < n and t[j] == "<":
            k = j + 1
            while k < n and t[k] not in "<>\n":
                k += 2 if t[k] == "\\" else 1
            if k >= n or t[k] != ">":
                return None
            dest, j = t[j + 1:k], k + 1
        else:
            k = _DEST.match(t, j).end()
            if k < n and t[k] == "(":
                return None  # unbalanced or deeper than 3
            dest, j = t[j:k], k
        title = None
        sp = j
        while j < n and t[j] in " \t\n":
            j += 1
        if j < n and t[j] in "\"'(" and j > sp:
            close = ")" if t[j] == "(" else t[j]
            k = j + 1
            while k < n and t[k] != close:
                if close == ")" and t[k] == "(":
                    return None
                k += 2 if t[k] == "\\" else 1
            if k >= n:
                return None
            title, j = t[j + 1:k], k + 1
            while j < n and t[j] in " \t\n":
                j += 1
        if j >= n or t[j] != ")":
            return None
        return j + 1, _unescape(dest), (_unescape(title) if title is not None else None)

    def _close_bracket(self, i: int) -> int:
        """Handle ']' at text[i]; return the index after what it consumed."""
        t = self.text
        if not self.brackets:
            self.nodes.append("]")
            return i + 1
        b = self.brackets[-1]
        if not b.image and len(self.brackets) - 1 < self.floor:
            self.brackets.pop()
            self.nodes.append("]")
            return i + 1
        found, end = None, i + 1
        if t[i + 1:i + 2] == "(":
            tail = self._link_tail(i + 1)
            if tail:
                end, found = tail[0], (tail[1], tail[2])
        if found is None:
            label = t[b.src:i] if i - b.src <= 999 else "["  # longer than any label; slicing it per ] is quadratic
            k = t.find("]", i + 2, i + 1002) if t[i + 1:i + 2] == "[" else -1
            if k > i + 2:  # full reference [text][label]
                label, end = t[i + 2:k], k + 1
            elif k == i + 2:  # collapsed [text][]
                end = k + 1
            if label.strip() and len(label) <= 999 and "[" not in label:
                found = self.refs.get(_norm_label(label))
            if found is None:
                end = i + 1
        self.brackets.pop()
        if found is None:
            self.nodes.append("]")
            return i + 1
        self._emphasis(b.bottom)
        inner = "".join(str(x) for x in self.nodes[b.node + 1:])
        del self.nodes[b.node:]
        dest, title = found
        title_attr = f' title="{_attr(title)}"' if title is not None else ""
        if b.image:
            alt = re.sub(r"<[^>]*>", "", inner).replace('"', "&quot;")
            self.nodes.append(f'<img src="{_attr(dest)}" alt="{alt}"{title_attr}>')
        else:
            self.nodes.append(f'<a href="{_attr(dest)}"{title_attr}>{inner}</a>')
            self.floor = len(self.brackets)
        self.floor = min(self.floor, len(self.brackets))
        return end

    def parse(self) -> str:
        """Render the whole text; see the module docstring for what is recognised."""
        t, n, i = self.text, len(self.text), 0
        nodes = self.nodes
        while i < n:
            if i in self.urls and not self.brackets:  # inside link text or image alt it stays text
                url = t[i:self.urls[i]]
                nodes.append(f'<a href="{_attr(url)}">{_esc_text(url)}</a>')
                i = self.urls[i]
                continue
            k = bisect_right(self.url_starts, i)
            m = _PLAIN.match(t, i, self.url_starts[k] if k < len(self.url_starts) else n)
            if m:
                nodes.append(m.group().replace(">", "&gt;"))
                i = m.end()
                continue
            ch = t[i]
            if ch == "\\":
                nxt = t[i + 1:i + 2]
                if nxt in ASCII_PUNCT:
                    nodes.append(_esc_code(nxt))
                    i += 2
                elif nxt == "\n":
                    nodes.append("<br>\n")
                    i += 2
                else:
                    nodes.append("\\")
                    i += 1
            elif ch == "`":
                k = i
                while k < n and t[k] == "`":
                    k += 1
                runs = self.ticks.get(k - i, [])
                pos = bisect_right(runs, i)
                if pos < len(runs):
                    code = t[k:runs[pos]].replace("\n", " ")
                    if len(code) > 2 and code[0] == code[-1] == " " and code.strip(" "):
                        code = code[1:-1]
                    nodes.append("<code>" + _esc_code(code) + "</code>")
                    i = runs[pos] + (k - i)
                else:
                    nodes.append(t[i:k])
                    i = k
            elif ch == "&":
                m = _ENTITY.match(t, i)
                if m and (m.group(1) is None or m.group(1) + ";" in html5):
                    nodes.append(m.group())
                    i = m.end()
                else:
                    nodes.append("&amp;")
                    i += 1
            elif ch == "<":
                end = self._html(i)
                if end < 0:
                    nodes.append("&lt;")
                    i += 1
                else:
                    i = end
            elif ch == "!" and t[i + 1:i + 2] == "[":
                self.brackets.append(_Bracket(len(nodes), i + 2, True, self.tail))
                nodes.append("![")
                i += 2
            elif ch == "!":
                nodes.append("!")
                i += 1
            elif ch == "[":
                self.brackets.append(_Bracket(len(nodes), i + 1, False, self.tail))
                nodes.append("[")
                i += 1
            elif ch == "]":
                i = self._close_bracket(i)
            elif ch in "*_":
                k = i
                while k < n and t[k] == ch:
                    k += 1
                before, after = (t[i - 1] if i else "\n"), (t[k] if k < n else "\n")
                bws, aws = before.isspace(), after.isspace()
                bp, ap = _is_punct(before), _is_punct(after)
                left = not aws and (not ap or bws or bp)
                right = not bws and (not bp or aws or ap)
                if ch == "*":
                    can_open, can_close = left, right
                else:
                    can_open, can_close = left and (not right or bp), right and (not left or ap)
                self._push_delim(_Delim(ch, k - i, can_open, can_close))
                i = k
            else:  # newline: a hard break after two or more trailing spaces
                prev = nodes[-1] if nodes and isinstance(nodes[-1], str) else ""
                bare = prev.rstrip(" ")
                if prev:
                    nodes[-1] = bare
                nodes.append("<br>\n" if len(prev) - len(bare) >= 2 else "\n")
                i += 1
                while i < n and t[i] == " ":
                    i += 1
        self._emphasis(None)
        return "".join(str(x) for x in nodes)


def _trim_url(url: str) -> str:
    """A bare URL without its trailing sentence punctuation or an unbalanced ")", as GFM
    reads an extended autolink."""
    opens, closes = url.count("("), url.count(")")
    while url and (url[-1] in ".,;:!?" or (url[-1] == ")" and closes > opens)):
        closes -= url[-1] == ")"
        url = url[:-1]
    return url


def _inline(text: str, refs: dict) -> str:
    """Render one block's inline Markdown as HTML."""
    return _InlineParser(text, refs).parse()
