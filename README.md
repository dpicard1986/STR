# STR Deal-Screening Agent

Daily screener for New England ski/beach short-term rental deals. Ingests listings,
underwrites each against complex-level rental comps, scores composite ROI
(50% year-1 after-tax ROI / 20% year-1 cash flow / 30% 10-yr IRR, ±30% value-add multiplier),
tracks price cuts in SQLite, and emails a ranked digest.

## Quick start (offline demo)

```bash
python3 -m str_agent.main --demo
python3 -m str_agent.main --demo --markets killington,hampton_beach   # filtered run
open out/digest_*.html
```

No dependencies required for the demo (PyYAML optional). All assumptions in `config.yaml`.

## Production setup

1. **Portal alerts → Gmail.** Create saved searches on Zillow/Redfin/Realtor for each market
   (price ≤ $800K, condos+houses). Set alert frequency to instant/daily. In Gmail, create a
   filter routing them to label `str-alerts`.

2. **Gmail App Password.** Go to myaccount.google.com → Security → 2-Step Verification →
   App passwords, generate one, and save it (spaces removed) in a file named
   `.gmail_app_password` in this folder — it's git-ignored. Then:
   ```bash
   pip install pyyaml
   ```
   Set `email.enabled: true` and `email.to:` in config.yaml. No OAuth, no Google Cloud
   project, no token expiry — ingest reads via IMAP, digest sends via SMTP.

3. **Run daily.**
   ```bash
   python3 -m str_agent.main --source gmail
   ```

## Live picks (Claude web search)

If `ANTHROPIC_API_KEY` is set in the environment, every run also asks Claude to search
Redfin live for the top candidates matching `config.yaml`'s filters and adds them to the
digest as a "Live picks" section with Redfin links — independent of the Gmail/CSV
ingestion pipeline, so it works even before saved-search alerts are set up. It's a fresh
search each run, so the same candidates showing up again the next day is expected.

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python3 -m str_agent.main --source gmail   # or --demo
```

Pass `--no-live-search` to skip it even when the key is set. Model defaults to
`claude-opus-4-8`; override with `STR_AGENT_SEARCH_MODEL`.

## Scheduling

**Mac (launchd):** `cp com.dave.stragent.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.dave.stragent.plist`

**Linux/VPS (cron):** `crontab -e` →
```
0 7 * * * cd /path/to/str-agent && /usr/bin/python3 -m str_agent.main --source gmail >> out/run.log 2>&1
```

## Maintaining data quality (the part that matters)

- `data/comps.csv` — the revenue engine. Add 5–10 real Airbnb/VRBO comps per complex you care
  about; sample seasonal rates monthly. Confidence grades (A/B/C) flow into the digest.
- `data/towns.csv` — verify tax rates annually; VT non-homestead rates move.
- `data/markets.csv` — appreciation assumptions per market drive the 10-yr IRR leg.
- `out/parse_misses.log` — if portals change their email HTML, patterns in
  `str_agent/ingest.py::parse_alert_html` need a tweak.

## Files

```
config.yaml            all assumptions (financing, tax profile, weights, thresholds)
data/                  markets, towns/tax rates, comps, demo listings
str_agent/main.py      pipeline + CLI (--markets filter, --demo)
str_agent/underwrite.py revenue, costs, tax module (cost seg, 100% bonus, recapture), IRR
str_agent/score.py     percentiles, value-add detection, composite
str_agent/db.py        SQLite: listings, price history, score history
str_agent/digest.py    HTML digest + Gmail send
str_agent/ingest.py    Gmail alert parsing / CSV mode
str_agent/live_search.py  Claude web search for live Redfin candidates
out/                   agent.db, daily digests, logs
```

## Notes

- Tax module assumes: MA resident MFJ top bracket, material participation, personal use
  < 14 days (no §280A allocation), 25% cost seg, 100% bonus depreciation, recapture on exit.
  This is a screening model, not tax advice — confirm with your CPA before filing positions.
- Regulatory status is flagged in output but intentionally excluded from scoring (config choice).
- Scores are percentile-relative to the active pool: they shift as inventory and filters change.
