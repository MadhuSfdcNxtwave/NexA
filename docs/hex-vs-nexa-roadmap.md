# Hex vs NexA — Process Guide & Roadmap

How Hex works, how NexA works today, and what to build next so NexA behaves more like Hex.

---

## 1. Hex process (target model)

Hex is a **data workspace**: multi-cell notebooks + warehouse pushdown + an agent that plans cells and documents assumptions.

### Architecture layers

```mermaid
flowchart TB
  subgraph UI["UI layer"]
    NB[Notebook]
    APP[Published App]
    TH[Threads / Magic AI]
    DB[Data Browser]
  end

  subgraph Runtime["Runtime"]
    DAG[Cell dependency DAG]
    KER[Python kernel]
    CACHE[SQL query cache]
  end

  subgraph Data["Data"]
    WH[(Warehouse / BigQuery)]
    END[Endorsed tables]
  end

  TH --> DAG
  NB --> DAG
  APP --> DAG
  DAG --> KER
  DAG --> CACHE
  CACHE --> WH
  DAG --> WH
  DB --> END
  END --> WH
```

### Agent / Threads flow

```mermaid
flowchart TD
  Q[User question] --> I{Intent clear?}
  I -->|No| CL[Clarify: timeframe, definitions, metric]
  CL --> Q
  I -->|Yes| S[Schema search — prefer Endorsed]
  S --> V[Sample / validate columns + joins]
  V --> P[Plan SQL / Python cells]
  P --> R[Run DAG — CTE chain to warehouse]
  R --> A[Answer + Explore + Notes / Assumptions]
  A --> TI[Thread Inspector: Intent · Assets · Assumptions]
```

### Building blocks

| Piece | Role |
|--------|------|
| **SQL cells** | Query warehouse; Query mode keeps heavy work in BQ |
| **Cell DAG** | Downstream cells reference upstream by name |
| **Chained SQL** | Hex compiles cell chain into CTEs → one warehouse query |
| **Python / charts** | Transform and visualize in project kernel |
| **Endorsed tables** | Governance bias for AI |
| **Magic / Threads** | Agent: clarify → discover → validate → answer |
| **Notes / Assumptions** | Self-document definitions (e.g. MAU ≥ 6h) |
| **Thread Inspector** | Visible lineage of intent, assets, assumptions |
| **Query cache** | Reuse identical SQL (~60 min) |

### Example — MAU retention notebook (cell DAG)

```mermaid
flowchart LR
  C1[monthly_platform_time_by_user] --> C3[user_month_retention_bucket_spine]
  C2[academy_users_lp_access_month] --> C3
  C3 --> C4[monthly_mau_retention_summary]
  C4 --> C5[Charts / insights]

  C1 -.->|compiled as CTEs| BQ[(BigQuery)]
  C2 -.-> BQ
  C3 -.-> BQ
  C4 -.-> BQ
```

1. `monthly_platform_time_by_user` — engagement → monthly minutes / `is_active`
2. `academy_users_lp_access_month` — master LP access month
3. `user_month_retention_bucket_spine` — NEW / RETAINED / … buckets
4. `monthly_mau_retention_summary` — aggregates
5. Charts / insights on top

Each step is a **cell**; later cells reference earlier ones. That is the Hex unit of work.

---

## 2. NexA process (today)

NexA is a **Hex-inspired Ask app**: natural language → correct table → SQL → answer. Most turns are still **one Ask → one SQL** (optional short notebook chain).

### Ask pipeline

```mermaid
flowchart TD
  A[Ask question] --> B[Apply clarification if any]
  B --> C[Expand drill-down / breakdown + abbreviations]
  C --> D[Query context]
  D --> E["@table pins + thread context"]
  E --> F{Memory / cache hit?}
  F -->|Yes| Z[Reuse prior answer]
  F -->|Miss| G[Route tables + query plan]
  G --> H{Clarify gate?}
  H -->|Yes| Y[ClarificationDialog — stop]
  Y --> A
  H -->|No| I[Schema + RAG + glossary + rules]
  I --> J{Notebook chain?}
  J -->|Yes| K[plan_notebook_steps → run cells → combine]
  J -->|No| L[SQL waterfall]
  K --> M[Validate + BigQuery]
  L --> M
  M --> N{Fail / empty?}
  N -->|Recover| L
  N -->|OK| O[Chart + analysis]
  O --> P[complete → UI]
```

### SQL waterfall (when not a notebook chain)

