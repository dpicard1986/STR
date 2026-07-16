"""STR Deal-Screening Agent — daily run.

Usage:
  python -m str_agent.main --demo                       # offline run on data/demo_listings.csv
  python -m str_agent.main --source gmail               # production: parse portal alert emails
  python -m str_agent.main --demo --markets killington,hampton_beach

If ANTHROPIC_API_KEY is set, every run also asks Claude to search Redfin live for
current top candidates (str_agent/live_search.py) and adds them to the digest with
links. Pass --no-live-search to skip that even when the key is set.
"""
from __future__ import annotations
import argparse, datetime, os, sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from . import db as dbm
from . import ingest
from . import live_search
from .underwrite import underwrite, load_csv
from .score import score_pool
from .digest import render, deliver

FRACTIONAL_WORDS = ("quarter share", "quarter-share", "fractional", "timeshare", "1/4 share")


def load_cfg(path="config.yaml"):
    text = Path(path).read_text()
    if yaml:
        return yaml.safe_load(text)
    return _mini_yaml(text)


def _mini_yaml(text):
    """Dependency-free fallback for this config's simple two-level structure."""
    cfg, section = {}, None
    for raw in text.splitlines():
        line = raw.split("#")[0].rstrip()
        if not line.strip():
            continue
        if not raw.startswith(" ") and line.endswith(":"):
            section = line[:-1].strip()
            cfg[section] = {}
        elif ":" in line and section:
            k, v = line.split(":", 1)
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                val = [x.strip() for x in v[1:-1].split(",") if x.strip()]
            elif v.lower() in ("true", "false"):
                val = v.lower() == "true"
            elif v in ("", "null", '""'):
                val = None
            else:
                try:
                    val = float(v) if "." in v or "e" in v.lower() else int(v)
                except ValueError:
                    val = v.strip('"')
            cfg[section][k.strip()] = val
    return cfg


def hard_filter(l, cfg):
    s = cfg["search"]
    if l["state"] not in s["states"]:
        return "state"
    if float(l["price"]) > s["max_price"]:
        return "price"
    if s.get("reject_fractional") and any(w in str(l.get("remarks", "")).lower() for w in FRACTIONAL_WORDS):
        return "fractional"
    mkts = s.get("markets")
    if mkts and l.get("market") not in mkts:
        return "market"
    # Distance check runs when lat/lon present; demo listings are pre-assigned to markets.
    return None


def run(args):
    cfg = load_cfg(args.config)
    if args.markets:
        cfg["search"]["markets"] = [m.strip() for m in args.markets.split(",")]
    comps = load_csv("comps.csv")
    towns = load_csv("towns.csv")
    markets = load_csv("markets.csv")
    today = datetime.date.today().isoformat()

    if args.source == "gmail":
        raw = ingest.from_gmail()
    else:
        raw = ingest.from_csv(args.listings)

    con = dbm.connect()
    events, pool = [], []
    for l in raw:
        reason = hard_filter(l, cfg)
        key, event = dbm.upsert_listing(con, l, today)
        if reason:
            con.execute("UPDATE listings SET out_of_scope=1 WHERE key=?", (key,))
            continue
        l["cum_price_cut_pct"] = max(float(l.get("cum_price_cut_pct", 0) or 0), dbm.cum_cut_pct(con, key))
        uw = underwrite(l, cfg, comps, towns, markets)
        if uw is None:
            continue
        pool.append((l, uw))
        if event == "price_cut":
            hist = con.execute("SELECT price FROM price_history WHERE key=? ORDER BY seen", (key,)).fetchall()
            events.append({"event": "price_cut", "address": l["address"], "old": hist[-2][0],
                           "new": hist[-1][0], "cum": l["cum_price_cut_pct"], "key": key})
        elif event == "new":
            events.append({"event": "new", "address": l["address"], "price": float(l["price"]), "key": key})

    ranked = score_pool(pool, cfg)
    for r in ranked:
        key = dbm.norm_key(r["listing"]["address"])
        dbm.save_score(con, key, today, r)
        for e in events:
            if e.get("key") == key and e["event"] == "price_cut":
                e["new_score"] = r["score"]

    live_candidates = []
    if not args.no_live_search and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            live_candidates = live_search.find_candidates(cfg)
        except Exception as e:  # noqa: BLE001
            print(f"live search failed: {e}")

    html = render(ranked, events, cfg, today, cfg["search"].get("markets"), live_candidates)
    print(deliver(html, cfg, today))
    for i, r in enumerate(ranked, 1):
        u = r["uw"]
        print(f"{i:>2}. {r['score']:5.1f}  VA {r['va']:3.0f}  ATROI {u['atroi']*100:5.1f}%  "
              f"CoC {u['coc']*100:5.1f}%  IRR10 {u['irr10']*100:5.1f}%  {r['listing']['address']}")
    return ranked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--source", choices=["csv", "gmail"], default="csv")
    ap.add_argument("--listings", default="data/demo_listings.csv")
    ap.add_argument("--markets", default=None, help="comma-separated market whitelist override")
    ap.add_argument("--demo", action="store_true", help="alias for --source csv with demo data")
    ap.add_argument("--no-live-search", action="store_true",
                     help="skip the Claude web-search candidate section even if ANTHROPIC_API_KEY is set")
    args = ap.parse_args()
    if args.demo:
        args.source = "csv"
    run(args)


if __name__ == "__main__":
    sys.exit(main())
