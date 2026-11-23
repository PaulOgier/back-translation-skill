"""Tests for markdown_page.py and the Markdown wiring in verify_translation.py and
export_blocks.py. Run from the repo root: python3 -m unittest discover -s tests"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "back-translation" / "scripts"))
import export_blocks  # noqa: E402
import translation_lang as tl  # noqa: E402
import verify_translation as vt  # noqa: E402
from markdown_page import markdown_to_html  # noqa: E402

DIR = Path(__file__).parent / "fixtures" / "markdown"
PT = vt.load_lang("pt")


def blocks(page: str) -> list[tuple[str, str]]:
    """The (tag, text) blocks of <main> as the checker reads them."""
    p = vt.parse_page(page)
    return [(b.tag, tl.norm_space(tl.plain(b.text))) for b in p.region_blocks("main")]


def md_main(md: str) -> str:
    """The HTML between <main> and </main> of a converted page."""
    out = markdown_to_html(md)
    return out[out.index("<main>") + 6:out.index("</main>")].strip()


def assert_linear(test, make, n: int, label: str) -> None:
    """Time markdown_to_html on inputs of size n and 2n. Linear code roughly doubles and the
    quadratic loops these tests guard against roughly quadruple, so a ratio under 3 passes
    on a slow CI runner and a fast laptop alike, where a wall-clock limit does not."""
    times = []
    for size in (n, 2 * n):
        text = make(size)
        start = time.perf_counter()
        markdown_to_html(text)
        times.append(time.perf_counter() - start)
    test.assertLess(times[1], 3 * times[0] + 0.05, f"{label}: {times[0]:.3f}s at n, {times[1]:.3f}s at 2n")


class Equivalence(unittest.TestCase):
    """Each construct parses into the blocks a hand-written page has."""

    def same(self, md: str, html: str):
        self.assertEqual(blocks(markdown_to_html(md)), blocks(f"<html><main>{html}</main></html>"))

    def test_headings(self):
        self.same("# One\n\n## Two ##\n\nThree\n=====\n\nFour\n---", "<h1>One</h1><h2>Two</h2><h1>Three</h1><h2>Four</h2>")
        self.assertEqual(md_main("#5 bolts"), "<p>\n#5 bolts\n</p>")

    def test_paragraphs_and_breaks(self):
        self.same("one\ntwo\n\nthree", "<p>one\ntwo</p><p>three</p>")
        self.assertIn("a<br>\nb", md_main("a  \nb"))
        self.assertIn("a<br>\nb", md_main("a\\\nb"))

    def test_lists(self):
        self.same("- a\n- b\n  - c\n- d", "<ul><li>a</li><li>b<ul><li>c</li></ul></li><li>d</li></ul>")
        self.same("1. a\n2. b", "<ol><li>a</li><li>b</li></ol>")
        self.same("- a\n\n- b", "<ul><li><p>a</p></li><li><p>b</p></li></ul>")
        self.assertIn('<ol start="3">', md_main("3. a\n4. b"))
        self.same("- a\nlazy", "<ul><li>a lazy</li></ul>")
        self.same("* a\n+ b", "<ul><li>a</li></ul><ul><li>b</li></ul>")

    def test_blockquote(self):
        self.same("> a\n> b\nlazy\n\n> c", "<blockquote><p>a b lazy</p></blockquote><blockquote><p>c</p></blockquote>")
        self.same("> - x\n> - y", "<blockquote><ul><li>x</li><li>y</li></ul></blockquote>")

    def test_code(self):
        self.assertEqual(md_main("```py\nx < 1 [2]\n```"), '<pre><code class="language-py">x &lt; 1 [2]\n</code></pre>')
        self.assertEqual(md_main("~~~~\n```\n~~~~"), "<pre><code>```\n</code></pre>")
        self.assertEqual(md_main("    a\n\n    b"), "<pre><code>a\n\nb\n</code></pre>")
        self.assertIn("<code>a ` b</code>", md_main("``a ` b``"))
        self.assertIn("<code>`x`</code>", md_main("`` `x` ``"))
        self.assertIn("`no", md_main("`no close"))
        p = vt.parse_page(markdown_to_html("Run `pwgen -s 15` now.\n\n```\nls -l\n```"))
        self.assertEqual(p.verbatim, ["pwgen -s 15", "ls -l"])

    def test_emphasis(self):
        self.assertIn("<em>a</em> <strong>b</strong> <em><strong>c</strong></em>", md_main("*a* **b** ***c***"))
        self.assertIn("<em>x</em>", md_main("_x_"))
        self.assertIn("snake_case_name", md_main("snake_case_name"))
        self.assertIn("2 * 3 * 4", md_main("2 * 3 * 4"))

    def test_links_and_images(self):
        out = md_main('[a](https://example.com/x "T") <https://example.com/y> <me@example.com> ![alt *t*](/i.png)')
        self.assertIn('<a href="https://example.com/x" title="T">a</a>', out)
        self.assertIn('<a href="https://example.com/y">https://example.com/y</a>', out)
        self.assertIn('<a href="mailto:me@example.com">me@example.com</a>', out)
        self.assertIn('<img src="/i.png" alt="alt t">', out)
        p = vt.parse_page(markdown_to_html("[a](/one) and [b][ref], [Ref][], [ref]\n\n[ref]: /two 'T'"))
        self.assertEqual(p.hrefs, ["/one", "/two", "/two", "/two"])
        self.assertIn("[not a link]", md_main("[not a link]"))
        self.assertIn('href="/a(b)"', md_main("[x](/a(b))"))
        self.assertIn('<a href="/u">a <img src="/i" alt="b"></a>', md_main("[a ![b](/i)](/u)"))

    def test_citation_markers_stay_markers(self):
        p = vt.parse_page(markdown_to_html("Must be 15 characters[1][2]."))
        self.assertEqual(tl.MARKER_RE.findall(p.region_text("main")), ["1", "2"])

    def test_table(self):
        self.same("| a | b \\| c |\n|:--|--:|\n| 1 | `2` |\n| 3 |",
                  "<table><tr><th>a</th><th>b | c</th></tr><tr><td>1</td><td><code>2</code></td></tr>"
                  "<tr><td>3</td><td></td></tr></table>")

    def test_hr_entities_escapes(self):
        self.assertEqual(md_main("***"), "<hr>")
        out = md_main("&copy; &#169; &bogus; a & b < c > d \\*x\\* \\[1\\]")
        self.assertIn("&copy; &#169; &amp;bogus; a &amp; b &lt; c &gt; d *x* [1]", out)
        self.assertEqual(blocks(markdown_to_html("<b> is bold & 1 < 2"))[0][1], "is bold & 1 < 2")
        self.assertEqual(blocks(markdown_to_html("1 < 2 and 3 > 2"))[0][1], "1 < 2 and 3 > 2")

    def test_raw_html_and_jsx(self):
        self.assertEqual(md_main('<div class="x">\n*raw*\n</div>'), '<div class="x">\n*raw*\n</div>')
        self.assertEqual(md_main('<Callout type="warning">\nBe **careful**.\n</Callout>'),
                         '<Callout type="warning">\n<p>\nBe <strong>careful</strong>.\n</p>\n</Callout>')
        self.assertIn('a <span data-x="1">b</span> <!-- c -->', md_main('a <span data-x="1">b</span> <!-- c -->'))
        self.same('<aside data-i18n-skip>Note</aside>\n\nText', "<aside data-i18n-skip>Note</aside><p>Text</p>")

    def test_front_matter(self):
        p = vt.parse_page(markdown_to_html("---\ntitle: 'It''s here'\ndescription: \"A \\\"b\\\"\"\n"
                                           "lang: pt-BR\nother: x\n---\nBody"))
        self.assertEqual((p.head["title"], p.head["description"], p.html_lang), ("It's here", 'A "b"', "pt-BR"))
        self.assertEqual(vt.parse_page(markdown_to_html("---\nlang: de\n---\nx", lang="fr")).html_lang, "fr")
        self.assertEqual(blocks(markdown_to_html("---\nnot closed\n\nx")), blocks(markdown_to_html("---\nnot closed\n\nx")))
        self.assertEqual(vt.parse_page(markdown_to_html("Body")).mains, 1)

    def test_pathological_lines_are_fast(self):
        for unit in ("*", "[", "`", "<", "_", "*a", "[a](", "](x (", '<a x="', "<!--", "&#", "a  ", "![", "\\",
                     "<a b ", "**a*", "[]", "> ", "- ", "1. ", "[a](\\(", "[](", "*a_", "_a*", "https://", "a)",
                     "<script>", "- [x] "):
            assert_linear(self, lambda n, u=unit: u * (n // len(u)), 250_000, repr(unit))
            self.assertEqual(vt.parse_page(markdown_to_html(unit * 1000)).mains, 1)


class ReviewRound4(unittest.TestCase):
    """Fourth-review false passes in the converter, each with its positive control."""

    def nosidecar(self, en_md: str, pt_md: str) -> tuple[list[str], list[str]]:
        found = vt.check_pages(markdown_to_html(en_md, default_lang="en"), markdown_to_html(pt_md, default_lang="pt"), PT)
        return ([f["message"] for f in found if f["level"] == "blocker"],
                [f["message"] for f in found if f["level"] == "warning" and f["check"] != "back-translation"])

    def test_inline_script_mention_is_text(self):
        en = "Put the snippet in the <script> tag of the page.\n\nYou must not wait more than 5 days.\n"
        pt = "Coloque o trecho na tag <script> da p\u00e1gina.\n\n"
        self.assertTrue(self.nosidecar(en, pt + "Voc\u00ea pode esperar mais de 50 dias.\n")[0])
        self.assertEqual(self.nosidecar(en, pt + "Voc\u00ea n\u00e3o deve esperar mais de 5 dias.\n"), ([], []))
        self.assertIn("&lt;style&gt;", md_main("a <style> b"))
        self.assertIn("<script>x()</script>", md_main("a <script>x()</script> b"))

    def test_unclosed_script_block_blocks(self):
        found = self.nosidecar("<script>\nx\n\nYou must wait 5 days.\n", "<script>\nx\n\nVoc\u00ea deve esperar 5 dias.\n")[0]
        self.assertIn("English: <script> never closed; everything after it is unchecked", found)

    def test_ordered_start_numbers(self):
        self.assertTrue(any("list numbers" in m for m in self.nosidecar("3. Restart now\n", "4. Reinicie agora\n")[0]))
        self.assertTrue(any("ol start=2019" in m for m in self.nosidecar("2019. That was the year.\n",
                                                                           "2018. Aquele foi o ano.\n")[0]))
        self.assertEqual(self.nosidecar("3. Restart now\n", "3. Reinicie agora\n"), ([], []))

    def test_bare_urls_are_links(self):
        self.assertTrue(any("links" in m for m in self.nosidecar("See https://example.com/a now.\n",
                                                                  "Veja https://example.com/b agora.\n")[0]))
        self.assertEqual(self.nosidecar("See https://example.com/a.\n", "Veja https://example.com/a.\n"), ([], []))
        self.assertEqual(md_main("(see https://x.example/a_(b)), `https://y.example` and <https://z.example>."),
                         '<p>\n(see <a href="https://x.example/a_(b)">https://x.example/a_(b)</a>), <code>https://y.example</code> '
                         'and <a href="https://z.example">https://z.example</a>.\n</p>')

    def test_front_matter_block_scalars_and_numbers(self):
        en = "---\ntitle: Hello\ndescription: >-\n  A long text\n  here\n---\nBody text.\n"
        pt = "---\ntitle: Ol\u00e1\ndescription: >-\n  Um texto longo\n  aqui\n---\nTexto do corpo.\n"
        self.assertEqual(vt.parse_page(markdown_to_html(en)).head["description"], "A long text here")
        self.assertEqual(vt.parse_page(markdown_to_html("---\ndescription: |\n  a\n  b\nlang: de\n---\nx")).head["description"],
                         "a\nb")
        self.assertEqual(self.nosidecar(en, pt), ([], []))
        self.assertIn("description is not translated", self.nosidecar(en, en.replace("Body text", "Texto do corpo"))[0])
        en = "---\ntitle: Hello\nsummary: Save 30% in 5 days\ndate: 2024-01-02\n---\nBody text.\n"
        pt = "---\ntitle: Ol\u00e1\nsummary: Economize {} em 5 dias\ndate: 2025-03-04\n---\nTexto do corpo.\n"
        self.assertTrue(any("x-front-matter" in m for m in self.nosidecar(en, pt.format("40%"))[0]))
        self.assertEqual(self.nosidecar(en, pt.format("30%")), ([], []))

    def test_leading_rule_is_not_front_matter(self):
        self.assertEqual(blocks(markdown_to_html("---\nIntro 5 days.\n\n---\nMore\n")),
                         [("p", "Intro 5 days."), ("p", "More"), ("#loose", "")])
        self.assertEqual(vt.parse_page(markdown_to_html("---\n\ntitle: T\n---\nx")).head["title"], "T")

    def test_task_list_state(self):
        self.assertTrue(any("checkboxes" in m for m in self.nosidecar("- [x] Enable logging\n- [ ] Disable root\n",
                                                                       "- [ ] Ative o registro\n- [x] Desative o root\n")[0]))
        self.assertEqual(self.nosidecar("- [x] Enable logging\n", "- [x] Ative o registro\n"), ([], []))
        self.same("- [x] Done\n- [ ] Open", '<ul><li><input type="checkbox" disabled checked>Done</li>'
                                           '<li><input type="checkbox" disabled>Open</li></ul>')

    def test_quadratic_scans_are_fast(self):
        for name, make, n in (("R1 blank lines after a list item", lambda n: "- a\n" + "\n" * n, 40_000),
                              ("R2 nested brackets", lambda n: "[" * n + "]" * n, 320_000),
                              ("R3 link definitions", lambda n: "".join(f"[l{i}]: /u{i}\n" for i in range(n)), 50_000)):
            with self.subTest(name):
                assert_linear(self, make, n, name)

    same = Equivalence.same


class Cli(unittest.TestCase):
    """The Markdown pair through the no-sidecar CLI, with positive controls."""
    EN, PT_MD = (DIR / "en.md").read_text(encoding="utf-8"), (DIR / "pt.md").read_text(encoding="utf-8")

    def run_cli(self, pt: str | None = None, bt: bool = True) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(DIR / "en.md", d)
            shutil.copy(DIR / "back_translation.json", d)
            (Path(d) / "pt.md").write_text(self.PT_MD if pt is None else pt, encoding="utf-8")
            extra = ["--back-translation", "back_translation.json", "--translator-model", "model-a"] if bt else []
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                code = vt.main(["--root", d, "--en", "en.md", "--tr", "pt.md", "--lang", "pt", "--json", *extra])
        return code, out.getvalue()

    def blocked(self, old: str, new: str) -> set[str]:
        self.assertIn(old, self.PT_MD)
        code, out = self.run_cli(self.PT_MD.replace(old, new))
        self.assertEqual(code, 1)
        return {f["check"] for f in json.loads(out) if f["level"] == "blocker"}

    def test_clean_pair_passes(self):
        self.assertEqual(self.run_cli(), (0, "[]\n"))

    def test_changed_number(self):
        self.assertIn("numbers", self.blocked("400 anos", "40 anos"))

    def test_swapped_table_cell(self):
        self.assertIn("numbers", self.blocked("| 8 caracteres | 2 dias |", "| 2 dias | 8 caracteres |"))

    def test_changed_link_target(self):
        self.assertIn("tokens", self.blocked("https://example.com/guide", "https://example.com/guia"))

    def test_changed_code_span(self):
        self.assertIn("tokens", self.blocked("`pwgen -s 15`", "`pwgen -s 16`"))

    def test_must_rendered_as_may(self):
        self.assertIn("normative", self.blocked("devem ter", "podem ter"))

    def test_dropped_list_item(self):
        self.assertIn("structure", self.blocked("- Troque a senha depois de um vazamento.\n", ""))

    def test_missing_lang_takes_cli_lang(self):
        self.assertEqual(self.run_cli(self.PT_MD.replace("lang: pt-BR\n", "")), (0, "[]\n"))

    def test_wrong_front_matter_lang_blocks(self):
        self.assertIn("page", self.blocked("lang: pt-BR\n", "lang: de\n"))


class Sidecar(unittest.TestCase):
    """Sidecar mode hashes the raw Markdown text, not the converted page."""

    def test_matching_raw_hash_is_not_stale(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            en_md = "---\ntitle: Passwords\nlang: en\n---\nPasswords must be 15 characters[1].\r\n"
            (root / "en.md").write_bytes(en_md.encode())
            h = hashlib.sha256(vt.read_capped(root / "en.md").encode()).hexdigest()
            self.assertNotEqual(h, hashlib.sha256(vt.read_page(root / "en.md").encode()).hexdigest())
            c, s = "Passwords must be 15 characters", "As senhas devem ter 15 caracteres"
            (root / "en.json").write_text(json.dumps({"article": {"path": "en.md", "body_sha256": h},
                                                      "claims": [{"id": 1, "claim": c}]}))
            (root / "pt.md").write_text("---\ntitle: Senhas\nlang: pt-BR\n---\n" + s + "[1].\n", encoding="utf-8")
            tr = {"lang": "pt", "source_sidecar": "en.json", "article": "pt.md", "source_body_sha256": h,
                  "translator_model": "opus", "claims": [{"id": 1, "sentence": s}],
                  "back_translation": [{"id": 1, "round": 2, "verdict": "MATCH", "model": "sonnet",
                                        "sentence_sha256": tl.sentence_sha256(s), "claim_sha256": tl.sentence_sha256(c)}]}
            (root / "t.json").write_text(json.dumps(tr))

            def run() -> tuple[int, list]:
                with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                    code = vt.main([str(root / "t.json"), "--root", d, "--json"])
                return code, json.loads(out.getvalue())

            self.assertEqual(run(), (0, []))
            (root / "en.md").write_text(en_md.replace("15", "16"), encoding="utf-8")
            code, found = run()
            self.assertEqual(code, 1)
            self.assertIn("stale", {f["check"] for f in found})


class ReadPage(unittest.TestCase):
    def test_suffixes(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("a.md", "b.MDX", "c.Markdown", "d.html", "e.php"):
                (Path(d) / name).write_text("# Hi", encoding="utf-8")
                page = vt.read_page(Path(d) / name)
                self.assertEqual("<main>" in page, name[0] in "abc", name)


class ExportBlocks(unittest.TestCase):
    def test_markdown_pair_aligns(self):
        with tempfile.TemporaryDirectory() as d:
            n = export_blocks.export(DIR / "en.md", DIR / "pt.md", Path(d))
            en = (Path(d) / "blocks_en.txt").read_text(encoding="utf-8").splitlines()
            tr = (Path(d) / "translation-only" / "blocks_tr.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(n, 18)
        self.assertEqual(en[0], "[title] Choosing a strong password")
        self.assertEqual([x.split(" ", 2)[:2] for x in en[2:]], [x.split(" ", 2)[:2] for x in tr[2:]])
        self.assertEqual(en[3], "B2 <p> Passwords must be at least 15 characters long[1].")
        self.assertEqual(tr[3], "B2 <p> As senhas devem ter pelo menos 15 caracteres[1].")


if __name__ == "__main__":
    unittest.main()
