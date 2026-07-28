#!/usr/bin/env python
"""Vendor a pinned CMUdict snapshot into the decoder package.

§3: "Pin the dict — it is part of the proof." The decodability guarantee is only
reproducible if the lexicon it was proved against is byte-identical, so the
dictionary is committed to the repo rather than resolved from the environment at
runtime. The `cmudict` PyPI package is a *build-time* tool used to obtain the
snapshot; it is never imported by the decoder.

Run from the repo root:

    cd services/api && uv run python ../../scripts/vendor-cmudict.py

Re-running with a different upstream version rewrites data/PINS.md, which will
show up in review as an intentional change to the proof surface.
"""

from __future__ import annotations

import hashlib
import inspect
import shutil
from pathlib import Path

# Files copied verbatim from the upstream package. LICENSE is included because
# CMUdict's BSD-style terms require the notice to travel with redistribution.
ARTIFACTS = ("cmudict.dict", "cmudict.phones", "cmudict.symbols", "LICENSE")

DEST = Path(__file__).resolve().parent.parent / "services/api/storyforge/decoder/data"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    import cmudict

    import importlib.metadata

    # Data files live under the package's data/ subdirectory; LICENSE sits at the
    # package root. Resolve each by search rather than assuming a fixed layout.
    pkg_dir = Path(inspect.getfile(cmudict)).parent
    version = importlib.metadata.version("cmudict")

    DEST.mkdir(parents=True, exist_ok=True)

    rows = []
    for name in ARTIFACTS:
        matches = [p for p in pkg_dir.rglob(name) if p.is_file()]
        if not matches:
            raise SystemExit(f"upstream artifact missing: {name} under {pkg_dir}")
        if len(matches) > 1:
            raise SystemExit(f"ambiguous upstream artifact {name}: {matches}")
        src = matches[0]
        dst = DEST / name
        shutil.copy2(src, dst)
        rows.append((name, dst.stat().st_size, sha256(dst)))
        print(f"vendored {name:<18} {dst.stat().st_size:>10,} bytes")

    pins = [
        "# Pinned proof artifacts",
        "",
        "The decodability guarantee (§5) is only reproducible against a fixed",
        "lexicon, so CMUdict is vendored rather than resolved at runtime (§3:",
        '"Pin the dict — it is part of the proof").',
        "",
        f"Source: `cmudict` PyPI package version **{version}**",
        "Regenerate with `scripts/vendor-cmudict.py`.",
        "",
        "`tests/decoder/test_lexicon.py` asserts these digests on every run, so a",
        "silent change to the proof surface fails the suite.",
        "",
        "| Artifact | Bytes | SHA-256 |",
        "|---|---:|---|",
    ]
    pins += [f"| `{n}` | {s:,} | `{h}` |" for n, s, h in rows]
    pins.append("")

    (DEST / "PINS.md").write_text("\n".join(pins), encoding="utf-8")
    print(f"\nwrote {DEST / 'PINS.md'}")


if __name__ == "__main__":
    main()
