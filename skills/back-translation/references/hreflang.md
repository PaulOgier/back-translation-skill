# Wiring a translated page in for search

Summarised from Google Search Central, "Managing multi-regional and multilingual sites"
and "Tell Google about localized versions of your page"
(https://developers.google.com/search/docs/specialty/international/localized-versions),
read November 2026. Re-read the source before relying on any line here.

- **URL.** Own URL per language in a subdirectory (`/pt/<same-slug>`); English stays
  where it is. Google calls URL parameters for language "Not recommended". No content
  switched by cookie or browser language.
- **Canonical.** Each language version is self-canonical, absolute. Google: "If you're
  using hreflang elements, make sure to specify a canonical page in the same language"
  (https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls).
- **hreflang.** One method only (HTML `<link>` tags are simplest). The identical set goes
  on every version, itself included, absolute https, plus `x-default` (usually the
  English). Return links are mandatory: if two pages do not both point to each other,
  the tags are ignored. With one version of a language use the bare code (`pt`, not
  `pt-BR`) so other regions still match. `es-419` is not supported; use `zh-Hans` /
  `zh-Hant` for script.
- **Only real translations get a language URL.** Google warns against "Translating only
  the boilerplate text of your pages while keeping the bulk of your content in a single
  language".
- **One language per page.** Translate the chrome too: navigation, footer, skip link,
  breadcrumb, headings of the sources block and its dates. Source quotes in the sources
  block stay in English, labelled as English.
- **Language signals.** `<html lang>`, title, description, `og:locale` and
  `og:locale:alternate`, schema `inLanguage`. Google: it "doesn't use hreflang or the
  HTML lang attribute to detect the language of a page"; it reads the content.
- **Sitemap.** A plain `<loc>` for the language URL is enough when hreflang is in the
  HTML.
- **Switcher.** Plain `<a href>` links to the same page in each language, names in their
  own language, no flags. Google: "Avoid automatically redirecting users from one language
  version of a site to a different language version". A dismissible suggestion banner
  is fine.
- **Disclosure** on every translated page: translated with AI from the English original,
  which is the reference, with a link to it and a way to report a translation error.

After publishing, inspect the language URL in Search Console once it has been crawled:
the Google-selected canonical must be the URL itself, or Google is treating the
translation as a duplicate.
