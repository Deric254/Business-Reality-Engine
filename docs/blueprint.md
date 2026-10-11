# BUSINESS REALITY ENGINE
## Full Product Blueprint / Master Template

> Core idea:
> A software intelligence layer that connects to the systems a business already uses, builds a living understanding of the business, continuously watches it, detects what matters, explains why, alerts management, supports decisions, observes outcomes, and builds institutional memory.

**Core promise**
> Keep your existing systems. Add intelligence to them.

**Product behavior**
> If everything is normal → silence.
> If something matters → LOOK HERE.

---

## 1. WHAT THE PRODUCT ACTUALLY IS

The Business Reality Engine is an always-on business intelligence and decision system.

It sits above existing business systems.

It does not replace:
- POS
- ERP
- accounting software
- CRM
- inventory software
- hospital systems
- payment systems
- Excel
- payroll
- production systems

Instead:

```
BUSINESS REALITY ENGINE
                           │
          ┌────────────────┴────────────────┐
          │                                 │
       UNDERSTANDS                       WATCHES
          │                                 │
          └──────────────┬──────────────────┘
                         ↓
                    DETECTS
                         ↓
                    EXPLAINS
                         ↓
                     ALERTS
                         ↓
                 SUPPORTS DECISION
                         ↓
                  OBSERVES OUTCOME
                         ↓
                 BUILDS MEMORY
```

---

## 2. THE PROBLEM

Businesses already generate enormous amounts of information.

The problem is not necessarily lack of data.

> The business cannot continuously understand everything happening inside it.

Information is scattered across POS, Excel, Accounting, CRM, Inventory, Bank/payment systems, Databases, Payroll, Operations, Production, Branches, Websites, External data.

Management usually has to:
1. open different systems
2. export reports
3. combine spreadsheets
4. clean them
5. compare periods
6. look for anomalies
7. investigate
8. decide what matters
9. remember what happened previously

By the time something becomes obvious, the damage may already have happened.

---

## 3. THE PRODUCT'S JOB

The engine has eight fundamental jobs.

1. **CONNECT** — Get authorized data from existing systems.
2. **UNDERSTAND** — Determine what the data represents and how the business operates.
3. **UNIFY** — Bring fragmented information into one coherent business model.
4. **WATCH** — Continuously monitor important variables and relationships.
5. **DETECT** — Find problems, risks, anomalies and opportunities.
6. **EXPLAIN** — Determine what changed and investigate possible drivers.
7. **ALERT** — Tell management what deserves attention.
8. **REMEMBER** — Record situations, decisions and outcomes so future intelligence improves.

---

## 4. THE COMPLETE BUSINESS LOOP

```
CONNECT → INGEST → CLEAN → RECONCILE → UNDERSTAND → MODEL → MONITOR
→ DETECT → INVESTIGATE → EXPLAIN → PRIORITIZE → ALERT → DECISION
→ ACTION → MEASURE → OUTCOME → MEMORY → FUTURE INTELLIGENCE
```

This loop should be reflected throughout the architecture.

---

## 5. UNIVERSAL BUSINESS MODEL

The system should not assume the industry first. It should discover what exists.

**Money** — revenue, expenses, profit, cash, receivables, payables, margins, costs, transactions

**Customers** — customers, acquisition, frequency, retention, churn, customer value, segments

**Products / Services** — products, services, prices, quantities, demand, profitability

**Resources** — inventory, employees, equipment, suppliers, locations, capacity

**Operations** — processes, deliveries, production, service activity, delays, errors, throughput

**Time** — daily, weekly, monthly, seasonal, historical, current, forecast

**Risk** — unusual activity, deterioration, concentration, shortages, cost increases, cash problems, operational failure

---

## 6. BUSINESS KNOWLEDGE GRAPH / RELATIONSHIP MODEL

The engine shouldn't only know individual numbers. It should know relationships.

```
Customer → places → Order → contains → Product → comes from → Inventory → supplied by → Supplier

Sale → Revenue → Payment → Cash

Product → Cost → Margin → Profitability
```

