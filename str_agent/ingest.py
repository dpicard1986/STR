"""Ingestion. Two modes:
1. csv: read listings from a CSV (demo / manual entry)
2. gmail: pull portal saved-search alert emails via IMAP (label: 'str-alerts')

Auth: Gmail App Password read from the file .gmail_app_password in the project folder
(myaccount.google.com -> Security -> 2-Step Verification -> App passwords).
No OAuth, no Google Cloud project, no token expiry.
"""
from __future__ import annotations
import csv, email, imaplib, re
from pathlib import Path

PRICE_RE = re.compile(r"\$([\d,]{5,10})")
BEDBATH_RE = re.compile(r"(\d+)\s*(?:bd|bed)s?\b.{0,20}?(\d+(?:\.\d)?)\s*(?:ba|bath)", re.I | re.S)
SQFT_RE = re.compile(r"([\d,]{3,6})\s*sq\s*\.?\s*ft", re.I)
ADDR_RE = re.compile(r"\d{1,6}[^,<>\n]{3,60},\s*[A-Za-z .]{2,30},?\s*(VT|NH|MA|ME)\b")

PASSWORD_FILE = ".gmail_app_password"


def gmail_login(cfg):
    addr = cfg["email"]["to"]
    pw_file = Path(PASSWORD_FILE)
    if not pw_file.exists():
        raise SystemExit(f"{PASSWORD_FILE} not found - create a Gmail App Password "
                         "(Google Account > Security > App passwords) and save it in that file.")
    pw = pw_file.read_text().strip().replace(" ", "")
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(addr, pw)
    return imap


def from_csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def parse_alert_html(html):
    """Extract candidate listings from one portal alert email body."""
    out = []
    for m in ADDR_RE.finditer(html):
        window = html[m.start(): m.start() + 1200]
        price = PRICE_RE.search(window)
        bb = BEDBATH_RE.search(window)
        sq = SQFT_RE.search(window)
        if not price:
            continue
        out.append({
            "address": m.group(0).strip(),
            "state": m.group(1),
            "price": float(price.group(1).replace(",", "")),
            "beds": int(bb.group(1)) if bb else 0,
            "baths": float(bb.group(2)) if bb else 0,
            "sqft": float(sq.group(1).replace(",", "")) if sq else 0,
        })
    return out


def _bodies(msg):
    for part in msg.walk():
        if part.get_content_type() in ("text/html", "text/plain"):
            payload = part.get_payload(decode=True)
            if payload:
                yield payload.decode(part.get_content_charset() or "utf-8", errors="ignore")


def from_gmail(label="str-alerts", newer_than_days=2, cfg=None):
    """Pull recent alert emails from the Gmail label via IMAP."""
    import datetime
    if cfg is None:
        from .main import load_cfg
        cfg = load_cfg()
    imap = gmail_login(cfg)
    try:
        status, _ = imap.select(f'"{label}"', readonly=True)
        if status != "OK":
            raise SystemExit(f'Gmail label "{label}" not found - create the filter per README step 3.')
        since = (datetime.date.today() - datetime.timedelta(days=newer_than_days)).strftime("%d-%b-%Y")
        _, data = imap.search(None, f"(SINCE {since})")
        ids = data[0].split()
        listings, misses = [], 0
        for mid in ids:
            _, msgdata = imap.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(msgdata[0][1])
            found = []
            for body in _bodies(msg):
                found.extend(parse_alert_html(body))
            if not found:
                misses += 1
            listings.extend(found)
        if misses:
            Path("out").mkdir(exist_ok=True)
            with open("out/parse_misses.log", "a") as f:
                f.write(f"{misses} alert emails yielded no listings\n")
        return listings
    finally:
        imap.logout()
