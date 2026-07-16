"""SQLite persistence: listings, price history, daily scores, change detection."""
from __future__ import annotations
import sqlite3, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings(
  key TEXT PRIMARY KEY, address TEXT, market TEXT, town TEXT, state TEXT,
  complex TEXT, price REAL, beds INT, baths REAL, sqft REAL, hoa_annual REAL,
  property_type TEXT, condotel INT, coastal INT, dom REAL,
  status TEXT DEFAULT 'active', first_seen TEXT, last_seen TEXT, remarks TEXT,
  out_of_scope INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS price_history(
  key TEXT, seen TEXT, price REAL);
CREATE TABLE IF NOT EXISTS scores(
  key TEXT, run_date TEXT, score REAL, va REAL, atroi REAL, coc REAL, irr10 REAL,
  PRIMARY KEY(key, run_date));
"""


def connect(path="out/agent.db"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def norm_key(address):
    return "".join(ch for ch in address.lower() if ch.isalnum())


def upsert_listing(con, l, today=None):
    """Insert/refresh a listing. Returns event: 'new' | 'price_cut' | 'price_up' | 'seen'."""
    today = today or datetime.date.today().isoformat()
    key = norm_key(l["address"])
    row = con.execute("SELECT price FROM listings WHERE key=?", (key,)).fetchone()
    event = "seen"
    if row is None:
        event = "new"
        con.execute(
            "INSERT INTO listings(key,address,market,town,state,complex,price,beds,baths,sqft,"
            "hoa_annual,property_type,condotel,coastal,dom,first_seen,last_seen,remarks) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, l["address"], l["market"], l["town"], l["state"], l["complex"], float(l["price"]),
             int(l["beds"]), float(l["baths"]), float(l["sqft"]), float(l["hoa_annual"]),
             l.get("property_type", "condo"), int(l.get("condotel", 0) or 0),
             int(l.get("coastal", 0) or 0), float(l.get("dom", 0) or 0), today, today,
             l.get("remarks", "")))
        con.execute("INSERT INTO price_history VALUES(?,?,?)", (key, today, float(l["price"])))
    else:
        old = row[0]
        new = float(l["price"])
        if abs(new - old) > 0.5:
            event = "price_cut" if new < old else "price_up"
            con.execute("INSERT INTO price_history VALUES(?,?,?)", (key, today, new))
            con.execute("UPDATE listings SET price=?, last_seen=? WHERE key=?", (new, today, key))
        else:
            con.execute("UPDATE listings SET last_seen=? WHERE key=?", (today, key))
    con.commit()
    return key, event


def cum_cut_pct(con, key):
    rows = con.execute("SELECT price FROM price_history WHERE key=? ORDER BY seen", (key,)).fetchall()
    if len(rows) < 2 or rows[0][0] == 0:
        return 0.0
    return max(0.0, (rows[0][0] - rows[-1][0]) / rows[0][0])


def prev_score(con, key):
    r = con.execute("SELECT score FROM scores WHERE key=? ORDER BY run_date DESC LIMIT 1", (key,)).fetchone()
    return r[0] if r else None


def save_score(con, key, run_date, rec):
    con.execute("INSERT OR REPLACE INTO scores VALUES(?,?,?,?,?,?,?)",
                (key, run_date, rec["score"], rec["va"], rec["uw"]["atroi"],
                 rec["uw"]["coc"], rec["uw"]["irr10"]))
    con.commit()