This allows the system to reason about relationships. For example:

> Sales are falling.

is weaker than:

> Sales have fallen 15%, but customer traffic is stable and inventory availability has not changed. The decline is concentrated in three high-value products.

---

## 7. CONNECTOR ARCHITECTURE

```
EXTERNAL SYSTEMS
POS, Accounting, CRM, Excel, Database, Payments, Inventory,
Payroll, ERP, Production, External APIs
          ↓
   CONNECTOR GATEWAY
          ↓
    DATA STANDARDIZATION
          ↓
   BUSINESS REALITY ENGINE
```

The intelligence engine should never care whether information came from Excel or MySQL.

---

## 8. CONNECTOR TYPES

- **A. API** — Business system → REST/GraphQL API → Connector
- **B. Webhooks** — New sale → Webhook → Engine
- **C. Database** — Read-only connections: PostgreSQL, MySQL, SQL Server, Oracle, etc.
- **D. ODBC/JDBC** — For systems exposing database connectivity through drivers
- **E. File ingestion** — CSV, XLSX, JSON, XML, TXT, structured reports
- **F. SFTP** — Scheduled data exchange
- **G. Shared folders** — Legacy environments
- **H. Email report ingestion** — Systems that auto-send structured reports
- **I. Local connector agent** — On-premise systems that cannot expose externally
- **J. Authorized UI automation** — Fallback for certain legacy applications
- **K. Human-assisted capture** — Last resort where no machine-readable access exists

---

## 9. IMPORTANT SECURITY PRINCIPLE

The system must never attempt to bypass security.

> The business must legitimately authorize access.

No: password theft, unauthorized database access, security bypass, scraping restricted systems, defeating authentication.

If there is no legitimate access method, the system must say:

> Data source unavailable.

---

## 10. UNIVERSAL DATA CONTRACT

Every connector converts its source into a standard internal representation:

```
source, entity, entity_id, timestamp, event_type, amount, quantity,
currency, location, customer_id, product_id, employee_id, supplier_id, metadata
```

> Different systems speak different languages. The engine needs one internal business language.

---

## 11. DATA INGESTION ENGINE

Handles: receiving data, validating, timestamping, identifying source, schema detection, incremental updates, duplicates, failed records, missing fields, data freshness.

Every incoming record should have provenance:

```
Source: POS
Imported: 12:41:08
Record: SALE-92882
Connector: POS Connector
Status: Validated
```

---

## 12. DATA QUALITY ENGINE

- **Completeness** — Are required fields missing?
- **Accuracy** — Do values make sense?
- **Consistency** — Does the same entity have conflicting values?
- **Duplication** — Is the same transaction appearing twice?
- **Timeliness** — Is the data stale?
- **Validity** — Are values within expected ranges?

---

## 13. ENTITY RESOLUTION

```
POS: "John Mwangi"
Payment system: "J. Mwangi"
Excel: "John M."
Customer database: "JM001"
          ↓
   Same underlying customer
```

Applies to: customers, products, suppliers, employees, branches, assets.

The engine should maintain confidence scores and allow corrections.

---

## 14. BUSINESS DISCOVERY

When a business connects its systems, the engine asks:

> What kind of business reality exists here?

It discovers: entities, transactions, money flows, products, customers, suppliers, locations, processes, relationships, metrics, seasonality, operational patterns.

> A living digital representation of the business.

---

## 15. DATA COVERAGE MAP

The system must know what it doesn't know.

```
BUSINESS DATA COVERAGE
Sales       🟢 Live
Inventory   🟢 5 min ago
Customers   🟢 10 min ago
Payments    🟢 Live
Expenses    🟡 Daily
Payroll     🟡 Weekly
Suppliers   🔴 Not connected
Production  🟡 2 hrs ago
```

> ⚠️ Supplier data has not been received for six days. Margin analysis may be incomplete.

---

## 16. THE MONITORING ENGINE

