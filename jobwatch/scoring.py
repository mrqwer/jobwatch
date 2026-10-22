import re


def strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def score(title: str, body: str, weights: dict[str, float]) -> float:
    """Sum of keyword weights. A keyword counts once; in the title it counts double.
    Negative weights push unwanted roles down instead of hard-dropping them."""
    t, b = title.lower(), body.lower()
    total = 0.0
    for word, w in weights.items():
        pat = rf"(?<!\w){re.escape(str(word).lower())}(?!\w)"
        if re.search(pat, t):
            total += 2 * w
        elif re.search(pat, b):
            total += w
    return total