```mermaid
flowchart TD
  START[Need SQL] --> T1{Temp query agent}
  T1 -->|Clarify| STOP[awaiting_clarification]
  T1 -->|SQL| DONE[Candidate SQL]
  T1 -->|Skip| T2{Drill-down rewrite}
  T2 -->|Hit| DONE
  T2 -->|Miss| T3{RAG compose}
  T3 -->|Hit| DONE
  T3 -->|Miss| T4{NPS / domain / semantic templates}
  T4 -->|Hit| DONE
  T4 -->|Miss| T5{Learned patterns}
  T5 -->|Hit| DONE
  T5 -->|Miss| T6[LLM SQL]
  T6 --> DONE
  DONE --> VAL[Validate]
  VAL --> BQ[(BigQuery)]
  BQ --> REC{Fail / empty?}
  REC -->|Yes| T6
  REC -->|No| OUT[Presentation]
```

1. **Temp query agent** — plan metric / date / breakdown; clarify if unsure  
2. **Drill-down rewrite** — prior `COUNT` → `user_id` list  
3. **RAG compose** — glossary + retrieval  
4. **NPS / domain / semantic templates** — known metrics  
5. **Learned patterns** — promoted templates  
6. **LLM SQL** — last resort  
7. **Validate → BQ → recover** on failure / empty  

### Capability map

```mermaid
quadrantChart
    title NexA capability maturity vs Hex
    x-axis Low maturity --> High maturity
    y-axis Low Hex impact --> High Hex impact
    quadrant-1 Strengthen next
    quadrant-2 Keep / polish
    quadrant-3 Later
    quadrant-4 Build soon
    Clarification UI: [0.55, 0.85]
    Glossary RAG: [0.65, 0.55]
    Notebook chain: [0.35, 0.90]
    Temp agent: [0.45, 0.70]
    Thread memory: [0.70, 0.50]
    Table mentions: [0.75, 0.40]
    Assumptions panel: [0.15, 0.95]
    Thread Inspector: [0.10, 0.90]
    MAU retention spine: [0.15, 0.85]
    Discovery trail UI: [0.25, 0.80]
```

### What NexA already has

| Capability | Status |
|------------|--------|
| BigQuery + endorsed-style routing | Partial |
| Clarification UI | Partial (not always before SQL) |
| Glossary / term resolver | Yes |
| Notebook step planner + SQL chain | Early |
| Temp query agent | Yes (stopgap) |
| Thread memory / cache | Yes |
| `@table` mentions | Yes |
| Notes / Assumptions panel | No |
| Thread Inspector | No |
| Full MAU retention spine (PM Hex export) | No |
| Always-clarify-before-guess | No |
| Visible discovery trail | Weak (status + SQL tab only) |

---

## 3. Side-by-side

```mermaid
flowchart TB
  subgraph HEX["HEX"]
    HQ[Question] --> HCL{Clear?}
    HCL -->|No| HASK[Clarify]
    HASK --> HQ
    HCL -->|Yes| HDISC[Endorsed schema]
    HDISC --> HVAL[Sample / validate]
    HVAL --> HCELLS[Plan cells]
    HCELLS --> HDAG[DAG + CTE chain]
    HDAG --> HBQ[(BigQuery)]
    HBQ --> HANS[Answer + Notes]
    HANS --> HTI[Thread Inspector]
  end

  subgraph NEXA["NexA today"]
    NQ[Question] --> NCTX[Context / follow-up / pins]
    NCTX --> NMEM{Cache?}
    NMEM -->|Yes| NRET[Reuse]
    NMEM -->|No| NROUTE[Route tables]
    NROUTE --> NCL{Clarify?}
    NCL -->|Yes| NUI[Dialog]
    NUI --> NQ
    NCL -->|No| NPLAN[Chain OR waterfall]
    NPLAN --> NVAL[Validate]
    NVAL --> NBQ[(BigQuery)]
    NBQ --> NPRES[Chart + analysis]
  end
```

| Dimension | Hex | NexA today |
|-----------|-----|------------|
| Unit of work | Multi-cell DAG | One Ask → one SQL (+ optional chain) |
| Ambiguity | Clarify first | Clarify gate + agent; still guesses sometimes |
| Definitions | Project cells + endorsed apps | Glossary, YAML rules, templates |
| Lineage | Thread Inspector | Routing reason + SQL tab |
| Heavy logic | Chained CTEs in warehouse | Single query or short chain |
| Answer shape | Headline + Explore + Notes | Analysis + chart + SQL |
| Governance | Endorsed tables in Data Browser | Included / endorsed flags in project |