**Individual variables** — Revenue, Sales, Expenses, Inventory, Customers, Payments, Costs

**Relationships** — Sales vs inventory, Revenue vs expenses, Customers vs revenue, Price vs demand, Inventory vs sales, Staff vs productivity, Collections vs cash, Supplier cost vs margin

---

## 17. NOT EVERYTHING NEEDS SECOND-BY-SECOND MONITORING

```
Real-time       → seconds/minutes
Near real-time  → minutes
Periodic        → hourly/daily
Slow-moving     → weekly/monthly
```

The system should know the expected freshness of each source.

---

## 18. EVENT ENGINE

```
New transaction → Connector receives event → Normalize → Update business model
→ Recalculate affected metrics → Evaluate rules → Evaluate anomalies
→ Check relationships → Generate/close/update alert
```

More efficient than recalculating the entire business every second.

---

## 19. DETECTION ENGINE

- **Rules** — If cash < minimum threshold → critical alert
- **Thresholds** — Expense increase > 20%
- **Historical comparison** — Current sales vs historical normal
- **Trend detection** — Sales declining continuously
- **Anomaly detection** — Unusual transaction pattern
- **Seasonality** — Expected December increase
- **Relationship analysis** — Sales ↓, Inventory stable, Traffic stable, Competitor price ↓
- **Forecast deviation** — Expected revenue 10M, actual trajectory 7.8M

---

## 20. DETECT FOUR THINGS

- **Problems** — Something is going wrong.
- **Risks** — Something may become a problem.
- **Opportunities** — Something positive may be exploited.
- **Unknowns** — The system lacks enough information to confidently assess something.

---

## 21. ALERT ENGINE

Not:
> Sales decreased 12%.

Instead:
> 🔴 ATTENTION: Sales deterioration detected
> Sales have fallen 12% over the last 21 days.
> The decline is concentrated in 3 products and 2 locations.
> Inventory availability remains normal.
> Customer traffic is broadly stable.
> Possible areas to investigate: product demand, pricing or sales execution.
> Confidence: Medium | Data coverage: 87% | Priority: High

---

## 22. ALERT STRUCTURE

Alert ID, Severity, Title, What happened?, Magnitude, Time period, Where?, Who/what affected?, Evidence, Related variables, Possible explanations, Confidence, Data coverage, Recommended investigation/action, Priority, Created at, Current status.

---

## 23. ALERT SEVERITY

🔴 Critical · 🟠 High · 🟡 Medium · 🔵 Informational · 🟢 Opportunity

(Exact labels configurable.)

---

## 24. ALERT STATES

```
Detected → Investigating → Acknowledged → Action planned
→ Action taken → Monitoring → Resolved

Detected → Dismissed
Detected → False positive
```

---

## 25. ALERT FATIGUE CONTROL

Needs: deduplication, grouping, suppression, severity ranking, escalation, cooldown periods, alert dependencies, alert correlation.

Instead of: Revenue down / Sales down / Orders down / Customers down —
generate: 🔴 Revenue deterioration detected, with findings underneath.

---

## 26. ALERT CORRELATION

```
Sales ↓, Orders ↓, Revenue ↓, Customer frequency ↓
→ Customer demand deterioration detected
```

(One alert, not four.)

---

## 27. ROOT-CAUSE / EXPLANATION ENGINE

```
Observation → Possible drivers → Evidence for each → Evidence against each → Confidence
```

| Possible driver | Evidence |
|---|---|
| Pricing | Competitor price decreased 10% |
| Inventory | Stock availability normal |
| Customer traffic | Stable |
| Product mix | High-value product sales fell sharply |

> Most supported hypothesis: pricing pressure. Confidence: medium.

---

## 28. AI'S ROLE

Bad architecture: `Raw Excel → LLM → "What is wrong?"`

Better:
```
Raw data → Data processing → SQL/statistics/analytics → Evidence
→ Business model → Historical memory → Business rules
→ LLM reasoning → Explanation/recommendation
```

AI becomes the reasoning layer, not the whole engine.

