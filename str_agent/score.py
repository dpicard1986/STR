"""Composite scoring: percentile-normalized metrics + value-add multiplier."""
from __future__ import annotations

VA_KEYWORDS = ["tlc", "original condition", "estate", "as-is", "as is", "bring your vision",
               "priced to sell", "bonus space", "bonus livable", "unfinished", "expansion potential",
               "handyman", "dated", "sweat equity"]
TURNKEY_KEYWORDS = ["fully renovated", "turn-key", "turnkey", "completely renovated", "designer"]


def percentile(value, pool):
    if len(pool) <= 1:
        return 50.0
    below = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return 100.0 * (below + 0.5 * equal) / len(pool)


def value_add_score(listing, uw, pool_uw_by_market):
    """0-100. Signals: $/sqft vs market pool median, remarks keywords, DOM, cuts."""
    score = 50.0
    mkt = listing["market"]
    peers = [u["price_sqft"] for (l, u) in pool_uw_by_market if l["market"] == mkt and l is not listing]
    if peers:
        med = sorted(peers)[len(peers) // 2]
        disc = (med - uw["price_sqft"]) / med
        score += max(min(disc / 0.15, 1.5), -1.0) * 23  # +-, 15% discount ~ +23 pts, cap 1.5x
    remarks = str(listing.get("remarks", "")).lower()
    if any(k in remarks for k in VA_KEYWORDS):
        score += 15
    if any(k in remarks for k in TURNKEY_KEYWORDS):
        score -= 15
    try:
        cut = float(listing.get("cum_price_cut_pct", 0) or 0)
    except ValueError:
        cut = 0
    if cut >= 0.05:
        score += 10
    elif cut > 0:
        score += 5
    try:
        dom = float(listing.get("dom", 0) or 0)
    except ValueError:
        dom = 0
    if dom > 90:
        score += 8
    return max(0.0, min(100.0, score))


def composite(listing, uw, va, pools, cfg):
    s = cfg["scoring"]
    base = (s["w_atroi"] * percentile(uw["atroi"], pools["atroi"])
            + s["w_cashflow"] * percentile(uw["coc"], pools["coc"])
            + s["w_irr10"] * percentile(uw["irr10"], pools["irr10"]))
    final = base * (1 + s["va_swing"] * (va - 50) / 50)
    return round(max(0.0, min(100.0, final)), 1)


def score_pool(rows, cfg):
    """rows: list of (listing, uw). Returns list of dicts with va + composite, ranked."""
    pools = {
        "atroi": [u["atroi"] for _, u in rows],
        "coc": [u["coc"] for _, u in rows],
        "irr10": [u["irr10"] for _, u in rows],
    }
    out = []
    for listing, uw in rows:
        va = value_add_score(listing, uw, rows)
        sc = composite(listing, uw, va, pools, cfg)
        out.append({"listing": listing, "uw": uw, "va": round(va, 0), "score": sc})
    out.sort(key=lambda r: -r["score"])
    return out