**One line:** Hex plans **cells and assumptions**; NexA mostly plans **one SQL path**.

---

## 4. What to do next (become more like Hex)

Ordered by impact. Do these in sequence; each unlocks the next.

### Roadmap overview

```mermaid
flowchart LR
  A[Phase A<br/>Trust] --> B[Phase B<br/>Cells]
  B --> C[Phase C<br/>Agent loop]
  C --> D[Phase D<br/>Polish]

  A1[Clarify first] -.-> A
  A2[Assumptions] -.-> A
  A3[Discovery trail] -.-> A
  A4[Golden CI] -.-> A

  B1[Prefer chain] -.-> B
  B2[Named cells UI] -.-> B
  B4[MAU spine] -.-> B

  C1[Query Agent] -.-> C
  C4[Thread Inspector] -.-> C
```

### Target state (after roadmap)

```mermaid
flowchart TD
  Q[User question] --> CL{Low confidence?}
  CL -->|Yes| ASK[Clarify — never guess]
  ASK --> Q
  CL -->|No| DISC[Discovery trail: tables + reasons]
  DISC --> PLAN{Multi-step metric?}
  PLAN -->|Yes| CELLS[Named notebook cells → CTE chain]
  PLAN -->|No| ONE[Template / agent single SQL]
  CELLS --> RUN[(BigQuery)]
  ONE --> RUN
  RUN --> ANS[Answer + chart]
  ANS --> NOTE[Notes / Assumptions always]
  NOTE --> INS[Thread Inspector<br/>Intent · Assets · Assumptions]
```

### Phase A — Stop wrong answers (trust)

| # | Work | Why (Hex behavior) | Where |
|---|------|--------------------|--------|
| A1 | **Always clarify when confidence is low** — expand `should_clarify_before_sql` + temp agent; never emit SQL on ambiguous metric/date/table | Clarify before run | `ask_clarify.py`, `temp_query_agent.py` |
| A2 | **Notes / Assumptions block** on every answer (date range, metric definition, table chosen, filters) | Notes / Assumptions | Backend complete payload + `AskSection` / InsightCard |
| A3 | **Discovery trail in UI** — tables considered, why chosen, columns matched | Visible discovery | Stream events already exist; surface in Thread panel |
| A4 | **Golden questions CI** — portal, NPS monthly, attendance ranges, drill-down, last calendar month | Regression like Hex apps | `tests/golden_questions.yaml` |

**Done when:** Ambiguous questions ask first; every answer shows assumptions; golden suite stays green.

### Phase B — Cell-first answers (Hex notebook shape)

| # | Work | Why | Where |
|---|------|-----|--------|
| B1 | **Prefer notebook chain** for multi-step metrics (retention, cohort, join+aggregate) | Cell DAG | `notebook_planner.py`, `sql_chain.py` |
| B2 | **Named cells in UI** (Hex-style labels, not only raw SQL steps) | Readable lineage | `SqlNotebookCells.jsx` |
| B3 | **CTE combine + Query-mode mindset** — keep heavy logic in BQ; preview rows in UI | Warehouse pushdown | `sql_chain.combine_sql` |
| B4 | **Port PM MAU retention spine** as endorsed notebook template (cells 1–4 from Hex export) | Match Hex report | New template under domain / notebook steps |

**Done when:** Retention / MAU-style questions produce 3–5 named cells, not one opaque SQL blob.

### Phase C — Agent loop (Hex Threads)

```mermaid
flowchart LR
  P[Plan] --> C[Clarify]
  C --> S[Compose SQL / cells]
  S --> K[Critic validate]
  K -->|Fix| S
  K -->|OK| R[Run BQ]
  R --> I[Inspector payload]
```

| # | Work | Why | Where |
|---|------|-----|--------|
| C1 | **Promote temp agent → real Query Agent** — plan → clarify → compose → critic → run | Magic / Threads loop | `agents/` |
| C2 | **Schema explorer sampling** before join-heavy SQL | Validate like Hex | `agents/schema_explorer.py` |
| C3 | **Query critic always on** for non-template SQL | Catch wrong measure / date | `agents/query_critic.py` |
| C4 | **Thread Inspector panel** — Intent, Assets (tables/SQL cells), Assumptions | Hex Inspector | New frontend panel |

**Done when:** Complex asks show Intent → Assets → Assumptions; critic blocks bad SQL.

### Phase D — Product polish (Hex workspace feel)