---

## 29. AI SHOULD RECEIVE STRUCTURED EVIDENCE

```json
{
  "metric": "revenue",
  "current": 8200000,
  "baseline": 9700000,
  "change": -15.46,
  "period": "21 days",
  "affected_locations": ["Route B", "Route C"],
  "inventory_status": "normal",
  "customer_traffic": "stable"
}
```

---

## 30. RECOMMENDATION ENGINE

> Recommended next step: Review Route B collection operations and the 15 farmers responsible for most of the decline.

Recommendations remain advisory.

---

## 31. HUMAN CONTROL

The engine never silently makes consequential business decisions.

Product provides: Evidence + Analysis + Recommendation + Historical experience. Management decides.

---

## 32. THE MANAGEMENT CONTROL ROOM

Not a traditional dashboard. The homepage should answer:

> What deserves my attention right now?

---

## 33. HOME SCREEN

```
BUSINESS REALITY
Good morning.
🔴 2 Critical  🟠 4 High  🟡 7 Medium  🟢 3 Opportunities

WHAT NEEDS ATTENTION
🔴 Cash collection deterioration
🟠 Margin compression
🟠 Inventory anomaly

OPPORTUNITIES
🟢 Product X demand increasing
🟢 Customer segment Y expanding
```

---

## 34. BUSINESS HEALTH

A summary can exist but should not become the product.

```
Revenue    Stable
Cash       Warning
Margins    Warning
Customers  Healthy
Inventory  Healthy
Operations Stable
```

Clicking should lead to evidence.

---

## 35. ALERT DETAIL PAGE

WHAT HAPPENED? / WHEN? / WHERE? / WHAT CHANGED? / WHAT DID NOT CHANGE? / POSSIBLE DRIVERS / HISTORICAL EXPERIENCE / WHAT HAPPENED THEN? / RECOMMENDED ACTION / CONFIDENCE / DATA COVERAGE

Actions: `[Investigate] [We're doing this] [Already handled] [Not doing this]`

---

## 36. BUSINESS MEMORY

The engine remembers: problems, evidence, recommendations, decisions, actions, outcomes, context, similar situations.

```
CASE #1842
Problem: Route B collection decline
Detected: 12 March
Evidence: 18% decline
Recommendation: Investigate route operations
Decision: Management approved investigation
Outcome: Collections increased 11.4%
Attribution confidence: Medium
```

---

## 37. THE SYSTEM SHOULD NOT DEPEND ON MANUAL RECORDING

> The system should learn primarily by observing business outcomes.

Minimal interaction: `[Doing this] [Already handled] [Not doing this]` — then the system continues watching.

---

## 38. OUTCOME MONITORING

```
Alert → Recommendation → Management acts → Business changes
```

The engine asks: *Did the situation improve?* — comparing before vs after, while considering baseline, time period, other changes, seasonality, unrelated events, data quality.

---

## 39. DON'T CONFUSE CORRELATION WITH CAUSATION

Not: "Our recommendation caused sales to increase."

Instead: "Sales improved 12% following the intervention. Other factors also changed, so attribution is uncertain."

This is essential for credibility.

---

## 40. HISTORICAL CASE RETRIEVAL

```
Current problem → Search business memory → Find similar historical cases
→ Compare conditions → Retrieve outcomes → Give evidence to AI → Current recommendation
```

> This resembles three previous situations. Intervention A produced improvement in two cases; intervention B produced no measurable improvement.

---

## 41. BUSINESS MEMORY IS NOT JUST A CHAT HISTORY

Structured as: Case, Problem, Evidence, Context, Recommendation, Decision, Intervention, Expected outcome, Measurement period, Actual outcome, Attribution confidence.

---

## 42. EXTERNAL CONTEXT

Optional: weather, market prices, exchange rates, public economic data, competitor prices, regulations, holidays, industry indicators.

```
Sales ↓ 14% + Heavy rainfall + Customer traffic ↓
```

External information must remain clearly separated from business-owned data.

