from __future__ import annotations

import hashlib
import math
import re

VECTOR_DIM = 128
TOKEN_PATTERN = re.compile(r"[a-z0-9_./-]+")


def _tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def embed_text(text: str, dim: int = VECTOR_DIM) -> list[float]:
    vector = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vector

    for token in tokens:
        digest = hashlib.sha1(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[idx] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]
