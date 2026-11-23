"""Regression tests for review findings, each with a positive control and a clean case.
Run from the repo root: python3 -m unittest discover -s tests"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "back-translation" / "scripts"))
import export_blocks  # noqa: E402
import verify_translation as vt  # noqa: E402
from block_checks import block_sha256, validate_back_translation  # noqa: E402
from markdown_page import markdown_to_html  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "nosidecar"
PT = vt.load_lang("pt")
UNCLOSED = "unclosed comment or tag; everything after it is unchecked"


def md_main(md: str) -> str:
    """The HTML between <main> and </main> of a converted page."""
    out = markdown_to_html(md)
    return out[out.index("<main>") + 6:out.index("</main>")].strip()


def md_pair(en_md: str, pt_md: str) -> tuple[list[str], list[str]]:
    """(blockers, warnings other than back-translation) of a Markdown pair in no-sidecar mode."""
    return html_pair(markdown_to_html(en_md, default_lang="en"), markdown_to_html(pt_md, default_lang="pt"))


def html_pair(en: str, pt: str) -> tuple[list[str], list[str]]:
    """(blockers, warnings other than back-translation) of an HTML pair in no-sidecar mode."""
    found = vt.check_pages(en, pt, PT)
    return ([f["message"] for f in found if f["level"] == "blocker"],
            [f["message"] for f in found if f["level"] == "warning" and f["check"] != "back-translation"])


def page(lang: str, body: str) -> str:
    """A minimal page with one <main>."""
    return f'<html lang="{lang}"><body><main>{body}</main></body></html>'


class C1EmptyLastListItem(unittest.TestCase):
    def test_empty_last_item_converts(self):
        for md in ("- a\n-", "- a\n- \n", "1.\n", "> -\n"):
            with self.subTest(md=md):
                self.assertIn("<li>", md_main(md))

    def test_pair_still_checked(self):
        self.assertTrue(md_pair("- Wait 5 days\n-", "- Espere 6 dias\n-")[0])
        self.assertEqual(md_pair("- Wait 5 days\n-", "- Espere 5 dias\n-"), ([], []))


class C2FrontMatterKeys(unittest.TestCase):
    EN = "---\ntitle: Hello\n{}---\nBody text.\n"
    PT_MD = "---\ntitle: Olá\n{}---\nTexto do corpo.\n"

    def pair(self, en_extra: str, pt_extra: str):
        return md_pair(self.EN.format(en_extra), self.PT_MD.format(pt_extra))

    def test_non_prose_and_one_sided_keys_pass(self):
        cases = {
            "translation-only translation_of": ("", "translation_of: /blog/top-10-tips\n"),
            "English-only aliases": ("aliases: [/2019/old-hello]\n", ""),
            "last_modified_at differs": ("last_modified_at: 2024-01-02\n", "last_modified_at: 2025-03-04\n"),
            "published datetime differs": ("published: 2024-01-02T10:00:00Z\n", "published: '2025-03-04 09:00:00 +02:00'\n"),
            "same keys, other order": ("battery: 5 hours\nrange: 7 hours\n", "range: 7 horas\nbattery: 5 horas\n"),
            "translation-only weight": ("", "weight: 20\n"),
            "translated_by": ("", "translated_by: gpt-5\n"),
            "both sides weight and slug": ("weight: 1\nslug: top-10\n", "weight: 2\nslug: dez-melhores\n"),
            "source_ key": ("", "source_rev: 42\n"),
        }
        for name, (en, pt) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.pair(en, pt), ([], []))

    def test_shared_prose_key_still_compared(self):
        found = self.pair("summary: Save 30% in 5 days\n", "summary: Economize 40% em 6 dias\n")[0]
        self.assertTrue(any("x-front-matter" in m for m in found), found)
        found = self.pair("battery: 5 hours\nrange: 7 hours\n", "range: 7 horas\nbattery: 6 horas\n")[0]
        self.assertTrue(any("battery" in m for m in found), found)
        self.assertEqual(self.pair("summary: Save 30% in 5 days\n", "summary: Economize 30% em 5 dias\n"), ([], []))

    def test_english_key_dropped_or_renamed_in_translation_blocks(self):
        for name, pt in {"dropped": "", "renamed": "resumo: Custa 8\n", "case changed": "Summary: Custa 8\n",
                         "date instead": "summary: 2026-01-01\n"}.items():
            with self.subTest(name):
                self.assertTrue(self.pair("summary: Costs 5 users\n", pt)[0])


class C3InlineTagsInLooseText(unittest.TestCase):
    def test_inline_level_tags_do_not_split_runs(self):
        cases = [("<div>CO<sub>2</sub> emissions fell</div>", "<div>As emissões de CO<sub>2</sub></div>")]
        for t in ("sup", "del", "ins", "label", "button"):
            cases.append((f"<div>Read <{t}>x</{t}> now</div>", f"<div>Leia agora <{t}>x</{t}></div>"))
        cases.append(("<div>Line one<br>line two</div>", "<div>Linha um e dois<br></div>"))
        cases.append(('<div>See <img src="a.png"> here</div>', '<div>Veja aqui <img src="a.png"></div>'))
        for en, pt in cases:
            with self.subTest(en):
                self.assertEqual(html_pair(page("en", en), page("pt", pt)), ([], []))

    def test_dropped_div_still_blocks(self):
        found = html_pair(page("en", "<div>First part.</div><div>Second part.</div>"),
                          page("pt", "<div>Primeira parte. Segunda parte.</div>"))[0]
        self.assertTrue(any("runs of text outside block tags" in m for m in found), found)
        self.assertEqual(vt.parse_page(page("en", "<div>CO<sub>2</sub> fell</div>")).loose_runs["main"], 1)


class C4BareUrls(unittest.TestCase):
    def test_link_and_image_syntax_kept(self):
        self.assertEqual(md_main("[https://example.com/a](https://example.com/a)"),
                         '<p>\n<a href="https://example.com/a">https://example.com/a</a>\n</p>')
        self.assertEqual(md_main("![see https://example.com/a](p.png)"),
                         '<p>\n<img src="p.png" alt="see https://example.com/a">\n</p>')

    def test_trailing_punctuation_left_out(self):
        for s in ("«https://example.com/a»", "„https://example.com/a“", '"https://example.com/a"',
                  "https://example.com/a。）了解详情", "https://example.com/a's page",
                  "https://example.com/a’s page", "(https://example.com/a)."):
            with self.subTest(s):
                self.assertIn('<a href="https://example.com/a">https://example.com/a</a>', md_main(s))

    def test_bare_url_still_compared(self):
        self.assertTrue(any("links" in m for m in md_pair("See «https://example.com/a».\n",
                                                          "Veja «https://example.com/b».\n")[0]))
        self.assertEqual(md_pair("See «https://example.com/a».\n", "Veja «https://example.com/a».\n"),
                         ([], []))


class C5HashBinding(unittest.TestCase):
    EN, PT_HTML = ((FIX / f).read_text(encoding="utf-8") for f in ("en.html", "pt.html"))
    BT = json.loads((FIX / "back_translation.json").read_text(encoding="utf-8"))

    def blockers(self, en=None, pt=None, bt=None):
        found = vt.check_pages(en or self.EN, pt or self.PT_HTML, PT, self.BT if bt is None else bt, "model-a")
        return [f["message"] for f in found if f["level"] == "blocker"]

    def test_clean_and_export(self):
        self.assertEqual(self.blockers(), [])
        with tempfile.TemporaryDirectory() as d:
            export_blocks.export(FIX / "en.html", FIX / "pt.html", Path(d))
            hashes = json.loads((Path(d) / "blocks_sha256.json").read_text(encoding="utf-8"))
        self.assertIn("title", hashes)
        self.assertIn("description", hashes)
        self.assertEqual(hashes["title"], block_sha256("Choosing a strong password", "Como escolher uma senha forte"))

    def test_english_edited_after_round_blocks(self):
        old = "store them."
        self.assertIn(old, self.EN)
        self.assertEqual(self.blockers(en=self.EN.replace(old, "store them safely.")),
                         ["B3 changed since it was back-translated; re-export and redo it"])

    def test_title_and_description_bound(self):
        for old, new, name in (("Como escolher uma senha forte</title>", "Como escolher uma boa senha</title>", "title"),
                               ("um bom começo.", "um ótimo começo.", "description")):
            with self.subTest(name):
                self.assertIn(old, self.PT_HTML)
                self.assertEqual(self.blockers(pt=self.PT_HTML.replace(old, new)),
                                 [f"{name} changed since it was back-translated; re-export and redo it"])
        self.assertEqual(self.blockers(bt=[r for r in self.BT if r["block"] != "title"]),
                         ["title: no back-translation recorded"])
        rounds = [dict(r, round=1) if r["block"] == "description" else r for r in self.BT]
        self.assertEqual(self.blockers(bt=rounds), ["description: latest is round 1 MATCH; needs round 2 MATCH"])

    def test_sha256_case_insensitive(self):
        self.assertEqual(self.blockers(bt=[dict(r, sha256=r["sha256"].upper()) for r in self.BT]), [])

    def test_record_shape(self):
        validate_back_translation([{"block": "title", "round": 2, "verdict": "MATCH", "model": "m", "sha256": "x"}])
        with self.assertRaises(ValueError):
            validate_back_translation([{"block": "summary", "round": 2, "verdict": "MATCH", "model": "m", "sha256": "x"}])


class LooseRunsMainOnly(unittest.TestCase):
    def test_chrome_outside_main_may_differ(self):
        en = '<html lang="en"><body><div>Skip to content</div><div>Home</div><main><p>Body 5.</p></main></body></html>'
        pt = '<html lang="pt"><body><div>Início</div><main><p>Corpo 5.</p></main></body></html>'
        self.assertEqual(html_pair(en, pt)[0], [])

    def test_dropped_div_inside_main_still_blocks(self):
        en = page("en", "<div>First paragraph.</div><div>Second says 5 things.</div>")
        pt = page("pt", "<div>Primeiro parágrafo e 5 coisas.</div>")
        self.assertTrue(any("runs of text" in m for m in html_pair(en, pt)[0]))


class CannotAsInability(unittest.TestCase):
    EN = "<p>Learners who can't remember their password ask for a new one.</p>"

    def test_plain_negation_for_cannot_warns(self):
        pt = "<p>Os alunos que não lembram a senha pedem uma nova.</p>"
        blockers, warnings = html_pair(page("en", self.EN), page("pt", pt))
        self.assertEqual(blockers, [])
        self.assertTrue(any("can't/cannot" in w for w in warnings), warnings)

    def test_dropped_negation_still_blocks(self):
        pt = "<p>Os alunos que lembram a senha pedem uma nova.</p>"
        self.assertTrue(any("modals" in m for m in html_pair(page("en", self.EN), page("pt", pt))[0]))

    def test_dropped_prohibition_beside_another_negation_blocks(self):
        for en, pt in (("You cannot share the file. It is not encrypted.", "Compartilhe o arquivo. Ele não é criptografado."),
                       ("You cannot share the file and you cannot copy it.", "Não compartilhe o arquivo; copie à vontade.")):
            with self.subTest(en):
                self.assertTrue(any("modals" in m for m in html_pair(page("en", f"<p>{en}</p>"), page("pt", f"<p>{pt}</p>"))[0]))

    def test_modal_kept_passes(self):
        pt = "<p>Os alunos que não podem lembrar a senha pedem uma nova.</p>"
        self.assertEqual(html_pair(page("en", self.EN), page("pt", pt)), ([], []))


class C6UnclosedMarkup(unittest.TestCase):
    EN_BODY = "<p>Intro.</p>{}<p>You must rotate 5 keys.</p>"
    PT_BODY = "<p>Introdução.</p>{}<p>Você {} girar {} chaves.</p>"

    def test_unclosed_comment_or_quote_blocks(self):
        for x in ("<!-- x", '<a title="x>y'):
            with self.subTest(x):
                found = html_pair(page("en", self.EN_BODY.format(x)), page("pt", self.PT_BODY.format(x, "pode", 9)))[0]
                self.assertIn(f"English: {UNCLOSED}", found)
                self.assertIn(f"translation: {UNCLOSED}", found)
        found = md_pair("Intro.\n\n<!-- todo\n\nYou must rotate 5 keys.\n",
                        "Introdução.\n\n<!-- todo\n\nVocê pode girar 9 chaves.\n")[0]
        self.assertIn(f"English: {UNCLOSED}", found)

    def test_closed_markup_is_checked_and_clean(self):
        x = '<!-- x --><a title="x">y</a>'
        self.assertTrue(html_pair(page("en", self.EN_BODY.format(x)), page("pt", self.PT_BODY.format(x, "pode", 9)))[0])
        self.assertEqual(html_pair(page("en", self.EN_BODY.format(x)), page("pt", self.PT_BODY.format(x, "deve", 5))),
                         ([], []))
        self.assertFalse(vt.parse_page("<p>R&D").unclosed_markup)

    def test_comment_opener_inside_closed_markup_passes(self):
        # not <textarea>: python 3.9 parses its content as markup, so an opener there really hides the rest
        for x in ("<script>var s='<!--';</script>", "<style><!-- a{}</style>", "<p title='<!--'>t</p>"):
            with self.subTest(x):
                self.assertFalse(vt.parse_page(page("en", x + "<p>ok</p>")).unclosed_markup)

    def test_list_number_rejects_underscore_digits(self):
        self.assertEqual(vt._list_number("01"), "1")
        self.assertEqual(vt._list_number("1_0"), "1_0")


class C7ScalarsAndNumbering(unittest.TestCase):
    def test_block_scalar_indicators(self):
        for ind in (">+", "|+", ">2", ">2-", "|-", ">"):
            with self.subTest(ind):
                head = vt.parse_page(markdown_to_html(f"---\ntitle: {ind}\n  Hello\n  there\nlang: en\n---\nx")).head
                self.assertEqual(head["title"].split(), ["Hello", "there"])

    def test_list_numbers_as_integers(self):
        same = (('<ol start="01"><li>a</li></ol>', "<ol><li>b</li></ol>"),
                ('<ol start="1"><li>a</li></ol>', '<ol start="01"><li>b</li></ol>'),
                ('<ol><li value="03">a</li></ol>', '<ol><li value="3">b</li></ol>'))
        for en, pt in same:
            with self.subTest(en):
                self.assertEqual(html_pair(page("en", en), page("pt", pt)), ([], []))
        found = html_pair(page("en", '<ol><li value="03">a</li></ol>'), page("pt", '<ol><li value="4">b</li></ol>'))[0]
        self.assertTrue(any("list numbers" in m for m in found), found)


if __name__ == "__main__":
    unittest.main()
