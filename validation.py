"""Input validation for the VarianceLens form.

Everything the analyst types is checked here before any calculation or AI call.
The function never raises on bad input; it returns readable error messages so
the form can be shown again with the analyst's values kept.
"""
import re
from decimal import Decimal, InvalidOperation

EVIDENCE_TAGS = {
    "none": "No evidence yet",
    "timing": "Billing / delivery moved to another period",
    "one_off": "One-off or unusual event",
    "rate_change": "Rate or price changed",
    "quantity_change": "Quantity / volume changed",
}

MAX_ROWS = 25
MAX_NOTE = 240
PERIOD_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _decimal(text):
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def validate_submission(form):
    """Return (rows, settings, errors, echo_rows).

    `form` is a Flask MultiDict (or anything with getlist/get).
    `echo_rows` keeps what the analyst typed so the form can be redisplayed.
    """
    errors = []

    period = (form.get("period") or "").strip()
    business_unit = " ".join((form.get("business_unit") or "").split())[:80]
    if not PERIOD_PATTERN.match(period):
        errors.append("Choose the month being reviewed (format YYYY-MM).")
    if not business_unit:
        errors.append("Enter the business unit or cost centre being reviewed.")

    amount_threshold = _decimal((form.get("amount_threshold") or "").strip())
    pct_threshold = _decimal((form.get("pct_threshold") or "").strip())
    if amount_threshold is None or amount_threshold < 0:
        errors.append("Amount threshold must be a number of 0 or more.")
    if pct_threshold is None or pct_threshold < 0 or pct_threshold > 100:
        errors.append("Percentage threshold must be between 0 and 100.")

    notes = (form.get("analyst_notes") or "").strip()[:1500]

    fields = ["category", "forecast", "actual", "preferred_direction",
              "units_forecast", "units_actual", "evidence_tag", "row_note"]
    columns = {name: form.getlist(name) for name in fields}
    row_count = max((len(v) for v in columns.values()), default=0)

    def cell(name, i, default=""):
        values = columns[name]
        return (values[i] if i < len(values) else default).strip()

    rows, echo_rows, seen = [], [], {}
    for i in range(row_count):
        raw = {name: cell(name, i) for name in fields}
        raw["preferred_direction"] = raw["preferred_direction"] or "higher"
        raw["evidence_tag"] = raw["evidence_tag"] or "none"
        if not (raw["category"] or raw["forecast"] or raw["actual"]):
            continue
        echo_rows.append(raw)
        label = f"Row {len(echo_rows)}"

        if not raw["category"] or not raw["forecast"] or not raw["actual"]:
            errors.append(f"{label}: Category, Forecast and Actual are all required.")
            continue
        key = " ".join(raw["category"].split()).casefold()
        if key in seen:
            errors.append(f"{label}: '{raw['category']}' is already used in row {seen[key]}.")
            continue
        seen[key] = len(echo_rows)

        forecast, actual = _decimal(raw["forecast"]), _decimal(raw["actual"])
        if forecast is None or actual is None:
            errors.append(f"{label}: Forecast and Actual must be numbers.")
            continue
        if raw["preferred_direction"] not in {"higher", "lower"}:
            errors.append(f"{label}: choose whether higher or lower is better.")
            continue
        if raw["evidence_tag"] not in EVIDENCE_TAGS:
            errors.append(f"{label}: unknown evidence option.")
            continue

        units_f, units_a = None, None
        if raw["units_forecast"] or raw["units_actual"]:
            units_f, units_a = _decimal(raw["units_forecast"]), _decimal(raw["units_actual"])
            if units_f is None or units_a is None or units_f <= 0 or units_a <= 0:
                errors.append(f"{label}: enter both forecast and actual units as positive numbers, or leave both empty.")
                continue

        rows.append({
            "category": " ".join(raw["category"].split())[:60],
            "forecast": forecast,
            "actual": actual,
            "preferred_direction": raw["preferred_direction"],
            "units_forecast": units_f,
            "units_actual": units_a,
            "evidence_tag": raw["evidence_tag"],
            "row_note": raw["row_note"][:MAX_NOTE],
        })

    if not echo_rows:
        errors.append("Add at least one category with forecast and actual figures.")
    if len(echo_rows) > MAX_ROWS:
        errors.append(f"Keep it to {MAX_ROWS} categories per review.")

    settings = {
        "period": period,
        "business_unit": business_unit,
        "amount_threshold": amount_threshold,
        "pct_threshold": pct_threshold,
        "analyst_notes": notes,
    }
    return rows, settings, list(dict.fromkeys(errors)), echo_rows