---

## 43. FORECASTING

```
Current trend → Forecast → Expected future state → Risk threshold → Early warning
```

> At the current expense trajectory, cash reserves may fall below the business's defined safety threshold within 26 days.

---

## 44. SCENARIO SIMULATION

> What happens if we increase price 5%? / What if expenses increase another 10%?

The engine can simulate likely outcomes using historical/business data, showing assumptions and uncertainty.

---

## 45. BUSINESS QUESTIONS INTERFACE

Should query the business model rather than act as a generic chatbot.

```
Q: Why did profit fall this month?
A: Profit ↓ 13%
   Main contributors: Revenue ↓ 6%, Supplier costs ↑ 11%, Labour costs ↑ 4%
   Largest contributor: Supplier cost increase.
   Evidence: Supplier X accounts for 42% of the increase.
```

---

## 46. NOT EVERYTHING NEEDS AI

- **SQL** — aggregation, joins, filtering, historical queries
- **Python/Polars/Pandas** — analysis, statistics, anomaly detection, transformation
- **ML** — prediction, classification, anomaly detection, forecasting
- **Rules** — known business policies, thresholds, compliance
- **LLM** — reasoning, explanation, synthesis, natural language, contextual recommendation

---

## 47. TECHNICAL ARCHITECTURE

For the local build:

```
                    WEB CLIENT
                 React / Next.js
                         │
                         ↓
                    API LAYER
                     FastAPI
                         │
          ┌──────────────┼──────────────┐
          ↓              ↓              ↓
     Business Model   Alerts        User/Auth
          │              │
          ↓              ↓
       Analytics      Reasoning
          │              │
          └───────┬──────┘
                  ↓
          BUSINESS MEMORY
                  │
                  ↓
             DATABASE

CONNECTOR SERVICE
       │
       ├── CSV
       ├── Excel
       ├── Database
       ├── API
       ├── Webhook
       └── SFTP / Email / Local Agent
```

---

## 48. DATA SECURITY & ACCESS CONTROL

Connecting to a business's systems is only step one. What happens to that data afterward needs its own layer.

```
Ingested data → Encryption at rest → Role-based access → Audit log → Retention policy
```

**Principles:**
- Encrypt data in transit (TLS) and at rest.
- Role-based access control (RBAC): not every user in a business should see payroll, margins, or customer PII.
- Every read/write to sensitive data is logged — who accessed what, when.
- Multi-tenant isolation: if the engine serves multiple businesses, one tenant's data must never leak into another's model, alerts, or memory.
- Data minimization: connectors pull what's needed for the business model, not everything a source system exposes.
- Clear data ownership: the business owns its data; the engine processes it under agreed terms.

---

## 49. ALERT DELIVERY

Detecting something is not the same as it reaching a human in time.

```
Alert generated → Route by severity/preference → Deliver → Confirm receipt → Escalate if unacknowledged
```

**Channels:** in-app (Control Room), email, SMS, push notification, WhatsApp, Slack/Teams.

**Rules:**
- Critical alerts should support multi-channel delivery and escalation if not acknowledged within a defined window.
- Delivery preferences are configurable per user/role (a finance manager may want margin alerts; an ops lead wants inventory alerts).
- A digest mode for medium/low severity items (daily/weekly rollup) prevents channel fatigue distinct from in-app alert fatigue control (Section 25).

---

## 50. CONFIGURATION & ONBOARDING

The engine can't fully self-configure on day one — someone has to tell it what matters for this business.

```
Connect sources → Business discovery (auto) → Review detected model →
Set/confirm thresholds → Assign alert routing → Go live
```

- Initial onboarding walks the business through confirming what Business Discovery (Section 14) found — correcting entity resolution, naming branches/products, confirming currency and time zone.
- Thresholds and rules start from sensible defaults (industry-informed) and are adjustable, not fixed.
- Ongoing configuration lives in the Control Room, not a separate admin tool — the business should be able to mute, retune, or expand monitoring without vendor involvement.

