# STR Deal-Screening Agent — Specification v1.0

Daily agent that screens new and changed listings in New England ski/beach markets,
underwrites each against real rental comps, scores composite ROI, and emails a ranked digest.

---

## 1. Search profile (hard filters)

| Filter | Rule |
|---|---|
| States | VT, NH, MA, ME |
| Price | ≤ $800,000 (config: `max_price`) |
| Location | ≤ 2.0 mi from a ski lift base OR ≤ 0.25 mi from public beach access |
| Ownership | Whole ownership or condotel. AUTO-REJECT: quarter-share / fractional / timeshare (keyword + deed-type detection) |
| Property type | Condo, townhouse, single-family |
| STR legality | Must be permitted at town level and by HOA if detectable. Regulatory risk is FLAGGED in output but NOT scored |
| Markets | Optional whitelist, config `markets: [...]` and CLI override `--markets killington,hampton_beach`. When set, only listings mapped to those markets are underwritten and ranked; others stored as out-of-scope (not deleted, so widening the filter later back-fills instantly). Market assignment comes from the geo tables (nearest lift/beach anchor). Current whitelist: killington, okemo, loon_lincoln, sunday_river, hampton_beach, old_orchard_beach |

**Geo tables (static config, maintained manually):**
- `lifts.csv` — lat/long of base areas: Killington (Snowshed, Ramshead, Bear Mtn), Pico, Okemo (Clocktower, Jackson Gore), Stratton, Mount Snow, Smugglers' Notch, Stowe, Sugarbush, Loon, Waterville Valley, Cannon, Bretton Woods, Attitash, Wildcat, Cranmore, Gunstock, Sunday River (South Ridge, Barker, White Cap), Sugarloaf, Wachusett, Jiminy Peak, Berkshire East
- `beach_access.csv` — lat/long of access points: Hampton Beach NH, Salisbury MA, Old Orchard Beach ME, Wells ME, York/Ogunquit ME, Higgins Beach ME, Cape Cod towns (Dennis, Yarmouth, Falmouth — STR-friendly subset), Plum Island MA, Newburyport MA
- Distance = haversine from listing lat/long; if listing coords missing, geocode the address (Nominatim/OSM, free)

## 2. Data ingestion

**Primary (free, reliable):** Saved-search email alerts parsed from Gmail.
- One saved search per portal per market (Zillow, Redfin, Realtor.com), criteria mirroring the hard filters loosely (agent re-filters precisely)
- Daily job reads the alerts label via Gmail API, extracts: address, price, beds/baths, sqft, listing URL, photos count, status, remarks text
- Listing detail page fetched once per new listing for: HOA fee, taxes, days on market, full remarks (respect robots.txt; fall back to alert-email data if blocked)

**Change detection:** SQLite table `listings` keyed on normalized address + unit.
- New listing → full underwrite
- Price change → re-underwrite + PRICE CUT alert (store full price history; report cut size, % and cumulative)
- Status change (pending/sold/back-on-market) → note in digest; back-on-market is a value-add signal
- Delisted → archive

## 3. Revenue engine (no paid data)

