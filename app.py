"""VarianceLens: explains forecast-vs-actual variance without inventing causes.

Pipeline for every review:
form -> validation -> logic layer -> prompt -> LLM -> parse + guard checks -> SQLite -> report page -> admin
"""
import hmac
import json
import os
import secrets
from decimal import Decimal
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import db
from finance_logic import analyze, snapshot_for_history
from llm_explainer import config as llm_config, fallback, generate
from validation import EVIDENCE_TAGS, validate_submission

BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
db.init_db()

CAUSE_LABELS = {"volume": "Volume", "price": "Price / rate", "timing": "Timing", "anomaly": "One-off",
                "mixed": "Price + volume", "unexplained": "Unexplained"}


@app.template_filter("amount")
def amount_filter(value):
    if value is None or value == "":
        return "—"
    return f"{Decimal(str(value)):,.0f}"


@app.template_filter("signed")
def signed_filter(value):
    d = Decimal(str(value))
    return ("+" if d > 0 else "") + f"{d:,.0f}"


@app.template_filter("pct")
def pct_filter(value, places=1):
    if value is None or value == "":
        return "—"
    return f"{Decimal(str(value)):,.{places}f}%"


@app.template_filter("share")
def share_filter(value):
    return f"{Decimal(str(value)) * 100:,.0f}%"


@app.context_processor
def globals_for_templates():
    return {"EVIDENCE_TAGS": EVIDENCE_TAGS, "CAUSE_LABELS": CAUSE_LABELS, "llm": llm_config()}


def load_sample(name):
    return json.loads((BASE_DIR / "sample_data" / name).read_text(encoding="utf-8"))


def blank_form():
    return {"period": "", "business_unit": "", "amount_threshold": "50000", "pct_threshold": "5",
            "analyst_notes": "", "rows": [{"preferred_direction": "higher", "evidence_tag": "none"}]}


def run_pipeline(rows, settings, use_ai=True):
    history = db.load_history(settings["business_unit"], settings["period"])
    analysis = analyze(rows, settings, history)
    ai = generate(analysis, settings) if use_ai else fallback(analysis, "Demo history seeded without AI.")
    report_id = db.save_report(rows, settings, analysis, snapshot_for_history(analysis), ai)
    return report_id


@app.route("/")
def index():
    form = blank_form()
    if request.args.get("sample"):
        form = load_sample("september_review.json")
    elif request.args.get("from"):
        report = db.get_report(request.args.get("from", type=int) or 0)
        if report:
            form = {**report["settings"], "rows": report["inputs"]}
    return render_template("index.html", form=form, errors=[])


@app.route("/analyze", methods=["POST"])
def analyze_route():
    rows, settings, errors, echo_rows = validate_submission(request.form)
    if errors:
        form = {k: request.form.get(k, "") for k in ("period", "business_unit", "amount_threshold",
                                                       "pct_threshold", "analyst_notes")}
        form["rows"] = echo_rows or blank_form()["rows"]
        return render_template("index.html", form=form, errors=errors), 422
    try:
        report_id = run_pipeline(rows, settings)
    except Exception:  # last-resort guard: never show a stack trace to the analyst
        app.logger.exception("Pipeline failed")
        form = {**{k: request.form.get(k, "") for k in ("period", "business_unit", "amount_threshold",
                                                         "pct_threshold", "analyst_notes")}, "rows": echo_rows}
        return render_template("index.html", form=form,
                               errors=["Something went wrong while saving the analysis. Your figures are kept below; please try again."]), 500
    return redirect(url_for("report", report_id=report_id))


@app.route("/reports")
def reports():
    unit = request.args.get("unit") or None
    return render_template("reports.html", reports=db.list_reports(unit), units=db.business_units(), unit=unit)


@app.route("/reports/<int:report_id>")
def report(report_id):
    r = db.get_report(report_id)
    if not r:
        abort(404)
    return render_template("report.html", r=r, a=r["analysis"], ai=r["ai"], s=r["settings"])


@app.route("/reports/<int:report_id>/review", methods=["POST"])
def review(report_id):
    choice = request.form.get("review")
    if choice not in {"accepted", "rejected"} or not db.get_report(report_id):
        abort(400)
    db.set_review(report_id, choice, request.form.get("comment", "").strip())
    flash("Thanks, your review was saved." if choice == "accepted"
          else "Marked as not usable. This shows up on the admin dashboard.")
    return redirect(url_for("report", report_id=report_id))


# ---------- admin ----------
def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    configured = os.getenv("ADMIN_PASSWORD", "")
    error = None
    if request.method == "POST":
        if configured and hmac.compare_digest(request.form.get("password", ""), configured):
            session["admin"] = True
            return redirect(request.args.get("next") if (request.args.get("next") or "").startswith("/admin") else url_for("admin"))
        error = "Incorrect password."
    return render_template("admin_login.html", configured=bool(configured), error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", d=db.dashboard_stats(), recent=db.list_reports(limit=12))


@app.route("/admin/seed-demo", methods=["POST"])
@admin_required
def seed_demo():
    """Adds Jun-Aug history for the demo business unit so the recurrence check has data."""
    for month in load_sample("history_seed.json"):
        rows, settings, errors, _ = validate_submission(_MultiDictLike(month))
        if not errors:
            run_pipeline(rows, settings, use_ai=False)
    flash("Added three months of demo history (June to August).")
    return redirect(url_for("admin"))


class _MultiDictLike:
    """Lets the seed file reuse the same validation as the web form."""
    def __init__(self, data):
        self.data = data

    def get(self, key, default=None):
        value = self.data.get(key, default)
        return default if value is None else str(value)

    def getlist(self, key):
        return [str(r.get(key, "") if r.get(key) is not None else "") for r in self.data.get("rows", [])]


@app.errorhandler(404)
def not_found(_):
    return render_template("message.html", title="Not found", text="That report does not exist."), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
