"""
html_safety.py: keep a page built from Markdown from running anything when it is
opened. markdown_page.py runs defang over the whole page body. Standard library only and Python 3.9 compatible.

A URL whose scheme is not http, https or mailto gets an "unsafe:" prefix (it stays
comparable: verify_translation.py drops the prefix again), and raw HTML start tags
lose on* handlers and srcdoc. The page also carries a Content-Security-Policy
(markdown_page.py), which is the guarantee; this is a best-effort second layer.
"""

from __future__ import annotations

import html
import re

_SCHEME = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*):")
_SAFE_SCHEMES = {"http", "https", "mailto", "unsafe"}  # "unsafe": already prefixed
_URL_STRIP = re.compile(r"[\t\n\r]")
_C0_AND_SPACE = "".join(map(chr, range(33)))
_REF = re.compile(r"&(?:#[xX]0*([0-9a-fA-F]{1,6})(?![0-9a-fA-F])|#0*([0-9]{1,7})(?![0-9])|[A-Za-z][A-Za-z0-9]{0,31});?")
_SEP = "\t\n\f\r "
_URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href", "data", "poster", "background"}


def _decode_ref(m: re.Match) -> str:
    """One character reference as a browser decodes it in an attribute; out-of-range
    code points become U+FFFD."""
    if m.group(1) or m.group(2):
        cp = int(m.group(1), 16) if m.group(1) else int(m.group(2))
        return chr(cp) if 0 < cp <= 0x10FFFF else "\ufffd"
    return html.unescape(m.group())


def safe_url(url: str) -> str:
    """url, with "unsafe:" in front if its scheme is not http, https or mailto. The
    scheme is read as a browser reads it (references decoded, with or without the
    semicolon; tabs and newlines removed; leading control characters and spaces
    dropped), so &#106;avascript: and java<TAB>script: are caught; relative URLs and
    fragments pass. Decoding more than the browser would only adds a harmless prefix."""
    seen = _URL_STRIP.sub("", _REF.sub(_decode_ref, url)).lstrip(_C0_AND_SPACE)
    m = _SCHEME.match(seen)
    return "unsafe:" + url if m and m.group(1).lower() not in _SAFE_SCHEMES else url


def _attr_end(s: str, i: int) -> tuple[int, int, int, int]:
    """Read one attribute at s[i] as the HTML tokenizer does; return (name end,
    value start, value end, attribute end), the value without its quotes. Without
    a value, value start is -1."""
    n, j = len(s), i + 1  # the first character may be "=", as in the spec
    while j < n and s[j] not in _SEP and s[j] not in "/>=":
        j += 1
    name_end = k = j
    while k < n and s[k] in _SEP:
        k += 1
    if k >= n or s[k] != "=":
        return name_end, -1, -1, name_end
    k += 1
    while k < n and s[k] in _SEP:
        k += 1
    if k < n and s[k] in "\"'":
        close = s.find(s[k], k + 1)
        return (name_end, k + 1, n, n) if close < 0 else (name_end, k + 1, close, close + 1)
    v = k
    while k < n and s[k] not in _SEP and s[k] != ">":
        k += 1
    return name_end, v, k, k


def defang(s: str) -> str:
    """HTML with every start tag's on* handlers and srcdoc removed and each URL
    attribute (href, src, action and the like) passed through safe_url, in linear
    time. Tags are read as the HTML tokenizer reads them ("/" separates attributes,
    a quoted value needs no space after it). Context (comments, script or textarea
    bodies) is not tracked; a quoted value that holds "<" is defanged too. Best
    effort: a tag that straddles the end of a quoted value seen in another context
    can keep its handler, so the page's CSP is the guarantee, not this."""
    out, i, n = [], 0, len(s)
    while True:
        lt = s.find("<", i)
        while lt >= 0 and not (lt + 1 < n and s[lt + 1].isascii() and s[lt + 1].isalpha()):
            lt = s.find("<", lt + 1)
        if lt < 0:
            out.append(s[i:])
            return "".join(out)
        j = lt + 1
        while j < n and s[j] not in _SEP and s[j] not in "/>":
            j += 1
        out.append(s[i:j])
        while j < n and s[j] != ">":
            if s[j] in _SEP or s[j] == "/":
                out.append(s[j])
                j += 1
                continue
            name_end, v, v_end, end = _attr_end(s, j)
            name = s[j:name_end].lower()
            if not (name.startswith("on") or name == "srcdoc"):
                value = s[v:v_end] if v >= 0 else ""
                if name in _URL_ATTRS and v >= 0:
                    value = safe_url(value)
                if "<" in value and s[v - 1:v] in ("'", '"'):  # quoted only, so nesting stops at two levels
                    value = defang(value)
                out.append(s[j:v] + value + s[v_end:end] if v >= 0 else s[j:end])
            j = end
        i = j
