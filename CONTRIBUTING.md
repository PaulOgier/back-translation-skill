# Contributing

## Adding a language

1. Copy `skills/back-translation/scripts/languages/pt.json` to `<code>.json` (a BCP 47
   code such as `fr`, `ja`, `zh-Hant`).
2. Fill in `decimal` and `group` separators, `scale_words` (with their values: English
   "billion" is 10^9; check the target word means the same), `number_words`, `units`,
   `comparators`, `months` and `date_patterns`, `negators`, and the `normative` map: the forms that render must, must not, should, should not, may
   and need not. List ambiguous forms under `forbidden` with the reason (German "muss
   nicht" means "need not", not "must not").
3. Add tests in `tests/` with a clean page pair and, for every rule, a positive control:
   a translation that breaks the rule and must block. A rule without a failing control
   is not tested.
4. Run the tests on the oldest and newest supported Python:

       python3.9 -m unittest discover -s tests
       python3 -m unittest discover -s tests

5. Say in the pull request who checked the language rules and against what source.

## Changing the checker

Keep it standard-library only and Python 3.9 compatible. Every new check needs a
positive control. The threat model is an honest translator making mistakes; known
accepted gaps are listed as `known_gap` cases in `tests/fixtures/`.

## Style

Plain English, no em dashes. Docstring every public function: what it does and why.
