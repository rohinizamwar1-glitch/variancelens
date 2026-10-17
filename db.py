"""SQLite persistence: every review is saved as its own report (nothing is overwritten)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "instance" / "variancelens.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    period TEXT NOT NULL,
    business_unit TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    history_json TEXT NOT NULL,
    ai_json TEXT NOT NULL,
    status TEXT NOT NULL,
    overall_confidence INTEGER NOT NULL,
    total_variance TEXT NOT NULL,
    explained_share REAL NOT NULL,
    guard_count INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    latency_ms INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    review TEXT NOT NULL DEFAULT 'pending',
    review_comment TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_reports_unit_period ON reports (business_unit, period);
"""


def _json(value):
    return json.dumps(value, default=lambda o: str(o) if isinstance(o, Decimal) else o, ensure_ascii=False)


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


def save_report(rows, settings, analysis, history_snapshot, ai):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO reports (period, business_unit, inputs_json, settings_json, analysis_json, history_json,
                   ai_json, status, overall_confidence, total_variance, explained_share, guard_count,
                   model, latency_ms, tokens_in, tokens_out)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (settings["period"], settings["business_unit"], _json(rows), _json(settings), _json(analysis),
             _json(history_snapshot), _json(ai), ai["status"], ai["overall_confidence"],
             str(analysis["total_variance"]), float(analysis["explained_share"]), len(ai["guard_flags"]),
             ai.get("model"), ai.get("latency_ms"), ai.get("tokens_in"), ai.get("tokens_out")))
        return cur.lastrowid


def load_history(business_unit, period, limit=3):
    """Latest saved report per earlier period for the same business unit, newest first."""
    with connect() as conn:
        records = conn.execute(
            """SELECT period, history_json FROM reports
               WHERE lower(business_unit) = lower(?) AND period < ?
               ORDER BY period DESC, id DESC""", (business_unit, period)).fetchall()
    out, seen = [], set()
    for r in records:
        if r["period"] in seen:
            continue
        seen.add(r["period"])
        out.append(json.loads(r["history_json"]))
        if len(out) == limit:
            break
    return out


def get_report(report_id):
    with connect() as conn:
        r = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not r:
        return None
    report = dict(r)
    for field in ("inputs_json", "settings_json", "analysis_json", "ai_json"):
        report[field[:-5]] = json.loads(report.pop(field))
    return report


def list_reports(business_unit=None, limit=100):
    sql = """SELECT id, created_at, period, business_unit, status, overall_confidence, total_variance,
                    explained_share, guard_count, review FROM reports"""
    args = []
    if business_unit:
        sql += " WHERE lower(business_unit) = lower(?)"
        args.append(business_unit)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def business_units():
    with connect() as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT business_unit FROM reports ORDER BY business_unit").fetchall()]


def set_review(report_id, review, comment):
    with connect() as conn:
        conn.execute("UPDATE reports SET review = ?, review_comment = ? WHERE id = ?",
                     (review, comment[:500], report_id))


def dashboard_stats():
    with connect() as conn:
        s = dict(conn.execute("""
            SELECT COUNT(*) AS total,
                   SUM(status = 'ai') AS ai_runs,
                   SUM(status = 'fallback') AS fallback_runs,
                   ROUND(AVG(overall_confidence), 1) AS avg_confidence,
                   ROUND(AVG(explained_share) * 100, 1) AS avg_explained,
                   SUM(guard_count) AS guard_total,
                   SUM(guard_count > 0) AS runs_with_guards,
                   SUM(review = 'accepted') AS accepted,
                   SUM(review = 'rejected') AS rejected,
                   SUM(review = 'pending') AS pending,
                   ROUND(AVG(latency_ms)) AS avg_latency,
                   SUM(COALESCE(tokens_in, 0)) AS tokens_in,
                   SUM(COALESCE(tokens_out, 0)) AS tokens_out
            FROM reports""").fetchone())
        units = [dict(r) for r in conn.execute("""
            SELECT business_unit, COUNT(*) AS runs, ROUND(AVG(overall_confidence)) AS avg_conf,
                   MAX(period) AS latest_period
            FROM reports GROUP BY business_unit ORDER BY runs DESC""").fetchall()]
        ai_rows = conn.execute("SELECT ai_json FROM reports ORDER BY id DESC LIMIT 200").fetchall()
        rejected = [dict(r) for r in conn.execute("""
            SELECT id, period, business_unit, review_comment FROM reports
            WHERE review = 'rejected' ORDER BY id DESC LIMIT 5""").fetchall()]
    causes = {}
    for r in ai_rows:
        for d in json.loads(r["ai_json"]).get("drivers", []):
            causes[d["cause"]] = causes.get(d["cause"], 0) + 1
    s = {k: (v or 0) for k, v in s.items()}
    reviewed = s["accepted"] + s["rejected"]
    s["acceptance_rate"] = round(100 * s["accepted"] / reviewed) if reviewed else None
    s["fallback_rate"] = round(100 * s["fallback_runs"] / s["total"]) if s["total"] else 0
    return {"stats": s, "units": units, "causes": sorted(causes.items(), key=lambda x: -x[1]), "rejected": rejected}
