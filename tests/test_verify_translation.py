"""Tests for verify_translation.py. Run: python3 -m unittest tests/test_verify_translation.py

fixtures/translation_cases.json holds the adversarial cases from the first
2026-11-22 review (false passes F1 to F16, false blocks B1 to B13), and
translation_cases_round2.json and _round3.json those from the second and third
(round 3 is a snapshot of every probe; its known_gap flag marks passes whose probe
name did not say the translation was correct). Each carries the verdict
and, for a block, the check that must raise it. Round-2 cases marked known_gap
are limits the checker accepts (left to the back-translation); they are listed
so a change in them shows up, not asserted as correct."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "back-translation" / "scripts"))
import export_blocks  # noqa: E402
import translation_lang as tl  # noqa: E402
import verify_translation as vt  # noqa: E402

EN = vt.load_lang("en")
PT, ES, DE, ZH = (vt.load_lang(c) for c in ("pt", "es", "de", "zh-Hans"))
LANGS = {"en": EN, "pt": PT, "es": ES, "de": DE, "zh-Hans": ZH}
FIX = Path(__file__).parent / "fixtures"
CASES = json.loads((FIX / "translation_cases.json").read_text(encoding="utf-8"))
CASES2 = json.loads((FIX / "translation_cases_round2.json").read_text(encoding="utf-8"))
CASES3 = json.loads((FIX / "translation_cases_round3.json").read_text(encoding="utf-8"))
NO_CLAIM_NOISE = ("English sidecar has no claims", "translated <main> text is identical to the English")


def vals(text, lang):
    return [(f"{v:f}" if isinstance(v, Decimal) else v, u) for v, _, u, _ in tl.number_tokens(text, lang, EN)]


def toks(text, lang):
    return tl.number_tokens(text, lang, None if lang is EN else EN)


def sidecars(en_html, lang, en_claims, tr_claims, model="sonnet", rounds=(2,)):
    h = hashlib.sha256(en_html.encode()).hexdigest()
    en = {"article": {"body_sha256": h, "path": "x"}, "claims": [{"id": i + 1, "claim": c} for i, c in enumerate(en_claims)]}
    tr = {"lang": lang, "source_body_sha256": h, "translator_model": "opus",
          "claims": [{"id": i + 1, "sentence": c} for i, c in enumerate(tr_claims)],
          "back_translation": [{"id": i + 1, "round": r, "verdict": "MATCH", "model": model,
                                "sentence_sha256": tl.sentence_sha256(c), "claim_sha256": tl.sentence_sha256(en_claims[i])}
                               for i, c in enumerate(tr_claims) for r in rounds]}
    return en, tr


def blockers(en_html, tr_html, lang, en_claims=(), tr_claims=(), **kw):
    en, tr = sidecars(en_html, lang, en_claims, tr_claims, **kw)
    page = f'<html lang="{lang}">' + tr_html
    return [f for f in vt.check(en, en_html, tr, page, LANGS[lang])
            if f["level"] == "blocker" and not (not en_claims and f["message"] in NO_CLAIM_NOISE)]


class ReviewCases(unittest.TestCase):
    def test_every_case(self):
        self.assertGreater(len(CASES), 80)
        for c in CASES:
            with self.subTest(c["name"]):
                found = blockers(c["en"], c["tr"], c["lang"], c["en_claims"], c["tr_claims"])
                if c["expect"] == "pass":
                    self.assertEqual(found, [], c["name"])
                else:
                    self.assertIn(c["check"], {f["check"] for f in found}, c["name"])

    def test_every_round2_case(self):
        self.assertGreater(len(CASES2), 130)
        for c in CASES2:
            with self.subTest(c["name"]):
                en, tr = sidecars(c["en"], c["lang"], c["en_claims"], c["tr_claims"])
                found = [f for f in vt.check(en, c["en"], tr, c["tr"], LANGS[c["lang"]]) if f["level"] == "blocker"]
                self.assertEqual("block" if found else "pass", c["expect"], c["name"])
                if found:
                    self.assertIn(c["check"], {f["check"] for f in found}, c["name"])


class Round3(unittest.TestCase):
    def test_every_round3_case(self):
        self.assertGreater(len(CASES3), 250)
        for c in CASES3:
            with self.subTest(c["name"]):
                en, tr = sidecars(c["en"], c["lang"], c["en_claims"], c["tr_claims"])
                found = [f for f in vt.check(en, c["en"], tr, c["tr"], LANGS[c["lang"]]) if f["level"] == "blocker"
                         and not (not c["en_claims"] and f["message"] == "English sidecar has no claims")]
                if not c.get("either"):
                    self.assertEqual("block" if found else "pass", c["expect"], c["name"])

    def test_negation_after_the_modal_and_relative_clauses(self):
        self.assertEqual(tl.modal_kinds("Users must also not share passwords", EN), ["must-not"])
        self.assertEqual(tl.modal_kinds("Passwords that are not unique must be changed", EN), ["must"])
        self.assertEqual(tl.modal_kinds("Usuários que não têm gerenciador devem anotá-la", PT), ["must"])
        self.assertEqual(tl.modal_kinds("Deve-se usar uma senha longa", PT), ["must"])
        self.assertEqual(tl.modal_kinds("Sharing passwords is not allowed", EN), ["may-not"])

    def test_comparators_dates_currency(self):
        self.assertTrue(tl.same_numbers(toks("15 or more characters", EN), toks("15 caracteres ou mais", PT)))
        self.assertFalse(tl.same_numbers(toks("15 or more characters", EN), toks("15 ou menos caracteres", PT)))
        self.assertFalse(tl.same_numbers(toks("≥ 8 characters", EN), toks("≤ 8 caracteres", PT)))
        self.assertFalse(tl.same_numbers(toks("$500", EN), toks("R$ 500", PT)))
        self.assertFalse(tl.same_numbers(toks("10x faster", EN), toks("10% mais rápido", PT)))
        self.assertNotEqual(tl.extract_dates("before 17 September 2026", EN)[0], tl.extract_dates("depois de 17 de setembro de 2026", PT)[0])
        self.assertNotEqual(tl.extract_dates("On 17 September it", EN)[0], tl.extract_dates("Em 17 de outubro", PT)[0])
        self.assertEqual(tl.extract_dates("Signing in may require a code", EN)[0], [])

    def test_parser_disagreements(self):
        hidden = "<main><p>a</p><![CDATA[ > Na verdade leva 9 dias. ]]></main>"
        self.assertIn("9", vt.parse_page(hidden).region_text("main"))
        self.assertIn("9", vt.parse_page("<main><script>x</script x>leva 9 dias</main>").region_text("main"))
        self.assertEqual(vt.parse_page('<main><a href="/a" href="/b">x</a></main>').hrefs, ["/a"])
        self.assertIn("devem", vt.parse_page("<main><p>de&shy;vem</p></main>").region_text("main"))
        self.assertIn("5", vt.parse_page("<html><head><title>t</title><main><p>5</p></main>").region_text("main"))

    def test_unapproved_english_blocks(self):
        en, tr = sidecars("<main><p>A[1].</p></main>", "pt", ["A is true"], ["A é verdade"])
        en["article"]["approval"] = "pending"
        en["claims"][0]["verdict"] = "CONTESTED"
        found = vt.check(en, "<main><p>A[1].</p></main>", tr, '<html lang="pt"><main><p>A é verdade[1].</p></main>', PT)
        msgs = " ".join(f["message"] for f in found)
        self.assertIn("approval", msgs)
        self.assertIn("CONTESTED", msgs)


class Numbers(unittest.TestCase):
    def test_separators_by_locale(self):
        self.assertEqual(vals("seen 210,461,208 times, 0.61 seconds", EN),
                         vals("visto 210.461.208 vezes, 0,61 segundo", PT))
        self.assertEqual(vals("1,024 iterations", EN), vals("1.024 Iterationen", DE))

    def test_billion_traps(self):
        self.assertEqual(vals("10.7 billion years", EN), vals("10,7 mil millones de años", ES))
        self.assertNotEqual(vals("10.7 billion years", EN), vals("10,7 billones de años", ES))
        self.assertEqual(vals("4.4 billion years", EN), vals("4,4 Milliarden Jahre", DE))
        self.assertNotEqual(vals("4.4 billion years", EN), vals("4,4 Billionen Jahre", DE))

    def test_chinese_wan_yi(self):
        self.assertEqual(vals("10.7 billion years", EN), vals("107亿年", ZH))
        self.assertEqual(vals("159 quadrillion years", EN), vals("15.9亿亿年", ZH))
        self.assertEqual(vals("120 million", EN), vals("1亿2000万", ZH))
        self.assertIn("ambiguous", " ".join(tl.scale_problems("3兆", ZH)))
        self.assertEqual(vals("5千克", ZH), [("5", None)])
        self.assertEqual(tl.scale_problems("o pavilhão está aberto", PT), [])

    def test_exact_at_any_length(self):
        self.assertNotEqual(vals("1.00000000000000000000000000001 million", EN),
                            vals("1,00000000000000000000000000009 milhão", PT))

    def test_number_words(self):
        self.assertEqual(vals("three out of four", EN), vals("três dos quatro", PT))
        self.assertEqual(vals("Twenty-five years", EN), vals("Vinte e cinco anos", PT))
        self.assertEqual(vals("both are", EN), vals("as duas estão", PT))

    def test_units_may_sit_after_an_adjective_and_must_match(self):
        self.assertTrue(tl.same_numbers(toks("three random words", EN), toks("três palavras aleatórias", PT)))
        self.assertFalse(tl.same_numbers(toks("254 days", EN), toks("254 longos anos", PT)))
        self.assertFalse(tl.same_numbers(toks("254 days", EN), toks("254", PT)))
        self.assertTrue(tl.same_numbers(toks("four of the following types of characters", EN), toks("quatro tipos de caracteres", PT)))

    def test_comparators(self):
        self.assertTrue(tl.same_numbers(toks("an 8-character minimum", EN), toks("um mínimo de 8 caracteres", PT)))
        self.assertTrue(tl.same_numbers(toks("a work factor of 10 or more", EN), toks("um fator de trabalho de 10 ou mais", PT)))
        self.assertFalse(tl.same_numbers(toks("at least 8", EN), toks("no máximo 8", PT)))
        self.assertFalse(tl.same_numbers(toks("before 2021", EN), toks("depois de 2021", PT)))

    def test_written_form_keeps_zeros(self):
        self.assertFalse(tl.same_numbers(toks("Python 3.10", EN), toks("Python 3.1", PT)))
        self.assertTrue(tl.same_numbers(toks("0.61 seconds", EN), toks("0,61 segundo", PT)))

    def test_bare_scale_words(self):
        self.assertFalse(tl.same_numbers(toks("billions of years", EN), toks("milhares de anos", PT)))
        self.assertTrue(tl.same_numbers(toks("billions of years", EN), toks("bilhões de anos", PT)))
        self.assertTrue(tl.same_numbers(toks("one billion years", EN), toks("1 bilhão de anos", PT)))

    def test_fractions_and_hyphen_compounds(self):
        self.assertFalse(tl.same_numbers(toks("8½ hours", EN), toks("8¼ horas", PT)))
        self.assertTrue(tl.same_numbers(toks("two-factor authentication", EN), toks("autenticação de dois fatores", PT)))

    def test_letters_that_casefold_oddly(self):
        self.assertEqual(vals("5 thouſand", EN), [("5000", None)])
        self.assertEqual(vals("5 MİLLİON", EN), [("5000000", None)])

    def test_markers_ignored(self):
        self.assertEqual(vals("RTX 5090[11]", EN), [("5090", None)])


class Normative(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(tl.modal_kinds("Verifiers must not impose composition rules", EN), ["must-not"])
        self.assertEqual(tl.modal_kinds("Os verificadores não devem impor regras", PT), ["must-not"])
        self.assertEqual(tl.modal_kinds("Os verificadores deveriam aceitar", PT), ["should"])
        self.assertEqual(tl.modal_kinds("验证方不得施加组成规则", ZH), ["must-not"])
        self.assertEqual(tl.modal_kinds("他们不得不更改", ZH), ["must"])
        self.assertEqual(tl.modal_kinds("You can set a length", EN), tl.modal_kinds("Puede definir una longitud", ES))
        self.assertEqual(tl.modal_kinds("Published in May 2024", EN), [])

    def test_spanish_tu_forms_and_feminine_minimum(self):
        """The tú register and "longitud mínima" are correct Spanish and must not block."""
        self.assertEqual(tl.modal_kinds("you can't change these settings", EN), tl.modal_kinds("no puedes cambiar esta configuración", ES))
        self.assertEqual(tl.modal_kinds("You can add up to 1,000 terms", EN), tl.modal_kinds("Puedes agregar hasta 1.000 términos", ES))
        self.assertEqual(tl.modal_kinds("you must change it", EN), tl.modal_kinds("debes cambiarla", ES))
        self.assertNotEqual(tl.modal_kinds("you must change it", EN), tl.modal_kinds("puedes cambiarla", ES))
        self.assertTrue(tl.same_numbers(toks("a minimum of 15 characters in length", EN), toks("una longitud mínima de 15 caracteres", ES)))
        self.assertTrue(tl.same_numbers(toks("set the minimum length to 15", EN), toks("fija la longitud mínima en 15", ES)))
        self.assertFalse(tl.same_numbers(toks("a minimum of 15 characters", EN), toks("una longitud máxima de 15 caracteres", ES)))
        self.assertTrue(tl.same_numbers(toks("a maximum length of 64 characters", EN), toks("una longitud máxima de 64 caracteres", ES)))
        self.assertFalse(tl.same_numbers(toks("a length of 64 characters", EN), toks("una longitud máxima de 64 caracteres", ES)))
        self.assertEqual(tl.modal_kinds("you don't have to change it", EN), tl.modal_kinds("no tienes por qué cambiarla", ES))
        self.assertEqual(tl.modal_kinds("you will have to change it", EN), tl.modal_kinds("tendrás que cambiarla", ES))

    def test_german_clause_negation(self):
        self.assertEqual(tl.modal_kinds("Prüfer dürfen keine Regeln vorschreiben", DE), ["must-not"])
        self.assertEqual(tl.modal_kinds("Ein Passwort muss kein Sonderzeichen enthalten", DE), ["need-not"])
        self.assertEqual(tl.modal_kinds("Sie können das, müssen aber nicht", DE), ["may", "need-not"])
        self.assertEqual(tl.modal_kinds("Benutzer dürfen Passwörter, die geleakt wurden, nicht weiterverwenden", DE), ["must-not"])
        self.assertEqual(tl.modal_kinds("Benutzer dürfen z. B. keine alten Passwörter verwenden", DE), ["must-not"])

    def test_negation_near_the_modal(self):
        self.assertEqual(tl.modal_kinds("O NIST não exige uma lista", PT), ["need-not"])
        self.assertEqual(tl.modal_kinds("Não se deve anotar as senhas", PT), ["must-not"])
        self.assertEqual(tl.modal_kinds("NIST does not require complexity rules", EN), ["need-not"])
        self.assertEqual(tl.modal_kinds("Users must use a password that is not on the list", EN), ["must"])
        self.assertEqual(tl.modal_kinds("Verifiers must not only check length", EN), ["must"])
        self.assertEqual(tl.modal_kinds("Isso pode não ser suficiente", PT), ["may-not"])

    def test_null_forms(self):
        self.assertEqual(tl.modal_kinds("Changes should not be required", EN), ["should-not"])
        self.assertEqual(tl.modal_kinds("MFA will be required", EN), ["must"])
        self.assertEqual(tl.modal_kinds("A MFA não vai ser exigida", PT), ["need-not"])

    def test_forbidden(self):
        found = blockers("<main><p>x</p></main>", "<main><p>Das Passwort muss\n nicht geändert werden.</p></main>", "de")
        self.assertTrue(any(f["check"] == "normative" and "muss nicht" in f["message"] for f in found))
        self.assertFalse(blockers("<main><p>The target state</p></main>", "<main><p>Der Soll-Zustand</p></main>", "de"))


class Claims(unittest.TestCase):
    EN_HTML = ('<main><p>NIST says verifiers should not require changes, and an 8 character '
               'password takes 254 days<sup><a href="#s1">[1]</a></sup>.</p><p>Run <code>hashcat -b</code>, see '
               '<a href="https://example.org/a">this</a>.</p></main>')
    PT_HTML = ('<main><aside data-i18n-skip><p>Traduzido em 22 de novembro de 2026.</p></aside>'
               '<p>O NIST diz que os verificadores não deveriam exigir trocas, e uma senha de 8 '
               'caracteres leva 254 dias<sup><a href="#s1">[1]</a></sup>.</p><p>Execute <code>hashcat -b</code>, veja '
               '<a href="https://example.org/a">isto</a>.</p></main>')
    CLAIM_EN = "NIST says verifiers should not require changes, and an 8 character password takes 254 days"
    CLAIM_PT = "O NIST diz que os verificadores não deveriam exigir trocas, e uma senha de 8 caracteres leva 254 dias"

    def run_check(self, page=None, sentence=CLAIM_PT, **kw):
        return blockers(self.EN_HTML, page or self.PT_HTML, "pt", [self.CLAIM_EN], [sentence], **kw)

    def test_clean_and_skip_aside_excluded(self):
        self.assertEqual(self.run_check(), [])

    def test_changed_number(self):
        found = self.run_check(self.PT_HTML.replace("254 dias", "245 dias"), self.CLAIM_PT.replace("254", "245"))
        self.assertIn("numbers", {f["check"] for f in found})

    def test_weaker_modal(self):
        weak = self.CLAIM_PT.replace("não deveriam", "não podem")
        self.assertIn("normative", {f["check"] for f in self.run_check(self.PT_HTML.replace("não deveriam", "não podem"), weak)})

    def test_code_changed(self):
        self.assertIn("tokens", {f["check"] for f in self.run_check(self.PT_HTML.replace("hashcat -b", "hashcat -B"))})

    def test_back_translation_rules(self):
        self.assertTrue(self.run_check(rounds=(1,)))
        self.assertTrue(self.run_check(model="opus"))

    def test_back_translation_bound_to_sentence(self):
        en, tr = sidecars(self.EN_HTML, "pt", [self.CLAIM_EN], [self.CLAIM_PT])
        tr["back_translation"][0]["sentence_sha256"] = tl.sentence_sha256("outra frase")
        found = vt.check(en, self.EN_HTML, tr, '<html lang="pt">' + self.PT_HTML, PT)
        self.assertTrue(any("different sentence" in f["message"] for f in found))

    def test_back_translation_bound_to_english_claim(self):
        en, tr = sidecars(self.EN_HTML, "pt", [self.CLAIM_EN], [self.CLAIM_PT])
        tr["back_translation"][0]["claim_sha256"] = tl.sentence_sha256("another claim")
        found = vt.check(en, self.EN_HTML, tr, '<html lang="pt">' + self.PT_HTML, PT)
        self.assertTrue(any("different English claim" in f["message"] for f in found))

    def test_same_model_despite_case_and_space(self):
        en, tr = sidecars(self.EN_HTML, "pt", [self.CLAIM_EN], [self.CLAIM_PT], model=" Opus ")
        found = vt.check(en, self.EN_HTML, tr, '<html lang="pt">' + self.PT_HTML, PT)
        self.assertTrue(any("same model" in f["message"] for f in found))

    def test_text_added_beside_a_claim(self):
        page = self.PT_HTML.replace('</a></sup>.</p><p>Execute', '</a></sup>, mas só no plano pago.</p><p>Execute')
        self.assertIn("claims", {f["check"] for f in self.run_check(page)})

    def test_stale_english(self):
        en, tr = sidecars(self.EN_HTML, "pt", [self.CLAIM_EN], [self.CLAIM_PT])
        tr["source_body_sha256"] = "0" * 64
        self.assertTrue(any(f["check"] == "stale" for f in vt.check(en, self.EN_HTML, tr, self.PT_HTML, PT)))

    def test_html_lang_must_match(self):
        en, tr = sidecars(self.EN_HTML, "pt", [self.CLAIM_EN], [self.CLAIM_PT])
        found = vt.check(en, self.EN_HTML, tr, '<html lang="es">' + self.PT_HTML, PT)
        self.assertTrue(any("does not match" in f["message"] for f in found))


class Parser(unittest.TestCase):
    def test_php_strip_is_linear_and_drops_unclosed_tail(self):
        t = time.time()
        p = vt.parse_page("<main><p>a</p>" + "<?php x " * 200_000 + "</main>")
        self.assertLess(time.time() - t, 2)
        self.assertEqual(p.mains, 1)
        self.assertEqual(vt.strip_php('a<?= "x" ?>b<?php y')[0], "ab")

    def test_stray_end_tags_close_nothing(self):
        p = vt.parse_page("</main><main>" + "<b>" * 1000 + "</i>" * 1000 + "<p>1</p></main>")
        self.assertEqual(p.mains, 1)
        self.assertIn("1", p.region_text("main"))

    def test_markers_inside_code_are_not_markers(self):
        p = vt.parse_page("<main><p>use <code>$a[1]</code></p></main>")
        self.assertEqual(tl.MARKER_RE.findall(p.region_text("main")), [])
        self.assertEqual(p.verbatim, ["$a[1]"])

    def test_claim_marker_binding(self):
        self.assertTrue(tl.MarkedBlock("X takes 254 days [1] , and Y.").binds("X takes 254 days, and Y", 1))
        self.assertTrue(tl.MarkedBlock("A is true[2]. B is true[1].").binds("A is true", 2))
        self.assertFalse(tl.MarkedBlock("A is true[2]. B is true[1].").binds("A is true", 1))
        self.assertFalse(tl.MarkedBlock("Something else[1].").binds("A is true", 1))
        title = f"O {tl.ATTR_OPEN}National Institute{tl.ATTR_CLOSE} NIST diz[1]."
        self.assertTrue(tl.MarkedBlock(title).binds("O NIST diz", 1))
        b = tl.MarkedBlock("Am 17. September 2026 lieferte es diese Zahlen[1]. Danach mehr.")
        self.assertEqual(b.remainder(b.sentence_of(1), {1: "Am 17. September 2026 lieferte es diese Zahlen"}), "")


class ContractionNegation(unittest.TestCase):
    """English contractions and curly apostrophes count as negation in sentence remainders."""

    EN_HTML = "<main><p>Use four words where it doesn't lock, says the tool[1].</p></main>"

    def test_remainder_keeps_in_word_apostrophes(self):
        b = tl.MarkedBlock("Use four words where it doesn't lock, says the tool[1].")
        self.assertEqual(b.remainder(b.sentence_of(1), {1: "says the tool"}), "Use four words where it doesn't lock")
        b = tl.MarkedBlock("Use four words where it doesn\u2019t lock, says the tool[1].")
        self.assertEqual(b.remainder(b.sentence_of(1), {1: "says the tool"}), "Use four words where it doesn't lock")
        b = tl.MarkedBlock("The 'box' is ticked[1].")
        self.assertEqual(b.remainder(b.sentence_of(1), {}), "The box is ticked")

    def test_contractions_and_curly_apostrophes_are_negators(self):
        for s in ("it doesn\u2019t lock", "it wouldn't", "it hadn't"):
            self.assertTrue(tl.negator_positions(s, EN), s)
        self.assertEqual(tl.negator_positions("it locks", EN), [])
        self.assertEqual(tl.modal_kinds("you can\u2019t", EN), ["may-not"])

    def test_negated_modal_in_remainder_counts_as_negation(self):
        en = "<main><p>Users can't reuse it, says the tool[1].</p></main>"
        kept = blockers(en, "<main><p>Os usuários não podem reutilizá-la, diz a ferramenta[1].</p></main>",
                        "pt", ["says the tool"], ["diz a ferramenta"])
        self.assertFalse([f for f in kept if f["check"] == "normative"], kept)
        dropped = blockers(en, "<main><p>Os usuários podem reutilizá-la, diz a ferramenta[1].</p></main>",
                           "pt", ["says the tool"], ["diz a ferramenta"])
        self.assertTrue([f for f in dropped if f["check"] == "normative"], dropped)

    def test_negated_modal_does_not_flip_its_neighbour(self):
        claim = "Admins must ensure users cannot reuse passwords"
        self.assertEqual(tl.modal_kinds(claim, EN), ["must", "may-not"])
        found = blockers(f"<main><p>{claim}[1].</p></main>",
                         "<main><p>Os administradores devem garantir que os usuários não possam reutilizar senhas[1].</p></main>",
                         "pt", [claim], ["Os administradores devem garantir que os usuários não possam reutilizar senhas"])
        self.assertFalse([f for f in found if f["check"] == "normative"], found)

    def test_language_without_negators_skips_the_negation_parity(self):
        found = blockers(self.EN_HTML, "<main><p>在不会锁定的地方使用四个词，工具说明如此[1]。</p></main>",
                         "zh-Hans", ["says the tool"], ["工具说明如此"])
        self.assertFalse([f for f in found if f["check"] == "normative"], found)

    def test_kept_negation_passes(self):
        found = blockers(self.EN_HTML, "<main><p>Use quatro palavras onde ela não bloqueia, diz a ferramenta[1].</p></main>",
                         "pt", ["says the tool"], ["diz a ferramenta"])
        self.assertFalse([f for f in found if f["check"] == "normative"], found)

    def test_dropped_negation_blocks(self):
        found = blockers(self.EN_HTML, "<main><p>Use quatro palavras onde ela bloqueia, diz a ferramenta[1].</p></main>",
                         "pt", ["says the tool"], ["diz a ferramenta"])
        self.assertTrue([f for f in found if f["check"] == "normative"], found)


class LocalisedAssets(unittest.TestCase):
    """A translated page may add its own language folder just before an image's filename."""

    @staticmethod
    def page(og, img, link, srcset=""):
        head = (f'<head><meta property="og:url" content="https://ex.com/a"><meta property="og:image" content="{og}">'
                f'<meta name="twitter:image" content="{og}"></head>')
        extra = f' srcset="{srcset}"' if srcset else ""
        return head + f'<main><p>Texto.</p><a href="{link}"><img src="{img}"{extra} alt="x"></a></main>'

    EN = ('<html lang="en"><head><meta property="og:url" content="https://ex.com/a">'
          '<meta property="og:image" content="https://ex.com/img/02-og.jpg"><meta name="twitter:image" '
          'content="https://ex.com/img/02-og.jpg"></head><main><p>Text.</p><a href="/img/01-2x.jpg">'
          '<img src="/img/01.jpg" srcset="/img/01.jpg 1x, /img/01-2x.jpg 2x" alt="x"></a></main>')

    def msgs(self, tr, lang="pt"):
        return [b["message"] for b in blockers(self.EN, tr, lang)]

    def test_own_language_folder_passes(self):
        tr = self.page("https://ex.com/img/pt/02-og.jpg", "/img/pt/01.jpg", "/img/pt/01-2x.jpg",
                       "/img/pt/01.jpg 1x, /img/pt/01-2x.jpg 2x")
        self.assertEqual(self.msgs(tr), [])

    def test_unchanged_english_paths_still_pass(self):
        tr = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg", "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assertEqual(self.msgs(tr), [])

    def test_other_language_folder_blocks(self):
        tr = self.page("https://ex.com/img/es/02-og.jpg", "/img/es/01.jpg", "/img/es/01-2x.jpg",
                       "/img/es/01.jpg 1x, /img/es/01-2x.jpg 2x")
        m = self.msgs(tr)
        for what in ("og:image differs", "twitter:image differs", "links differ", "media sources differ"):
            self.assertTrue(any(what in x for x in m), what)

    ALL_FOUR = ("og:image differs", "twitter:image differs", "links differ", "media sources differ")

    def assert_blocks(self, msgs, *what):
        for w in what:
            self.assertTrue(any(w in x for x in msgs), f"{w!r} not in {msgs}")

    def test_folder_not_before_filename_blocks(self):
        tr = self.page("https://ex.com/pt/img/x/02-og.jpg", "/img/pt/x/01.jpg", "/img/pt/x/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks(self.msgs(tr), *self.ALL_FOUR)

    def test_uppercase_folder_blocks(self):
        tr = self.page("https://ex.com/img/PT/02-og.jpg", "/img/PT/01.jpg", "/img/PT/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks(self.msgs(tr), *self.ALL_FOUR)

    def test_root_level_folder_is_a_page_prefix_and_blocks(self):
        en = self.EN.replace("/img/", "/")
        tr = self.page("https://ex.com/pt/02-og.jpg", "/pt/01.jpg", "/img/01-2x.jpg", "/01.jpg 1x, /01-2x.jpg 2x")
        m = [b["message"] for b in blockers(en, tr.replace("/img/01-2x.jpg", "/01-2x.jpg"), "pt")]
        self.assert_blocks(m, "og:image differs", "twitter:image differs", "media sources differ")

    def test_query_and_fragment_kept(self):
        en = self.EN.replace('src="/img/01.jpg"', 'src="/img/01.jpg?v=2"').replace('href="/img/01-2x.jpg"', 'href="/img/01-2x.jpg#x"')
        tr = self.page("https://ex.com/img/pt/02-og.jpg", "/img/pt/01.jpg?v=2", "/img/pt/01-2x.jpg#x",
                       "/img/pt/01.jpg 1x, /img/pt/01-2x.jpg 2x")
        self.assertEqual([b["message"] for b in blockers(en, tr, "pt")], [])

    def test_folder_inside_a_query_blocks(self):
        en = self.EN.replace('src="/img/01.jpg"', 'src="/img/01.jpg?f=/b.png"')
        tr = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg?f=/pt/b.png", "/img/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks([b["message"] for b in blockers(en, tr, "pt")], "media sources differ")

    def test_page_link_gets_no_asset_allowance(self):
        en = self.EN.replace('href="/img/01-2x.jpg"', 'href="/guides/how.php"')
        for href in ("/guides/pt/how.php", "/pt/guides/pt/how.php"):
            tr = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", href, "/img/01.jpg 1x, /img/01-2x.jpg 2x")
            self.assert_blocks([b["message"] for b in blockers(en, tr, "pt")], "links differ")

    def test_prefix_and_folder_on_one_link_blocks(self):
        tr = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/pt/img/pt/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks(self.msgs(tr), "links differ")

    def test_page_prefix_on_a_page_link_still_passes(self):
        en = self.EN.replace('href="/img/01-2x.jpg"', 'href="/guides/how"')
        tr = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/pt/guides/how", "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assertEqual([b["message"] for b in blockers(en, tr, "pt")], [])

    def test_other_site_media_and_links_block(self):
        en = (self.EN.replace('src="/img/01.jpg"', 'src="https://cdn.other.com/01.jpg"')
              .replace('href="/img/01-2x.jpg"', 'href="//cdn.other.com/01-2x.jpg"'))
        tr = self.page("https://ex.com/img/02-og.jpg", "https://cdn.other.com/pt/01.jpg", "//cdn.other.com/pt/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks([b["message"] for b in blockers(en, tr, "pt")], "links differ", "media sources differ")

    def test_protocol_relative_other_host_blocks(self):
        en = self.EN.replace("https://ex.com/img/02-og.jpg", "//cdn.other.com/02-og.jpg")
        tr = self.page("//cdn.other.com/pt/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg", "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks([b["message"] for b in blockers(en, tr, "pt")], "og:image differs", "twitter:image differs")

    def test_srcset_judged_per_candidate(self):
        en = self.EN.replace("/img/01.jpg 1x, /img/01-2x.jpg 2x", "https://cdn.other.com/a.jpg 1x, /img/b.jpg 2x")
        ok = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg",
                       "https://cdn.other.com/a.jpg 1x, /img/pt/b.jpg 2x")
        self.assertEqual([b["message"] for b in blockers(en, ok, "pt")], [])
        bad = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg",
                        "https://cdn.other.com/pt/a.jpg 1x, /img/b.jpg 2x")
        self.assert_blocks([b["message"] for b in blockers(en, bad, "pt")], "media sources differ")

    def test_english_already_in_a_pt_folder(self):
        en = self.EN.replace("/img/", "/img/pt/")
        same = self.page("https://ex.com/img/pt/02-og.jpg", "/img/pt/01.jpg", "/img/pt/01-2x.jpg",
                         "/img/pt/01.jpg 1x, /img/pt/01-2x.jpg 2x")
        self.assertEqual([b["message"] for b in blockers(en, same, "pt")], [])
        dropped = self.page("https://ex.com/img/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg", "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assert_blocks([b["message"] for b in blockers(en, dropped, "pt")], *self.ALL_FOUR)

    def test_hyphenated_language_folder(self):
        tr = self.page("https://ex.com/img/zh-Hans/02-og.jpg", "/img/zh-Hans/01.jpg", "/img/zh-Hans/01-2x.jpg",
                       "/img/zh-Hans/01.jpg 1x, /img/zh-Hans/01-2x.jpg 2x")
        self.assertEqual(self.msgs(tr, "zh-Hans"), [])
        self.assert_blocks(self.msgs(tr.replace("zh-Hans", "pt-BR"), "pt"), *self.ALL_FOUR)

    def test_image_on_the_english_og_image_host(self):
        en = self.EN.replace('<meta property="og:url" content="https://ex.com/a">', "")
        tr = self.page("https://ex.com/img/pt/02-og.jpg", "/img/pt/01.jpg", "/img/pt/01-2x.jpg",
                       "/img/pt/01.jpg 1x, /img/pt/01-2x.jpg 2x").replace('<meta property="og:url" content="https://ex.com/a">', "")
        self.assertEqual([b["message"] for b in blockers(en, tr, "pt")], [])

    def test_page_prefix_on_media_blocks(self):
        tr = self.page("https://ex.com/img/02-og.jpg", "/pt/img/01.jpg", "/img/01-2x.jpg", "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        self.assertTrue(any("media sources differ" in x for x in self.msgs(tr)))

    def test_different_file_blocks(self):
        tr = self.page("https://ex.com/img/pt/03-og.jpg", "/img/pt/01.jpg", "/img/pt/01-2x.jpg",
                       "/img/pt/01.jpg 1x, /img/pt/09-2x.jpg 2x")
        m = self.msgs(tr)
        self.assertTrue(any("og:image differs" in x for x in m))
        self.assertTrue(any("media sources differ" in x for x in m))

    def test_another_sites_folder_is_its_address(self):
        en = self.EN.replace('content="https://ex.com/img/02-og.jpg"', 'content="https://cdn.other.com/02-og.jpg"')
        tr = self.page("https://cdn.other.com/pt/02-og.jpg", "/img/01.jpg", "/img/01-2x.jpg",
                       "/img/01.jpg 1x, /img/01-2x.jpg 2x")
        m = [b["message"] for b in blockers(en, tr, "pt")]
        self.assertTrue(any("og:image differs" in x for x in m))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        en_html = '<html lang="en"><main><p>Passwords must be 15 characters[1].</p></main>\r\n'
        (self.root / "en.php").write_bytes(en_html.encode())
        text = (self.root / "en.php").read_text(encoding="utf-8")  # universal newlines, as verify_sources reads it
        h = hashlib.sha256(text.encode()).hexdigest()
        (self.root / "en.json").write_text(json.dumps({"article": {"path": "en.php", "body_sha256": h},
                                                       "claims": [{"id": 1, "claim": "Passwords must be 15 characters"}]}))
        (self.root / "pt.php").write_text('<html lang="pt-BR"><main><p>As senhas devem ter 15 caracteres[1].</p></main>',
                                          encoding="utf-8")
        s = "As senhas devem ter 15 caracteres"
        c = "Passwords must be 15 characters"
        self.tr = {"lang": "pt", "source_sidecar": "en.json", "article": "pt.php", "source_body_sha256": h,
                   "translator_model": "opus", "claims": [{"id": 1, "sentence": s}],
                   "back_translation": [{"id": 1, "round": 2, "verdict": "MATCH", "model": "sonnet",
                                         "sentence_sha256": tl.sentence_sha256(s), "claim_sha256": tl.sentence_sha256(c)}]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, tr):
        p = self.root / "t.json"
        p.write_text(json.dumps(tr) if not isinstance(tr, str) else tr)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return vt.main([str(p), "--root", str(self.root)])

    def test_clean_with_crlf_english(self):
        self.assertEqual(self.run_cli(self.tr), 0)

    def test_unknown_language_is_input_error(self):
        self.assertEqual(self.run_cli({**self.tr, "lang": "xx"}), 2)
        self.assertEqual(self.run_cli({**self.tr, "lang": "../en"}), 2)

    def test_malformed_shapes_are_input_errors(self):
        self.assertEqual(self.run_cli({**self.tr, "claims": "nope"}), 2)
        self.assertEqual(self.run_cli({**self.tr, "claims": [{"id": "1", "sentence": 3}]}), 2)
        self.assertEqual(self.run_cli({**self.tr, "back_translation": [{"id": 1}]}), 2)
        self.assertEqual(self.run_cli("[1, 2]"), 2)
        self.assertEqual(self.run_cli("{not json"), 2)

    def test_non_utf8_page_is_input_error(self):
        (self.root / "pt.php").write_bytes(b"<main>\xff\xfe</main>")
        self.assertEqual(self.run_cli(self.tr), 2)

    def test_path_outside_root_is_input_error(self):
        self.assertEqual(self.run_cli({**self.tr, "article": "../../etc/hosts"}), 2)

    def test_fifo_is_input_error(self):
        os.mkfifo(self.root / "fifo.php")
        self.assertEqual(self.run_cli({**self.tr, "article": "fifo.php"}), 2)

    def test_oversized_file_is_input_error(self):
        (self.root / "pt.php").write_text("x" * (vt.MAX_INPUT_BYTES + 1))
        self.assertEqual(self.run_cli(self.tr), 2)

    def test_changed_number_blocks(self):
        (self.root / "pt.php").write_text('<html lang="pt-BR"><main><p>As senhas devem ter 16 caracteres[1].</p></main>',
                                          encoding="utf-8")
        self.assertEqual(self.run_cli(self.tr), 1)


class NoSidecar(unittest.TestCase):
    """No-sidecar mode: two pages, optional per-block back-translation records."""
    DIR = FIX / "nosidecar"
    EN_HTML = (DIR / "en.html").read_text(encoding="utf-8")
    PT_HTML = (DIR / "pt.html").read_text(encoding="utf-8")
    BT = json.loads((DIR / "back_translation.json").read_text(encoding="utf-8"))

    def found(self, pt=None, bt=BT, model="model-a"):
        return vt.check_pages(self.EN_HTML, pt or self.PT_HTML, PT, bt, model)

    def checks(self, old, new):
        self.assertIn(old, self.PT_HTML)
        return {f["check"] for f in self.found(self.PT_HTML.replace(old, new)) if f["level"] == "blocker"}

    def run_cli(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            try:
                code = vt.main(["--root", str(self.DIR), *argv])
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_clean_pair_passes(self):
        self.assertEqual(self.found(), [])
        code, out = self.run_cli("--en", "en.html", "--tr", "pt.html", "--lang", "pt",
                                 "--back-translation", "back_translation.json", "--translator-model", "model-a")
        self.assertEqual((code, out.splitlines()[0]), (0, "Translation check: pt.html (pt)  Blockers: 0  Warnings: 0"))

    def test_changed_number(self):
        self.assertIn("numbers", self.checks("400 anos", "40 anos"))

    def test_swapped_table_cell(self):
        self.assertIn("numbers", self.checks("<td>8 caracteres</td><td>2 dias</td>", "<td>2 dias</td><td>8 caracteres</td>"))

    def test_changed_link(self):
        self.assertIn("tokens", self.checks("https://example.com/pt/guide", "https://example.com/pt/guia"))

    def test_changed_code_span(self):
        self.assertIn("tokens", self.checks("pwgen -s 15", "pwgen -s 16"))

    def test_must_rendered_as_may(self):
        found = self.found(self.PT_HTML.replace("devem ter", "podem ter"))
        self.assertTrue(any(f["check"] == "normative" and f["message"].startswith("B2: modals") for f in found))

    def test_missing_back_translation_record(self):
        found = self.found(bt=[r for r in self.BT if r["block"] != 5])
        self.assertTrue(any(f["level"] == "blocker" and f["message"].startswith("B5: no back-translation") for f in found))

    def test_back_translation_by_the_translator(self):
        found = self.found(model=" Model-B ")
        self.assertTrue(any(f["level"] == "blocker" and "same model" in f["message"] for f in found))

    def test_no_back_translation_warns_once(self):
        found = self.found(bt=None)
        self.assertEqual([(f["level"], f["message"]) for f in found], [("warning", "no back-translation recorded")])
        code, out = self.run_cli("--en", "en.html", "--tr", "pt.html", "--lang", "pt")
        self.assertEqual((code, out.splitlines()[0]), (0, "Translation check: pt.html (pt)  Blockers: 0  Warnings: 1"))

    def test_both_modes_or_neither_exit_2(self):
        self.assertEqual(self.run_cli("t.json", "--en", "en.html", "--tr", "pt.html", "--lang", "pt")[0], 2)
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("--en", "en.html", "--tr", "pt.html")[0], 2)

    def test_bad_input_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("en.html", "pt.html"):
                (Path(d) / name).write_text((self.DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
            bad = Path(d) / "bt.json"
            bad.write_text(json.dumps([{"block": "1", "round": 2, "verdict": "MATCH", "model": "m"}]))
            self.assertEqual(self.run_cli("--en", "en.html", "--tr", "pt.html", "--lang", "pt", "--root", d,
                                          "--back-translation", "bt.json")[0], 2)
        self.assertEqual(self.run_cli("--en", "../en.html", "--tr", "pt.html", "--lang", "pt")[0], 2)


class ReviewRound4(unittest.TestCase):
    """Fourth-review false passes and false blocks in the checker, each with its control."""

    @staticmethod
    def page(lang, body, title=None):
        return f'<html lang="{lang}"><head><title>{title or "T " + lang}</title></head><body><main>{body}</main></body></html>'

    def found(self, en_body, tr_body, lang="pt", tr_lang=None):
        f = vt.check_pages(self.page("en", en_body), self.page(tr_lang or lang, tr_body), LANGS[lang])
        return ([x["message"] for x in f if x["level"] == "blocker"],
                [x["message"] for x in f if x["level"] == "warning" and x["check"] != "back-translation"])

    def test_unclosed_raw_text_element_blocks(self):
        for tag in ("script", "style", "template"):
            with self.subTest(tag):
                b, _ = self.found(f"<p>a <{tag}> b</p><p>You must wait 5 days.</p>",
                                  f"<p>x <{tag}> y</p><p>Voc\u00ea pode esperar 50 dias.</p>")
                self.assertIn(f"translation: <{tag}> never closed; everything after it is unchecked", b)
        self.assertEqual(self.found("<p>Wait 5 days.</p><script>x()</script><style>p{}</style>",
                                    "<p>Espere 5 dias.</p><script>x()</script><style>p{}</style>"), ([], []))

    def test_ordered_list_start_and_value(self):
        b, _ = self.found("<ol start=3><li>Restart now</li></ol>", "<ol start=4><li>Reinicie agora</li></ol>")
        self.assertTrue(any("list numbers" in m for m in b))
        b, _ = self.found('<ol><li value="7">Restart now</li></ol>', '<ol><li value="8">Reinicie agora</li></ol>')
        self.assertTrue(any("li value=7" in m for m in b))
        self.assertEqual(self.found('<ol start="3"><li>Restart now</li></ol>', "<ol start=3><li>Reinicie agora</li></ol>"),
                         ([], []))
        self.assertEqual(self.found('<ol start="1"><li>Restart now</li></ol>', "<ol><li>Reinicie agora</li></ol>"), ([], []))

    def test_checkbox_state(self):
        b, _ = self.found('<ul><li><input type="checkbox" checked>Enable logging</li></ul>',
                          '<ul><li><input type="checkbox">Ative o registro</li></ul>')
        self.assertTrue(any("checkboxes" in m for m in b))

    def test_dropped_loose_text_run_blocks(self):
        b, _ = self.found("<div>You must wait 5 days.</div><div>Keep it.</div>", "<div>Voc\u00ea deve esperar 5 dias.</div>")
        self.assertIn("main: 2 runs of text outside block tags in English, 1 in the translation", b)
        self.assertEqual(self.found("<div>You must wait 5 days.</div><div>Keep <b>it</b>.</div>",
                                    "<div>Voc\u00ea deve esperar 5 dias.</div><div>Guarde <b>isso</b>.</div>"), ([], []))

    def test_negation_only_difference_warns_in_no_sidecar_mode(self):
        b, w = self.found("<p>You must wait.</p><p>It is safe.</p>", "<p>Voc\u00ea deve esperar.</p><p>N\u00e3o \u00e9 seguro.</p>")
        self.assertEqual((b, w), ([], ["B2: negation differs; confirm in the back-translation"]))
        b, _ = self.found("<p>You must not wait.</p>", "<p>Voc\u00ea deve esperar.</p>")
        self.assertTrue(any(m.startswith("B1: modals") for m in b))
        sidecar = blockers("<main><p>It is safe, says the tool[1].</p></main>",
                           "<main><p>N\u00e3o \u00e9 seguro, diz a ferramenta[1].</p></main>", "pt", ["says the tool"], ["diz a ferramenta"])
        self.assertIn("normative", {f["check"] for f in sidecar})  # sidecar mode unchanged: still a blocker

    def test_reordered_numbers_of_different_kinds_warn(self):
        b, w = self.found("<p>It grew 5% in 2021.</p>", "<p>Em 2021, cresceu 5%.</p>")
        self.assertEqual(b, [])
        self.assertTrue(w and "different order" in w[0])
        self.assertTrue(self.found("<p>It grew 5% in 2021.</p>", "<p>Em 2022, cresceu 5%.</p>")[0])
        self.assertTrue(self.found("<p>8 characters take 2 days, 12 take 3 days.</p>",
                                   "<p>12 caracteres levam 2 dias, 8 levam 3 dias.</p>")[0])
        self.assertTrue(blockers("<main><p>It grew 5% in 2021.</p></main>", "<main><p>Em 2021, cresceu 6%.</p></main>", "pt"))

    def test_fragment_links_may_be_translated(self):
        self.assertEqual(self.found('<p><a href="#how-it-works">How</a></p>', '<p><a href="#como-funciona">Como</a></p>'),
                         ([], []))
        for en, tr in (("/guide#how", "/guide#como"), ("#how", "/how"), ("https://a.org/x", "https://a.org/y")):
            with self.subTest(en):
                b, _ = self.found(f'<p><a href="{en}">How</a></p>', f'<p><a href="{tr}">Como</a></p>')
                self.assertTrue(any(m.startswith("links differ") for m in b))

    def test_lang_tags(self):
        for lang, page_lang in (("zh-Hans", "zh"), ("zh-Hans", "zh-CN"), ("zh-Hans", "ZH-sg"), ("zh-Hans", "zh-Hans-CN"),
                                ("pt", "pt-PT"), ("es", "es-419"), ("de", "de-AT")):
            with self.subTest(page_lang):
                b, _ = self.found("<p>Hello there.</p>", "<p>\u4f60\u597d\u3002</p>" if lang == "zh-Hans" else "<p>Ol\u00e1 la.</p>",
                                  lang, page_lang)
                self.assertFalse([m for m in b if "html lang" in m], page_lang)
        for lang, page_lang in (("zh-Hans", "zh-TW"), ("zh-Hans", "zh-Hant"), ("pt", "es"), ("de", "en")):
            with self.subTest(page_lang):
                b, _ = self.found("<p>Hello there.</p>", "<p>Ol\u00e1 la.</p>", lang, page_lang)
                self.assertTrue([m for m in b if "html lang" in m], page_lang)

    def test_leading_bom_is_not_a_block(self):
        p = vt.parse_page("\ufeff<!doctype html><html><main><p>x</p></main></html>")
        self.assertEqual(p.loose["outside"].text.strip(), "")
        self.assertEqual(vt.aligned_blocks(p, p)[0][1].tag, "p")


class BlockHashes(unittest.TestCase):
    """No-sidecar back-translation records are tied to the block text by sha256."""
    DIR = FIX / "nosidecar"

    def test_export_writes_hashes_outside_translation_only(self):
        with tempfile.TemporaryDirectory() as d:
            n = export_blocks.export(self.DIR / "en.html", self.DIR / "pt.html", Path(d))
            hashes = json.loads((Path(d) / "blocks_sha256.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(os.listdir(Path(d) / "translation-only")), ["blocks_tr.txt"])
        self.assertEqual(len(hashes), n + 2)  # the blocks, title and description
        bt = json.loads((self.DIR / "back_translation.json").read_text(encoding="utf-8"))
        self.assertEqual({(f"B{r['block']}" if isinstance(r["block"], int) else r["block"]): r["sha256"] for r in bt}, hashes)

    def test_block_edited_after_the_round_blocks(self):
        en, pt = ((self.DIR / f).read_text(encoding="utf-8") for f in ("en.html", "pt.html"))
        bt = json.loads((self.DIR / "back_translation.json").read_text(encoding="utf-8"))
        self.assertEqual(vt.check_pages(en, pt, PT, bt, "model-a"), [])
        edited = pt.replace("guard\u00e1-las.", "guard\u00e1-las. Isso ajuda muito.")
        self.assertNotEqual(edited, pt)
        found = [f["message"] for f in vt.check_pages(en, edited, PT, bt, "model-a") if f["level"] == "blocker"]
        self.assertEqual(found, ["B3 changed since it was back-translated; re-export and redo it"])

    def test_record_without_sha256_is_input_error(self):
        with self.assertRaises(ValueError):
            vt.check_pages("<main></main>", "<main></main>", PT, [{"block": 1, "round": 2, "verdict": "MATCH", "model": "m"}], "x")


if __name__ == "__main__":
    unittest.main()
