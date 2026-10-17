"""AI layer: prompt construction, LLM call, parsing and guardrail checks.

The AI only writes the *wording*. The logic layer has already decided the numbers,
the candidate cause for each driver and the maximum confidence. After the call we
check the reply against those facts and override anything the AI made up.

Works with any OpenAI-compatible chat endpoint. Default is Google Gemini's free tier.
Standard library only (urllib), so no SDK is needed.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal

PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
}
CAUSE_WORDS = re.compile(r"\b(due to|because|driven by|caused by|as a result of|owing to|attributable to)\b", re.I)
NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


def config():
    provider = os.getenv("LLM_PROVIDER", "gemini").lower()
    url, model = PROVIDERS.get(provider, PROVIDERS["gemini"])
    return {
        "provider": provider,
        "url": os.getenv("LLM_BASE_URL", url),
        "model": os.getenv("LLM_MODEL", model),
        "key": os.getenv("LLM_API_KEY", ""),
    }


def _fmt(value, places=2):
    return f"{Decimal(value):,.{places}f}"


def build_facts(analysis, settings):
    """Only computed facts and analyst-supplied evidence go to the model."""
    drivers = []
    for i in analysis["items"]:
        if i["tier"] == "Low":
            continue
        drivers.append({
            "category": i["category"],
            "key_driver": i["key_driver"],
            "forecast": _fmt(i["forecast"]),
            "actual": _fmt(i["actual"]),
            "variance": _fmt(i["variance"]),
            "variance_pct": None if i["pct"] is None else _fmt(i["pct"], 1),
            "share_of_gross_variance_pct": _fmt(i["share"] * 100, 1),
            "assessment": i["assessment"],
            "materiality_tier": i["tier"],
            "cause_from_logic": i["cause"],
            "allowed_causes": i["allowed_causes"],
            "logic_reasons": i["cause_reasons"],
            "price_volume": None if not i["price_volume"] else {k: _fmt(v) for k, v in i["price_volume"].items()},
            "analyst_row_note": i["row_note"] or None,
            "recurring_miss": i["recurring"],
            "evidence_strength": i["evidence_band"],
            "max_confidence": i["confidence_ceiling"],
        })
    return {
        "period": settings["period"],
        "business_unit": settings["business_unit"],
        "total_forecast": _fmt(analysis["total_forecast"]),
        "total_actual": _fmt(analysis["total_actual"]),
        "total_variance": _fmt(analysis["total_variance"]),
        "key_driver_coverage_pct": _fmt(analysis["key_coverage"] * 100, 1),
        "analyst_notes": settings["analyst_notes"] or None,
        "material_categories": drivers,
    }


SYSTEM_PROMPT = """You help a finance analyst answer leadership's question "Why did we miss the forecast?".
You receive pre-computed facts. Rules you must follow:
1. Use ONLY the facts given. Never invent customers, events, markets, seasons or reasons.
2. For each category, "cause" must be one of its allowed_causes. If cause_from_logic is "unexplained",
   say plainly that the data does not show the cause, and write one specific follow-up question the analyst
   should answer. Do not use phrases like "due to" or "driven by" for unexplained categories.
