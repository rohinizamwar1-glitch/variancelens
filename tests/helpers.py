from decimal import Decimal as D


def row(category, forecast, actual, direction="higher", uf=None, ua=None, tag="none", note=""):
    return {"category": category, "forecast": D(str(forecast)), "actual": D(str(actual)),
            "preferred_direction": direction, "units_forecast": None if uf is None else D(str(uf)),
            "units_actual": None if ua is None else D(str(ua)), "evidence_tag": tag, "row_note": note}


SETTINGS = {"period": "2026-09", "business_unit": "Test unit", "amount_threshold": D("50000"),
            "pct_threshold": D("5"), "analyst_notes": ""}
