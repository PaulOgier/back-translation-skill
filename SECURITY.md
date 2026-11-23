# Security

The checker reads local files only, makes no network calls and runs no model. In
no-sidecar mode it refuses page paths that escape `--root`, and it caps every input at
5 MB.

Its threat model is an honest translator (a model) making mistakes, not an adversary
crafting markup to slip a change past the gate. Accepted limits are listed as
`known_gap` cases in `tests/fixtures/`. Do not rely on it to validate untrusted
third-party submissions.

Size is capped, time is not. On Python 3.9 the standard library HTML parser is slow on
some malformed markup (an unclosed attribute or comment repeated many thousands of
times), and a 5 MB page can use several hundred MB of memory. Run it on the newest
Python you have, on pages you wrote or translated.

The translate and back-translate steps send your article to whichever model providers
you choose. Do not use it on text you are not allowed to send to them.

Report a vulnerability through GitHub's private vulnerability reporting on this
repository.