---

## 51. SYSTEM SELF-MONITORING

The engine watches the business. Something has to watch the engine.

**Track:**
- Connector uptime and last successful sync per source
- Ingestion lag and failed record rate
- Alert delivery failures
- LLM call volume, latency, and error rate
- Data quality score trends (from Section 12)

This feeds an internal ops view (for the vendor) separate from the customer-facing Data Coverage Map (Section 15), which is the business-facing symptom of the same underlying health data.

---

## 52. DEPLOYMENT MODEL

Not addressed elsewhere: how this actually gets hosted and delivered.

- **SaaS (multi-tenant)** — fastest to scale, lowest cost per customer, requires strong tenant isolation (Section 48).
- **On-premise / local agent** — needed for businesses (e.g. hospitals) whose systems can't expose data externally; pairs with the Local Connector Agent (Section 8-I).
- **Hybrid** — local agent handles ingestion/normalization on-site, reasoning and Control Room run in the cloud.

The connector architecture (Section 7) already anticipates this via the Local Connector Agent — deployment model should be a per-customer choice, not a platform fork.

---

## 53. BUSINESS MODEL VERSIONING & CHANGE MANAGEMENT

Businesses change shape over time — a new branch opens, a product line is discontinued, a system is replaced. The business model (Section 5-6) must adapt without corrupting history.

```
Change detected (new entity, discontinued source, restructured branch)
→ Version the affected model segment
→ Preserve historical comparisons against the old structure
→ Re-baseline forward-looking monitoring
```

- Historical alerts and cases (Section 36) should reference the model version active at the time, so past evidence stays interpretable even after the business restructures.
- When a connected system is replaced (e.g. new POS), entity resolution (Section 13) must reconcile the old and new source under the same underlying entities.

---

## 54. AI COST CONTROL

Section 28-29 route evidence to an LLM for reasoning — this has a cost curve as alert volume and business count grow.

- Cache reasoning outputs for identical/near-identical evidence patterns.
- Reserve LLM calls for alerts above a severity/confidence threshold; let deterministic layers (Section 46) fully resolve routine cases.
- Batch or defer LLM reasoning for low-priority/informational alerts (digest-time reasoning rather than real-time).
- Track cost per alert and per tenant as an operational metric (feeds Section 51).

---

## 55. VALIDATION & ACCURACY TESTING

Before a business trusts the engine's judgment, its judgment needs to be tested.

```
Historical data → Replay through detection engine → Compare generated alerts
against known real outcomes → Measure precision/recall → Tune thresholds
```

- Backtest the detection and anomaly engines against a business's historical data before go-live, where available.
- Track false-positive and false-negative rates per alert type over time, not just at launch.
- Confidence scores (used throughout: Sections 21, 27, 35) should be calibrated against actual outcome accuracy, not asserted arbitrarily.

---

## 56. ADVISORY BOUNDARIES

Given Section 31 (Human Control), the product's role needs an explicit boundary, not just a design principle.

> The engine provides evidence, analysis, and advisory recommendations. It does not execute business decisions and is not a substitute for professional judgment (financial, legal, medical, or operational) in regulated contexts.

This should be visible in the product itself — e.g. on recommendation cards — not just in a terms-of-service document, especially for regulated domains like hospital systems.

---

## 57. LOCALIZATION

For businesses operating across regions or currencies:

- Multi-currency support in the Universal Data Contract (Section 10), with FX handling for cross-currency comparisons.
- Timezone-aware timestamping so "daily" and "21 days" (as used throughout alerts) mean the business's local day, not UTC.
- Regional business calendars — holidays, seasonality baselines (Section 19) — should be configurable per market rather than assumed globally.
- Language support for the Control Room and alert text, where the business's management team isn't English-first.

---

## CLOSING NOTE

Sections 1-47 define what the engine *understands and decides*. Sections 48-57 define what makes it *trustworthy, secure, and operable* in production — without these, the engine can be technically correct and still fail on adoption, compliance, or cost grounds.
