#!/usr/bin/env python3
"""
export_blocks.py: write an English page and its translation as numbered, aligned
blocks (B1, B2, ...) plus title and description, for a back-translation round.

The translation file goes into its own directory, so the back-translator can be
given read access to that directory alone and never sees the English. The block
numbers come from verify_translation's parser, so B<n> on one side is B<n> on the
other. Exits 2 when the two pages do not have the same block sequence; fix the
structure first (verify_translation.py reports it).

Usage: export_blocks.py <english> <translation> <out-dir>   (HTML, PHP or Markdown)
Writes <out-dir>/blocks_en.txt, <out-dir>/translation-only/blocks_tr.txt and
<out-dir>/blocks_sha256.json ({"B1": sha256, ..., "title": sha256, "description":
sha256}, each of the English and translated text together). Each back-translation
record copies its unit's sha256 (block 1 for B1, block "title" for the title), so
editing either page after the round no longer matches its record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_translation as vt  # noqa: E402
from block_checks import aligned_blocks, block_sha256, block_text, bt_units  # noqa: E402


def export(en_path: Path, tr_path: Path, out: Path) -> int:
    """Write both block files and the hash of every back-translated unit (blocks, title,
    description); return the number of aligned non-empty blocks."""
    pages = {k: vt.parse_page(vt.read_page(p)) for k, p in (("en", en_path), ("tr", tr_path))}
    units = bt_units(pages["en"], pages["tr"])
    if units is None:
        raise ValueError("the pages do not have the same block sequence; run verify_translation.py")
    lines = {k: [f"[title] {p.head.get('title', '')}", f"[description] {p.head.get('description', '')}"]
             for k, p in pages.items()}
    for n, a, b in aligned_blocks(pages["en"], pages["tr"]):
        lines["en"].append(f"B{n} <{a.tag}> {block_text(a.text)}")
        lines["tr"].append(f"B{n} <{b.tag}> {block_text(b.text)}")
    (out / "translation-only").mkdir(parents=True, exist_ok=True)
    (out / "blocks_en.txt").write_text("\n".join(lines["en"]) + "\n", encoding="utf-8")
    (out / "translation-only" / "blocks_tr.txt").write_text("\n".join(lines["tr"]) + "\n", encoding="utf-8")
    hashes = {name: block_sha256(e, t) for name, _, e, t in units}
    (out / "blocks_sha256.json").write_text(json.dumps(hashes, indent=1) + "\n", encoding="utf-8")
    return sum(isinstance(key, int) for _, key, _, _ in units)


def main(argv=None) -> int:
    """CLI entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        n = export(Path(args[0]), Path(args[1]), Path(args[2]))
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{n} aligned blocks written to {args[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
