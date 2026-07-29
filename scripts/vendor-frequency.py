#!/usr/bin/env python
"""Vendor a pinned word-frequency list.

§3 specifies SUBTLEX-US. Its canonical hosts (OSF, the Ghent mirror) were not
reachable when this was built, so the list is derived from the `wordfreq`
package instead and written to the same CSV shape — see docs/DEVIATIONS.md 2.1.
Nothing downstream knows the difference; swapping the source back to SUBTLEX
means re-running this script and reviewing the PINS.md diff.

Like CMUdict, this is proof surface: it decides which words may reach a
generation prompt at all. So it is vendored and SHA-pinned rather than resolved
at runtime, and `wordfreq` is a build-time tool the API never imports.

Run:  cd services/api && uv run python ../../scripts/vendor-frequency.py
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

# §5.4: "intersect with SUBTLEX top ~20k".
TOP_N = 20_000

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "data/frequency-en.csv"


def main() -> None:
    from wordfreq import top_n_list, zipf_frequency

    words = [w for w in top_n_list("en", TOP_N) if w.isalpha()]

    DEST.parent.mkdir(parents=True, exist_ok=True)
    with DEST.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "word", "zipf"])
        for rank, word in enumerate(words, start=1):
            writer.writerow([rank, word, f"{zipf_frequency(word, 'en'):.3f}"])

    digest = hashlib.sha256(DEST.read_bytes()).hexdigest()
    size = DEST.stat().st_size
    print(f"vendored {DEST.relative_to(ROOT)}  {len(words):,} words  {size:,} bytes")
    print(f"sha256 {digest}")

    # Append to the decoder's PINS.md so one test asserts every proof artifact.
    pins = ROOT / "services/api/storyforge/decoder/data/PINS.md"
    text = pins.read_text(encoding="utf-8")
    # Repo-relative path, because this artifact lives outside the decoder
    # package — it is precompute input, not something the validator reads.
    row = f"| `data/frequency-en.csv` | {size:,} | `{digest}` |"
    if "frequency-en.csv" in text:
        import re

        text = re.sub(r"^\| `frequency-en\.csv` \|.*$", row, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + row + "\n"
    pins.write_text(text, encoding="utf-8")
    print(f"pinned in {pins.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
