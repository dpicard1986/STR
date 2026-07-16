"""Daily digest: HTML render + email via Gmail SMTP (App Password). Falls back to writing an HTML file."""
from __future__ import annotations
import datetime
from pathlib import Path


def pct(x, d=1):
    return f"{x*100:.{d}f}%"


def money(x):
    return f"${x:,.0f}"


def render(ranked, events, cfg, run_date=None, market_scope=None):
    run_date = run_date or datetime.date.today().isoformat()
    hot = [r for r in ranked if r["score"] >= cfg["scoring"]["hot_score"]
           or r["uw"]["atroi"] >= cfg["scoring"]["hot_atroi"]]
    cuts = [e for e in events if e["event"] == "price_cut"]
    new = [e for e in events if e["event"] == "new"]
    scope = ", ".join(market_scope) if market_scope else "all markets"

    rows = ""
    for i, r in enumerate(ranked[:10], 1):
        l, u = r["listing"], r["uw"]
        rows += (f"<tr><td>{i}</td><td><b>{l['address']}</b><br>"
                 f"<small>{l['market']} - {l['beds']}BR/{l['baths']}BA - {money(float(l['price']))} - "
                 f"{money(u['price_sqft'])}/sqft - rev conf {u['rev_confidence']}</small></td>"
                 f"<td><b>{r['score']}</b></td><td>{pct(u['atroi'])}</td><td>{pct(u['coc'])}</td>"
                 f"<td>{pct(u['irr10'])}</td><td>{r['va']:.0f}</td>"
                 f"<td>{money(u['cf_y1'])}</td></tr>")

    cut_rows = "".join(
        f"<tr><td>{e['address']}</td><td>{money(e['old'])} -> {money(e['new'])} "
        f"(-{pct((e['old']-e['new'])/e['old'])}, cum -{pct(e['cum'])})</td>"
        f"<td>{e.get('new_score','-')}</td></tr>" for e in cuts) or "<tr><td colspan=3>None today</td></tr>"

    new_rows = "".join(f"<li>{e['address']} - {money(e['price'])}</li>" for e in new) or "<li>None today</li>"

    html = f"""<html><body style="font-family:Georgia,serif;max-width:860px">
<h2>STR Deals - {run_date}</h2>
<p><small>Scope: {scope} | {len(new)} new | {len(cuts)} price cuts | {len(hot)} hot |
weights ATROI {cfg['scoring']['w_atroi']:.0%} / CF {cfg['scoring']['w_cashflow']:.0%} /
IRR10 {cfg['scoring']['w_irr10']:.0%} | VA multiplier +/-{cfg['scoring']['va_swing']:.0%} |
rate {cfg['financing']['mortgage_rate']:.2%}, {cfg['financing']['down_payment_pct']:.0%} down |
regulatory flags shown, never scored</small></p>
<h3>Top ranked</h3>
<table border=1 cellpadding=6 cellspacing=0>
<tr><th>#</th><th>Property</th><th>Score</th><th>Y1 after-tax ROI</th><th>Y1 CoC</th>
<th>10-yr IRR</th><th>VA</th><th>Y1 cash flow</th></tr>{rows}</table>
<h3>Price cuts</h3>
<table border=1 cellpadding=6 cellspacing=0><tr><th>Property</th><th>Change</th><th>New score</th></tr>{cut_rows}</table>
<h3>New today</h3><ul>{new_rows}</ul>
<p><small>Estimates from complex-level rental comps; verify HOA, taxes, and STR rules before offer.
Not financial advice.</small></p>
</body></html>"""
    return html


def deliver(html, cfg, run_date=None):
    run_date = run_date or datetime.date.today().isoformat()
    out = Path("out") / f"digest_{run_date}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(html)
    if cfg["email"].get("enabled"):
        try:
            send_smtp(html, cfg, run_date)
            return f"emailed to {cfg['email']['to']} (and saved {out})"
        except Exception as e:  # noqa: BLE001
            return f"email failed ({e}); saved {out}"
    return f"saved {out}"


def send_smtp(html, cfg, run_date):
    """Send via Gmail SMTP using the App Password in .gmail_app_password."""
    import smtplib
    from email.mime.text import MIMEText

    pw_file = Path(".gmail_app_password")
    if not pw_file.exists():
        raise FileNotFoundError(".gmail_app_password not found")
    pw = pw_file.read_text().strip().replace(" ", "")
    addr = cfg["email"]["to"]
    msg = MIMEText(html, "html")
    msg["From"] = addr
    msg["To"] = addr
    msg["Subject"] = f"STR Deals - {run_date}"
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(addr, pw)
        s.send_message(msg)