**Complex-level comp sets.** For each condo complex / micro-neighborhood in the geo tables, maintain a comp table of 5–10 actual Airbnb/VRBO listings:
- Fields: listing URL, bedrooms, sleeps, nightly rates sampled across 4 season buckets (peak winter, shoulder, peak summer, off), cleaning fee, review count, occupancy proxy (calendar-blocked % sampled monthly)
- Refresh cadence: weekly rate sample; monthly occupancy sample
- Seed set: compiled manually on first run (Whiffletree, Highridge, Trail Creek, Northbrook, Sunrise, Mountain Green, Kettlebrook, Winterplace, Trailside, Clearbrook, Village of Loon, Forest Ridge, Fall Line/Brookside SR, Grand Victorian, Brunswick, Ashworth Ave corridor, Jonathan's)

**Revenue build per candidate:**
```
gross = Σ_seasons (nights_available × occ_season × ADR_season)
```
- ADR/occ from the matched complex comp set, bedroom-matched (±1 BR with $/BR adjustment)
- No complex match → fall back to market-tier table (town × bedroom), seeded from public market stats, e.g. Killington ~42% occ / condo ADR $250–450; Lincoln ~44% occ / $340 ADR; Old Orchard Beach ~64% occ / $378 ADR — refreshed quarterly
- Haircuts: 3% OTA fees, 4% supplies/consumables, 5% maintenance/repairs, 2% vacancy shock. Cleaning fees treated as pass-through (excluded from both sides)
- Confidence grade A/B/C on every revenue estimate (A = ≥5 same-complex, same-BR comps)

## 4. Underwriting model

**Financing (config):** 20% down, 30-yr fixed, rate = `mortgage_rate` (refresh weekly from FRED/Mortgage News Daily), +50 bps and 25–30% down if condotel flag set. Closing costs 3%.

**Operating costs:** town tax rate table (VT non-homestead rates — Killington/Ludlow ~1.6–2.3%; NH town rates; ME; MA), HOA from listing (impute complex median if missing, flag imputed), insurance est. 0.35% of price (0.6% coastal/flood-zone flag), utilities $4.5–6K/yr by size, internet/software $1.2K. Self-managed: 0% management fee.

**Tax module (owner profile — config):**
- MA resident, MFJ, top federal bracket; MA 5% + 4% surtax over $1M threshold
- Material participation = TRUE (self-managed, avg stay ≤ 7 days targeted) → losses non-passive under §469
- Personal use < 14 days → NOT a §280A residence; no expense allocation haircut
- Cost segregation: 25% of improvement basis to 5/7/15-yr property (config: `cost_seg_pct`); improvement basis = price × (1 − land%), land% by property type (condo 10%, SFH 20%)
- Bonus depreciation: 100% (permanent for property acquired after 1/19/2025 under 2025 tax law)
- Straight-line 27.5-yr on remainder
- Year-1 tax shield = (bonus + SL + operating loss) × marginal rate
- Exit model: 10-yr hold, sale at appreciated value, depreciation recapture at 25% (§1250) / ordinary (§1245 on cost-seg property), federal + MA cap gains, optional §1031 toggle

**Outputs per property:**
1. `cf_y1` — Year-1 pre-tax cash flow ($ and CoC % on cash-in = down + closing)
2. `atroi_y1` — Year-1 after-tax ROI = (cash flow + tax shield) / cash-in
3. `irr_10` — 10-yr levered after-tax IRR (appreciation config: `appreciation_rate` default 3%, market-adjustable; principal paydown; terminal sale w/ recapture)
4. DSCR, breakeven occupancy, price/sqft vs complex median

## 5. Scoring

Normalize each metric to a 0–100 percentile within the current active candidate pool.
**When a market whitelist is active, percentiles renormalize within the filtered pool only** — scores are relative rankings, so a property's score will shift when the filter changes; the digest header states the active market scope.

```
base = 0.50 × pct(atroi_y1) + 0.20 × pct(cf_y1) + 0.30 × pct(irr_10)
final = base × (1 + 0.30 × (value_add_score − 50) / 50)    # ±30% swing
```

**Value-Add Score (0–100), heavily weighted via multiplier. Signals:**
- Price/sqft ≥ 15% below same-complex/micro-market median (strongest signal, 35 pts scaled)
- Remarks keywords: "TLC", "original condition", "estate", "as-is", "bring your vision", "priced to sell", "bonus space", "unfinished", "expansion potential" (20 pts)
- Spread vs renovated comps: recent renovated sale in same complex ≥ 20% higher $/sqft (20 pts)
- Days on market > 1.5× market median, or back-on-market (10 pts)
- Cumulative price cuts ≥ 5% (10 pts)
- Photo-count heuristic: very low photo count often = dated interior (5 pts)
- PENALTY: "fully renovated / turn-key" with $/sqft at complex ceiling → score < 40
- When VA score > 70, add a rough reno-uplift line to the digest: est. reno $ (config $/sqft by scope) vs. implied post-reno value from renovated comps

**Regulatory flag (displayed, never scored):** town STR status = permissive / registration required / pending ordinance / restrictive; source note + last-checked date. Maintained table, re-verified quarterly.

## 6. Daily email digest (via Gmail API, sends every morning)

```
Subject: STR Deals — {date} — {n_new} new, {n_cuts} cuts, top score {x}

1) TOP RANKED (max 5, score-ordered)
   Address | Price | BR/BA | Mkt | Score | ATROI | CoC | IRR10 | VA | Reg flag | link
2) PRICE CUTS on tracked properties
   Address | old → new (−$, −%) | cumulative cut | new score | Δscore
3) NEW TODAY (all passing hard filters, one line each)
4) STATUS CHANGES (pending, sold — with sold $/sqft fed back into comp tables)
5) WATCHLIST movers (score changed ≥ 10 pts, e.g., comp-set rate refresh)
```
Threshold ping: any property with final score ≥ 85 or ATROI ≥ 30% triggers an immediate (not batched) email.

## 7. Operations

- Schedule: daily 7:00 AM ET (cron / launchd); comp refresh weekly Sun; market stats quarterly
- Storage: single SQLite db (listings, price_history, comps, scores, towns, geo)
- Config: `config.yaml` — all assumptions above (rate, weights, appreciation, cost_seg_pct, thresholds)
- Failure handling: portal/scrape failures degrade gracefully to email-alert data; digest always sends with a data-freshness header; log parse failures for manual review
- Every digest line links to the listing and to a per-property one-page underwrite (rendered HTML/PDF)

## 8. Known limitations / v2 candidates

- Scraped comp occupancy is a proxy (calendar-blocked ≠ booked); revenue confidence grades communicate this
- HOA special-assessment risk (e.g., Mountain Green's ~$50M program) requires manual flag entry — `complex_flags` table supported
- MLS-grade data via broker API (Repliers/SimplyRETS) is the clean upgrade if email parsing proves lossy
- Insurance quotes are estimates; coastal properties warrant a real quote before offer
