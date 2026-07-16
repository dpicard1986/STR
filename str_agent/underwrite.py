"""Underwriting engine: revenue from comps, costs, tax module, year-1 and 10-yr returns."""
from __future__ import annotations
import csv, math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

SEASON_NIGHTS = {"winter": 90, "summer": 75, "shoulder": 100, "off": 100}


def load_csv(name):
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f))


def town_tax_rate(town, state, towns):
    for t in towns:
        if t["town"].lower() == town.lower() and t["state"] == state:
            return float(t["tax_rate_pct"])
    return 0.015  # conservative default; flag in output


def match_comp(listing, comps):
    """Complex + bedroom match, then complex +/-1BR with $/BR adjust, then market tier."""
    cx = [c for c in comps if c["market"] == listing["market"]]
    exact = [c for c in cx if c["complex"] == listing["complex"] and int(c["bedrooms"]) == int(listing["beds"])]
    if exact:
        return exact[0], exact[0]["confidence"]
    same_cx = [c for c in cx if c["complex"] == listing["complex"]]
    if same_cx:
        c = min(same_cx, key=lambda r: abs(int(r["bedrooms"]) - int(listing["beds"])))
        adj = dict(c)
        d_br = int(listing["beds"]) - int(c["bedrooms"])
        for k in ("adr_winter", "adr_summer", "adr_shoulder", "adr_off"):
            adj[k] = str(float(c[k]) * (1 + 0.18 * d_br))  # ~18% ADR per bedroom
        return adj, "C"
    if cx:  # market tier: bedroom-nearest anywhere in market
        c = min(cx, key=lambda r: abs(int(r["bedrooms"]) - int(listing["beds"])))
        return c, "C"
    return None, "F"


def gross_revenue(comp):
    g = 0.0
    for s, nights in SEASON_NIGHTS.items():
        g += nights * float(comp[f"occ_{s}"]) * float(comp[f"adr_{s}"])
    return g


def amortize(principal, rate, years):
    m = rate / 12
    n = years * 12
    pay = principal * m / (1 - (1 + m) ** -n)
    return pay


def loan_balance(principal, rate, years, after_months):
    m = rate / 12
    n = years * 12
    pay = amortize(principal, rate, years)
    return principal * (1 + m) ** after_months - pay * (((1 + m) ** after_months - 1) / m)


def irr(cashflows, lo=-0.9, hi=1.5, tol=1e-6):
    def npv(r):
        return sum(cf / (1 + r) ** i for i, cf in enumerate(cashflows))
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < tol:
            return mid
        if f_lo * f > 0:
            lo, f_lo = mid, f
        else:
            hi = mid
    return (lo + hi) / 2


def underwrite(listing, cfg, comps, towns, markets):
    p = float(listing["price"])
    fin, op, tax, proj = cfg["financing"], cfg["operating"], cfg["tax"], cfg["projection"]
    condotel = str(listing.get("condotel", "0")) == "1"
    down_pct = fin["condotel_down_pct"] if condotel else fin["down_payment_pct"]
    rate = fin["mortgage_rate"] + (fin["condotel_rate_bump"] if condotel else 0)
    loan = p * (1 - down_pct)
    cash_in = p * down_pct + p * fin["closing_cost_pct"]
    pi_yr = amortize(loan, rate, fin["term_years"]) * 12
    interest_y1 = loan * rate  # close approximation for year 1

    taxes = p * town_tax_rate(listing["town"], listing["state"], towns)
    ins = p * (op["insurance_coastal_pct"] if str(listing.get("coastal", "0")) == "1" else op["insurance_pct_of_price"])
    util = min(max(float(listing["sqft"]) * op["utilities_per_sqft_yr"], op["utilities_min_yr"]), op["utilities_max_yr"])
    hoa = float(listing["hoa_annual"])
    opex = taxes + ins + util + hoa + op["internet_software_yr"]

    comp, conf = match_comp(listing, comps)
    if comp is None:
        return None
    gross = gross_revenue(comp)
    net_rev = gross * (1 - op["revenue_haircut_pct"]) * (1 - op["mgmt_fee_pct"])

    cf_y1 = net_rev - opex - pi_yr
    coc = cf_y1 / cash_in

    # Tax module: material participation, <14 days personal use -> full non-passive treatment
    land_pct = tax["land_pct_sfh"] if listing.get("property_type") == "sfh" else tax["land_pct_condo"]
    basis = p * (1 - land_pct)
    seg = basis * tax["cost_seg_pct"]
    bonus = seg * tax["bonus_pct"]
    sl = (basis - seg) / 27.5 * 0.5  # mid-year convention, year 1
    dep_y1 = bonus + sl
    taxable_y1 = net_rev - opex - interest_y1 - dep_y1
    shield_y1 = -taxable_y1 * tax["marginal_rate"] if taxable_y1 < 0 else 0.0
    atroi = (cf_y1 + shield_y1) / cash_in

    # 10-year after-tax IRR
    apprec = next((float(m["appreciation"]) for m in markets if m["market"] == listing["market"]), 0.03)
    flows = [-cash_in]
    dep_taken = dep_y1
    for yr in range(1, proj["hold_years"] + 1):
        rev = net_rev * (1 + proj["rent_growth"]) ** (yr - 1)
        ox = opex * (1 + proj["expense_growth"]) ** (yr - 1)
        cf = rev - ox - pi_yr
        if yr == 1:
            cf += shield_y1
        else:
            sl_full = (basis - seg) / 27.5
            dep_taken += sl_full
            bal_prev = loan_balance(loan, rate, fin["term_years"], (yr - 1) * 12)
            interest = bal_prev * rate
            taxable = rev - ox - interest - sl_full
            cf += (-taxable * tax["marginal_rate"]) if taxable < 0 else (-taxable * tax["marginal_rate"])
        flows.append(cf)
    # Terminal sale
    hy = proj["hold_years"]
    sale = p * (1 + apprec) ** hy
    bal = loan_balance(loan, rate, fin["term_years"], hy * 12)
    sell_costs = sale * proj["selling_cost_pct"]
    recapture = seg * tax["marginal_rate"] + (dep_taken - seg) * tax["recapture_sl_rate"]
    gain = sale - sell_costs - p
    cap_gains_tax = max(gain, 0) * tax["cap_gain_rate"]
    flows[-1] += sale - sell_costs - bal - recapture - cap_gains_tax
    irr10 = irr(flows)

    return {
        "gross": gross, "net_rev": net_rev, "opex": opex, "pi": pi_yr,
        "cf_y1": cf_y1, "coc": coc, "shield_y1": shield_y1, "atroi": atroi,
        "irr10": irr10 if irr10 is not None else -0.5,
        "cash_in": cash_in, "dep_y1": dep_y1, "rev_confidence": conf,
        "dscr": net_rev / (pi_yr + opex) if (pi_yr + opex) else 0,
        "price_sqft": p / float(listing["sqft"]),
    }
