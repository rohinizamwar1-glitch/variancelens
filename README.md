# VarianceLens

**Track 4 · Finance · Variance Explanation Assistant**

Month-end. Leadership asks *"Why did we miss the forecast?"*. The analyst has the numbers, but writing a defensible explanation takes hours, and an AI asked naively will happily invent reasons ("festive demand", "competitor discounting") that nobody can verify.

VarianceLens calculates the drivers first, decides what the data can and cannot support, and only then asks an LLM to word the explanation. Anything the AI adds that is not backed by the input is caught and overridden.

> **JTBD:** When I see variance in numbers, I want to explain what caused it clearly and quickly, so I can defend my analysis.

## Pipeline

| # | Stage | Where |
|---|---|---|
| 1 | Structured input form (month, unit, thresholds, category rows, units, evidence tag) | `templates/index.html` |
| 2 | Input validation | `validation.py` |
| 3 | **Logic layer before AI** | `finance_logic.py` |
| 4 | Prompt construction (facts only) | `llm_explainer.build_facts`, `SYSTEM_PROMPT` |
| 5 | LLM API call (OpenAI-compatible: Gemini / Groq / OpenAI) | `llm_explainer.call_llm` |
| 6 | Output parsing | `llm_explainer._extract_json` |
| 7 | Confidence and guardrail checks | `llm_explainer.generate` |
| 8 | Database storage (every report kept) | `db.py` |
| 9 | Structured report UI | `templates/report.html` |
| 10 | Admin dashboard | `templates/admin.html` |

## What the logic layer does (before any AI call)

1. **Variance and %**, with favourable/unfavourable depending on whether higher or lower is better for that line.
2. **Materiality tiers:** a line is material if |variance| ≥ amount threshold **or** ≥ % threshold. It is **High** if it is also ≥ 25% of gross movement or ≥ 3× the % threshold.
3. **Pareto key drivers:** the fewest material lines that together explain ≥ 80% of gross movement.
4. **Price/volume split** when units are entered: volume effect = (Uₐ − U_f) × P_f, price effect = (Pₐ − P_f) × Uₐ.
5. **Candidate cause** from unit data plus the analyst's evidence tag (timing, one-off, rate change, quantity change). If the tag contradicts the unit data, the data wins and a conflict is flagged.
6. **Recurrence check:** if a line moved the same way in 2 of the last 3 saved months for this unit, it is flagged as a likely forecasting bias, not a one-off.
7. **Evidence score → confidence ceiling:** units +2, evidence tag +2, note +1, history +1, conflict −2. Strong caps confidence at 90, moderate at 70, weak at 40.
8. **Data-quality flags:** variance ≥ 100% with no evidence, or opposite signs between forecast and actual.

## Guardrails against invented causes (the key failure mode)

- The model sees only computed facts and the analyst's own notes.
- Each line gets a list of **allowed causes**. If the AI picks anything else, it is reset.
- If a line has no evidence and the AI writes "due to / driven by / because…", the wording is replaced with the calculated summary.
- Any number in the AI text that is not in the input is flagged, and confidence drops to 30 or below.
- AI confidence can never exceed the evidence ceiling.
- Overall confidence is capped at 50 when less than half the movement is backed by evidence.
- If the AI is down, out of quota or returns junk, the report still saves with calculated explanations and says so.

## Run on Replit

1. Import this repo into Replit (Python).
2. **Secrets:** `LLM_API_KEY` (free Gemini key from https://aistudio.google.com/apikey), `ADMIN_PASSWORD`, `SECRET_KEY`.
3. Press **Run**. Go to **Admin → Seed demo history** to add June to August, then **New review → Load sample** for September.

Without `LLM_API_KEY` the app still works and shows "Calculated only" reports.

## Tests

```
python -m unittest discover -s tests -t .
```
22 tests cover the logic layer, guardrails (with a fake AI reply that invents causes and numbers) and the Flask routes.
