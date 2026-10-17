# VarianceLens: Product Brief

## 1. Problem statement
Every month-end, finance analysts at growing companies are asked one question by leadership: *"Why did we miss the forecast?"* The numbers are available within a day of close, but turning them into an explanation takes two to three days of spreadsheet work, Slack messages to business owners and rewriting. Worse, explanations are often unsupported ("demand was soft") and fall apart the moment a CFO asks a follow-up. The real problem is not the arithmetic. It is producing an explanation that is **fast and defensible**, and being honest about what the data cannot explain.

## 2. User persona
**Priya, 27, FP&A analyst at a D2C consumer brand (₹15–20 Cr monthly revenue).**
- Owns the monthly forecast-vs-actual pack for 3 business units, each with 8–15 P&L lines.
- Works in Excel and Google Sheets; data comes from Tally/ERP and the Shopify and marketplace dashboards.
- Pain: the leadership review is on day 3 of close; she spends most of that time chasing reasons and wording slides.
- Fear: presenting a reason that turns out to be wrong in front of the CFO.
- Success: a one-paragraph answer she can paste into the review deck, with drivers ranked and gaps clearly marked as "to confirm".

## 3. JTBD
> When I see variance in numbers, I want to explain what caused it clearly and quickly, so I can defend my analysis.

Trigger: the month-end review, when leadership asks why the forecast was missed.

## 4. Why AI? (and where not)
| Part of the job | Needs AI? | Why |
|---|---|---|
| Variance, %, ranking, materiality, Pareto | **No** | Deterministic maths. An LLM would be slower, costlier and sometimes wrong. |
| Price/volume split, recurrence vs last months | **No** | Formula plus database lookup. |
| Deciding which causes the data supports | **No** | Rules on units and evidence tags. This must be auditable. |
| Turning 5–10 facts into a clear leadership paragraph | **Yes** | Language synthesis is where analysts lose time and where LLMs are strong. |
| Writing the right follow-up question for an unexplained line | **Yes** | Context-aware phrasing; rules give generic text. |

AI is therefore a **writer, not an analyst**. If AI were removed, the product would still calculate everything correctly; it would just produce plainer text.

## 5. Feature list: value vs effort
| Feature | Value | Effort | Decision |
|---|---|---|---|
| Structured form with evidence tag per line | High | Low | Build (MVP) |
| Logic layer: variance, tiers, Pareto, price/volume | High | Medium | Build (MVP) |
| AI explanation with guardrails | High | Medium | Build (MVP) |
| Confidence ceiling from evidence | High | Low | Build (MVP) |
| Report history plus recurrence check | High | Medium | Build (MVP) |
| Analyst review (usable / not usable) | Medium | Low | Build (MVP) |
| Admin dashboard (fallback rate, guardrail hits, acceptance) | Medium | Medium | Build (MVP) |
| CSV/Excel upload | Medium | Medium | Next |
| ERP / Google Sheets connector | High | High | Later |
| Multi-month trend charts | Medium | Medium | Later |

## 6. Failure mode analysis
| Failure mode | Impact | Mitigation in VarianceLens |
|---|---|---|
| **AI invents a cause** ("festive demand") | Analyst loses credibility | Allowed-cause list per line; cause words blocked on unexplained lines; analyst notes are the only source of context |
| AI invents or alters numbers | Wrong figures in board pack | Every number in AI text checked against input; mismatch → flagged, confidence ≤ 30 |
| Overconfident explanation | False sense of certainty | Confidence ceiling from evidence (40/70/90); overall capped at 50 if < 50% backed by evidence |
| Analyst mis-tags evidence | Wrong classification | Unit data overrides a conflicting tag, shown as a conflict |
| "One-off" that is actually recurring | Forecast never fixed | Recurrence check across last 3 months |
| Bad input data (sign flip, typo) | Explaining an error | Data-quality flags shown before explanations |
| AI down, quota exceeded, malformed JSON | No output at month-end | Fallback to calculated explanation; report still saved |

## 7. AI trade-offs
- **Model:** Gemini 2.5 Flash (free tier) by default; swappable to Groq or OpenAI via one setting. A small, fast model is enough because reasoning is done by the logic layer.
- **Temperature 0.1:** consistency over creativity.
- **Facts-only prompt vs raw data:** less context for the model, but far fewer hallucinations.
- **Override vs trust:** guardrails sometimes replace decent AI wording with plainer text. That is accepted: a dull correct sentence beats a fluent wrong one.

## 8. Cost & sustainability
- One LLM call per review, about 1.5k input and 0.5k output tokens.
- Gemini free tier: ₹0 for prototype and team-scale use. On paid GPT-4o-mini it would be roughly ₹0.05 per review, or under ₹5 a month for 3 units × 30 reruns.
- No call is made when nothing is material.
- Token usage and latency are tracked on the admin dashboard.
- SQLite is enough for one team; move to Postgres for multi-team use.

## 9. Reflection
*(Rohini: write this in your own words. Prompts: What surprised you while building? Which guardrail mattered most when you tested it? What would you change with another week? What did you learn about when NOT to use AI?)*
