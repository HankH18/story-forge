# Pinned proof artifacts

The decodability guarantee (§5) is only reproducible against a fixed
lexicon, so CMUdict is vendored rather than resolved at runtime (§3:
"Pin the dict — it is part of the proof").

Source: `cmudict` PyPI package version **1.1.3**
Regenerate with `scripts/vendor-cmudict.py`.

`tests/decoder/test_lexicon.py` asserts these digests on every run, so a
silent change to the proof surface fails the suite.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `cmudict.dict` | 3,618,488 | `81917843c7f44ce2b094ac63873c2c7a4cf802040792c455ba3ca406891c3d22` |
| `cmudict.phones` | 382 | `ffb588a5e55684723582c7256e1d2f9fadb130011392d9e59237c76e34c2cfd6` |
| `cmudict.symbols` | 281 | `408ccaae803641c6d7b626b6299949320c2dbca96b2220fd3fb17887b023b027` |
| `LICENSE` | 1,754 | `bd4ce8e44170a5f9f481310ca85c51de3c4f851a65e679b40e603b143bd3542a` |
| `data/frequency-en.csv` | 398,644 | `da0f3d22b268ac78dec64063dae40f75b965e4dfc512e3414d24e03d90d0469e` |