| # | Work | Why | Where |
|---|------|-----|--------|
| D1 | Endorsed-only default for AI routing | Governance | `table_routing`, Data tab |
| D2 | Stronger follow-up context (never drop prior table/filters) | Thread continuity | `ask_context`, `question_intent` |
| D3 | Knowledge answers for abbreviations without SQL | Hex can answer without querying | `knowledge_query` path |
| D4 | Optional web / doc search for definitions outside warehouse | Hex Auto + web | Later / optional |

---

## 5. Recommended next 2 weeks

Focus on **Phase A + B4 start** — highest user pain.

```mermaid
gantt
    title Next 2 weeks
    dateFormat  YYYY-MM-DD
    axisFormat  %b %d
    section Week 1
    A1 Clarify gate           :a1, 2026-07-09, 3d
    A2 Assumptions block      :a2, after a1, 2d
    A4 Golden CI cases        :a4, 2026-07-09, 4d
    section Week 2
    A3 Discovery trail UI     :a3, 2026-07-16, 3d
    B1/B2 Named notebook cells :b12, after a3, 3d
    B4 MAU spine scaffold     :b4, 2026-07-16, 5d
```

1. **Week 1**
   - A1: Tighten clarify gate (portal vs events, NPS score vs responders, date ambiguity)
   - A2: Ship Assumptions block on every `complete` response
   - A4: Add failing golden cases from recent bugs

2. **Week 2**
   - A3: Discovery trail UI (tables + reason)
   - B1/B2: Named notebook cells for join + aggregate questions
   - B4: Scaffold MAU retention cells from Hex export (even if charts come later)

Skip Phase D until A/B feel trustworthy.

---

## 6. Success criteria (Hex-like enough)

```mermaid
flowchart LR
  S1[Clarify not guess] --> S2[Assumptions visible]
  S2 --> S3[Named cells for hard metrics]
  S3 --> S4[Core metrics via templates/cells]
  S4 --> S5[Thread Inspector on every turn]
```

NexA is “like Hex” for NxtWave when:

1. Unclear questions **ask** instead of guessing  
2. Answers show **what was assumed** (dates, metric, table)  
3. Multi-step metrics run as **named SQL cells** chained in BQ  
4. Core academy questions (attendance, NPS, portal, placements, MAU retention) hit **templates / cells**, not free-form LLM  
5. Users can open **Intent · Assets · Assumptions** for any Thread turn  

---

## 7. Quick reference — key files

```mermaid
flowchart TB
  UI[AskSection / ClarificationDialog / SqlNotebookCells] --> API[API stream]
  API --> PIPE[ask_pipeline.py]
  PIPE --> CL[ask_clarify]
  PIPE --> CTX[ask_context / question_intent / question_dates]
  PIPE --> PLAN[notebook_planner / sql_chain]
  PIPE --> AG[agents: temp / critic / explorer]
  PIPE --> RAG[glossary / term_resolver / rag]
  PIPE --> BQ[(BigQuery)]
```

| Area | Files |
|------|--------|
| Ask pipeline | `backend/ask_pipeline.py` |
| Clarify | `backend/ask_clarify.py` |
| Dates / intent | `backend/question_dates.py`, `question_intent.py` |
| Notebook cells | `backend/notebook_planner.py`, `sql_chain.py`, `notebook_step_sql.py` |
| Agents | `backend/agents/temp_query_agent.py`, `pipeline_bridge.py`, `query_critic.py` |
| Glossary / RAG | `backend/glossary.yaml`, `rag_pipeline.py`, `term_resolver.py` |
| Frontend Ask | `frontend/src/components/AskSection.jsx`, `ClarificationDialog.jsx`, `SqlNotebookCells.jsx` |

---

## 8. Mental model

```mermaid
flowchart LR
  NOW[NexA today<br/>Ask waterfall<br/>mostly one-shot SQL] -->|Phase A–C| NEXT[NexA next<br/>Clarify-first<br/>Assumptions + named cells<br/>Thread Inspector]
  NEXT -->|Closer to| HEX[Hex<br/>Notebook DAG<br/>cells + assumptions]
```

| | |
|--|--|
| **Hex** | Notebook DAG + agent that plans **cells** and documents **assumptions** |
| **NexA today** | Ask waterfall that aims at Hex answers, mostly **one-shot SQL** |
| **NexA next** | Clarify-first → Assumptions always → Named cells for hard metrics → Thread Inspector |

---

*Last updated: July 2026 — living doc; update as phases ship.*
