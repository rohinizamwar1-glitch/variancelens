"""Deterministic logic layer. Runs BEFORE the AI and decides what the AI may say.

Steps, in order:
1. Variance, variance %, favourable / unfavourable per category.
2. Share of gross variance and a materiality tier (High / Medium / Low).
3. Pareto key drivers: the fewest material categories that explain >= 80% of gross variance.
4. Price / volume split when units are supplied.
5. Rule-based candidate cause from data + the analyst's evidence tag.
6. Recurrence check against this business unit's earlier saved reports.
7. Evidence strength -> confidence ceiling the AI can never exceed.
"""
from decimal import Decimal, ROUND_HALF_UP

PARETO_TARGET = Decimal("0.80")
HIGH_SHARE = Decimal("0.25")
DOMINANCE = Decimal("0.60")
CEILINGS = {"strong": 90, "moderate": 70, "weak": 40}

TAG_TO_CAUSE = {
    "timing": "timing",
    "one_off": "anomaly",
    "rate_change": "price",
    "quantity_change": "volume",
}


def _q(value, places="0.01"):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def _price_volume(row):
    uf, ua = row.get("units_forecast"), row.get("units_actual")
    if not uf or not ua:
        return None
    price_f = row["forecast"] / uf
    price_a = row["actual"] / ua
    volume_effect = (ua - uf) * price_f
    price_effect = (price_a - price_f) * ua  # volume + price == actual - forecast
    return {
        "price_forecast": _q(price_f), "price_actual": _q(price_a),
        "volume_effect": _q(volume_effect), "price_effect": _q(price_effect),
    }


def _candidate_cause(row, pv):
    """Return (cause, reasons, conflict). Cause is what the data can support."""
    reasons, conflict = [], False
    tag_cause = TAG_TO_CAUSE.get(row["evidence_tag"])
    data_cause = None
    if pv:
        v, p = abs(pv["volume_effect"]), abs(pv["price_effect"])
        total = v + p
        if total:
            if v / total >= DOMINANCE:
                data_cause = "volume"
            elif p / total >= DOMINANCE:
                data_cause = "price"
            else:
                data_cause = "mixed"
        reasons.append(
            f"Units supplied: volume effect {pv['volume_effect']:,}, price effect {pv['price_effect']:,}."
        )
    if tag_cause:
        reasons.append(f"Analyst evidence: {row['evidence_tag'].replace('_', ' ')}.")

    if data_cause and tag_cause and tag_cause in {"price", "volume"} and data_cause not in {tag_cause, "mixed"}:
        conflict = True
        reasons.append(f"Conflict: evidence says {tag_cause}, but the unit data points to {data_cause}. Data wins.")
        return data_cause, reasons, conflict
    if tag_cause in {"timing", "anomaly"}:
        return tag_cause, reasons, conflict
    if data_cause:
        return data_cause, reasons, conflict
    if tag_cause:
        return tag_cause, reasons, conflict
    reasons.append("No units and no evidence supplied, so the cause cannot be determined from the data.")
    return "unexplained", reasons, conflict


def _recurrence(category, direction_sign, history):
    """Count earlier periods where this category missed in the same direction and was material."""
    key = category.casefold()
    hits, seen = 0, 0
    for past in history[:3]:
        for item in past.get("drivers", []):
            if item["category"].casefold() == key:
                seen += 1
                past_variance = Decimal(str(item["variance"]))
                if item["tier"] != "Low" and past_variance != 0 and (past_variance > 0) == (direction_sign > 0):
                    hits += 1
                break
    return hits, seen