3. Do not introduce any number that is not in the facts. Quote numbers exactly as given.
4. "confidence" (0-100) must not exceed that category's max_confidence.
5. If recurring_miss is true, say the forecast may be biased for this line.
6. Plain business English. Max 2 sentences per explanation. Headline max 3 sentences.
Return ONLY JSON with this shape:
{"headline": str,
 "drivers": [{"category": str, "cause": str, "explanation": str, "confidence": int, "follow_up_question": str}],
 "cannot_conclude": str}"""


def _extract_json(text):
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object in model reply")
    return json.loads(text[start:end + 1])


def _norm(token):
    token = token.replace(",", "").lstrip("+")
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token.lstrip("-") or "0"


def _allowed_numbers(facts):
    allowed = set(str(n) for n in range(0, 13))
    blob = json.dumps(facts)
    for token in NUMBER.findall(blob):
        n = _norm(token)
        allowed.add(n)
        try:  # allow the same figure rounded to 0 or 1 decimals
            d = Decimal(n)
            allowed.add(_norm(f"{d:.0f}"))
            allowed.add(_norm(f"{d:.1f}"))
        except Exception:
            pass
    return allowed


def call_llm(facts, cfg, timeout=25):
    body = json.dumps({
        "model": cfg["model"],
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
        ],
    }).encode()
    req = urllib.request.Request(cfg["url"], data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['key']}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    text = data["choices"][0]["message"]["content"]
    return text, data.get("usage") or {}


def _template_driver(i):
    pct = "" if i["pct"] is None else f" ({_fmt(i['pct'], 1)}%)"
    base = (f"{i['category']} was {_fmt(i['abs_variance'])}{pct} "
            f"{'above' if i['variance'] > 0 else 'below'} forecast, {_fmt(i['share'] * 100, 1)}% of the total movement.")
    if i["cause"] == "unexplained":
        return base + " The figures alone do not show why."
    return base + " " + " ".join(i["cause_reasons"])


def _follow_up(i):
    if i["cause"] == "unexplained":
        return f"What changed in {i['category']} this month: quantity, rate, timing of billing, or a one-off item?"
    if i["evidence_band"] == "weak":
        return f"Can you add units or a source document for {i['category']} to confirm the cause?"
    return ""


def fallback(analysis, reason):
    drivers = []
    for i in analysis["items"]:
        if i["tier"] == "Low":
            continue
        drivers.append({
            "category": i["category"], "cause": i["cause"],
            "explanation": _template_driver(i),
            "confidence": min(i["confidence_ceiling"], 60),
            "follow_up_question": _follow_up(i),
            "guard": [],
        })
    return _finish(analysis, {
        "status": "fallback", "error": reason,
        "headline": (f"Actuals were {_fmt(abs(analysis['total_variance']))} "
                     f"{'above' if analysis['total_variance'] >= 0 else 'below'} forecast. "
                     "Showing calculated drivers only; the AI summary was not generated."),
        "cannot_conclude": "", "drivers": drivers, "guard_flags": [],
    })


def _finish(analysis, result):
    by_cat = {i["category"]: i for i in analysis["items"]}
    weight, total = Decimal("0"), Decimal("0")
    for d in result["drivers"]:
        item = by_cat[d["category"]]
        d["evidence_band"] = item["evidence_band"]
        d["tier"] = item["tier"]
        d["key_driver"] = item["key_driver"]
        if item["key_driver"]:
            weight += item["share"]
            total += item["share"] * d["confidence"]
    overall = int(total / weight) if weight else 0
    if analysis["explained_share"] < Decimal("0.5"):
        overall = min(overall, 50)
        result["guard_flags"].append("Less than half of the variance has evidence behind it, so overall confidence is capped at 50.")
    result["overall_confidence"] = overall
    return result


def generate(analysis, settings):
    cfg = config()
    if not any(i["tier"] != "Low" for i in analysis["items"]):
        res = fallback(analysis, "No material variance, so no AI explanation was needed.")
        res["headline"] = "No category crossed the materiality thresholds. Actuals are in line with forecast."
        return res
    if not cfg["key"]:
        return fallback(analysis, "LLM_API_KEY is not set.")

    facts = build_facts(analysis, settings)
    started = time.monotonic()
    try:
        text, usage = call_llm(facts, cfg)
        parsed = _extract_json(text)
    except urllib.error.HTTPError as exc:
        return fallback(analysis, f"AI service returned HTTP {exc.code}.")
    except (urllib.error.URLError, TimeoutError) as exc:
        return fallback(analysis, f"AI service unreachable ({exc.__class__.__name__}).")
    except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
        return fallback(analysis, "AI reply could not be parsed.")
    latency = int((time.monotonic() - started) * 1000)

    allowed_numbers = _allowed_numbers(facts)
    by_cat = {i["category"].casefold(): i for i in analysis["items"] if i["tier"] != "Low"}
    guard_flags, drivers, done = [], [], set()

    for raw in parsed.get("drivers") or []:
        if not isinstance(raw, dict):
            continue
        item = by_cat.get(str(raw.get("category", "")).casefold())
        if not item or item["category"] in done:
            continue
        done.add(item["category"])
        guard = []
        cause = str(raw.get("cause", "")).lower()
        if cause not in item["allowed_causes"]:
            guard.append(f"AI suggested '{cause or 'nothing'}', which the data does not support. Reset to '{item['cause']}'.")
            cause = item["cause"]
        explanation = str(raw.get("explanation", "")).strip()[:600]
        try:
            confidence = int(raw.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        if confidence > item["confidence_ceiling"]:
            guard.append(f"AI confidence {confidence} lowered to the evidence ceiling {item['confidence_ceiling']}.")
        confidence = max(0, min(confidence, item["confidence_ceiling"]))

        if cause == "unexplained" and CAUSE_WORDS.search(explanation):
            guard.append("AI stated a cause for a line with no evidence. Wording replaced with the calculated summary.")
            explanation = _template_driver(item)
            confidence = min(confidence, 25)
        stray = [t for t in NUMBER.findall(explanation) if _norm(t) not in allowed_numbers]
        if stray:
            guard.append(f"Numbers not found in the input: {', '.join(stray[:3])}. Treat this explanation as unverified.")
            confidence = min(confidence, 30)
        if not explanation:
            explanation = _template_driver(item)

        follow = str(raw.get("follow_up_question", "")).strip()[:300] or _follow_up(item)
        drivers.append({"category": item["category"], "cause": cause, "explanation": explanation,
                        "confidence": confidence, "follow_up_question": follow, "guard": guard})
        guard_flags.extend(f"{item['category']}: {g}" for g in guard)

    for item in by_cat.values():  # any material line the AI skipped still gets shown
        if item["category"] not in done:
            drivers.append({"category": item["category"], "cause": item["cause"],
                            "explanation": _template_driver(item), "confidence": min(item["confidence_ceiling"], 60),
                            "follow_up_question": _follow_up(item), "guard": ["AI skipped this line; calculated summary shown."]})
            guard_flags.append(f"{item['category']}: AI skipped this line.")

    order = {i["category"]: i["rank"] for i in analysis["items"]}
    drivers.sort(key=lambda d: order[d["category"]])

    headline = str(parsed.get("headline", "")).strip()[:800]
    stray = [t for t in NUMBER.findall(headline) if _norm(t) not in allowed_numbers]
    if stray or not headline:
        guard_flags.append("Headline contained unsupported numbers or was empty; replaced with the calculated headline.")
        headline = fallback(analysis, "")["headline"].split(" Showing")[0]

    return _finish(analysis, {
        "status": "ai", "error": None, "provider": cfg["provider"], "model": cfg["model"],
        "latency_ms": latency,
        "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens"),
        "headline": headline, "cannot_conclude": str(parsed.get("cannot_conclude", "")).strip()[:500],
        "drivers": drivers, "guard_flags": guard_flags,
    })