def analyze(rows, settings, history=None):
    history = history or []
    amount_t = settings["amount_threshold"]
    pct_t = settings["pct_threshold"]

    items = []
    for row in rows:
        variance = row["actual"] - row["forecast"]
        pct = (variance / abs(row["forecast"]) * 100) if row["forecast"] != 0 else None
        if variance == 0:
            assessment = "On plan"
        elif (variance > 0) == (row["preferred_direction"] == "higher"):
            assessment = "Favourable"
        else:
            assessment = "Unfavourable"
        items.append({**row, "variance": variance, "abs_variance": abs(variance), "pct": pct,
                      "assessment": assessment})

    gross = sum((i["abs_variance"] for i in items), Decimal("0"))
    total_f = sum((i["forecast"] for i in items), Decimal("0"))
    total_a = sum((i["actual"] for i in items), Decimal("0"))

    for i in items:
        i["share"] = (i["abs_variance"] / gross) if gross else Decimal("0")
        material = i["variance"] != 0 and (
            i["abs_variance"] >= amount_t or (i["pct"] is not None and abs(i["pct"]) >= pct_t))
        if not material:
            i["tier"] = "Low"
        elif i["share"] >= HIGH_SHARE or (i["pct"] is not None and abs(i["pct"]) >= pct_t * 3):
            i["tier"] = "High"
        else:
            i["tier"] = "Medium"

    items.sort(key=lambda i: i["abs_variance"], reverse=True)
    cumulative = Decimal("0")
    for rank, i in enumerate(items, start=1):
        i["rank"] = rank
        i["key_driver"] = i["tier"] != "Low" and cumulative < PARETO_TARGET
        if i["key_driver"]:
            cumulative += i["share"]
        i["cumulative_share"] = cumulative

    data_flags = []
    for i in items:
        pv = _price_volume(i)
        i["price_volume"] = pv
        cause, reasons, conflict = _candidate_cause(i, pv)
        i["cause"], i["cause_reasons"], i["conflict"] = cause, reasons, conflict

        hits, seen = _recurrence(i["category"], i["variance"], history) if i["variance"] else (0, 0)
        i["recurring"] = hits >= 2
        i["history_points"] = seen
        if i["recurring"]:
            i["cause_reasons"].append(
                f"Moved in the same direction in {hits} of the last {seen} saved periods: likely a forecasting bias.")
            if cause == "anomaly":
                i["conflict"] = True
                i["cause_reasons"].append("Tagged one-off, but it keeps recurring. Treat the one-off label with caution.")

        score = 0
        score += 2 if pv else 0
        score += 2 if i["evidence_tag"] != "none" else 0
        score += 1 if i["row_note"] else 0
        score += 1 if seen else 0
        score -= 2 if i["conflict"] else 0
        i["evidence_points"] = score
        i["evidence_band"] = "strong" if score >= 4 else "moderate" if score >= 2 else "weak"
        if cause == "unexplained":
            i["evidence_band"] = "weak"
        i["confidence_ceiling"] = CEILINGS[i["evidence_band"]]

        allowed = {cause, "unexplained"}
        if cause == "mixed":
            allowed = {"price", "volume", "unexplained"}
        i["allowed_causes"] = sorted(allowed)

        if i["pct"] is not None and abs(i["pct"]) >= 100 and i["evidence_tag"] == "none":
            data_flags.append(f"{i['category']}: variance is {abs(i['pct']):.0f}% of forecast with no evidence. Check the source data before explaining it.")
        if i["forecast"] != 0 and i["actual"] != 0 and (i["forecast"] > 0) != (i["actual"] > 0):
            data_flags.append(f"{i['category']}: forecast and actual have opposite signs. Possible sign or mapping error.")

    key = [i for i in items if i["key_driver"]]
    explained = sum((i["share"] for i in key if i["cause"] != "unexplained"), Decimal("0"))
    favourable = sum((i["abs_variance"] for i in items if i["assessment"] == "Favourable"), Decimal("0"))
    unfavourable = sum((i["abs_variance"] for i in items if i["assessment"] == "Unfavourable"), Decimal("0"))

    return {
        "items": items,
        "key_drivers": key,
        "total_forecast": total_f,
        "total_actual": total_a,
        "total_variance": total_a - total_f,
        "gross_variance": gross,
        "favourable_total": favourable,
        "unfavourable_total": unfavourable,
        "key_coverage": cumulative,
        "explained_share": explained,
        "data_flags": data_flags,
        "history_periods": len(history[:3]),
    }


def snapshot_for_history(analysis):
    """Small JSON-safe record used by the recurrence check in later periods."""
    return {"drivers": [
        {"category": i["category"], "variance": str(i["variance"]), "tier": i["tier"]}
        for i in analysis["items"]
    ]}
