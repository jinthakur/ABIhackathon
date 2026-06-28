# Care Claim — Judge Walkthrough

---

## The Problem

Post-acute care facilities bill Medicare Part B for wound care services. Today, a human biller manually reviews every patient chart — reading free-text nursing notes, pulling up coverage records, checking wound measurements — before deciding whether to submit a claim. With 300 patients across three facilities, that's hundreds of hours of repetitive, error-prone work every billing cycle.

**Care Claim automates that entire workflow.**

---

## What We Built

A fully automated, end-to-end clinical data pipeline with a live web dashboard — built in a single hackathon session.

```
PCC API  →  Ingestion  →  Extraction  →  Eligibility  →  SQLite  →  Dashboard
```

### 5 Specialized Agents

| Agent | Job |
|---|---|
| **Patient Agent** | Fetches all 300 patients across Facilities 101, 102, 103 |
| **Clinical Agent** | Pulls diagnoses, coverage, notes, and assessments per patient |
| **Extraction Agent** | Parses wound data from free-text notes and structured assessments |
| **Eligibility Agent** | Applies Medicare Part B rules and issues a routing decision |
| **Orchestrator** | Runs all agents concurrently (15 threads), coordinates DB writes |

---

## The Hard Part: Extraction

The notes come in four radically different formats. All of these mean the same thing to a clinician:

```
SOAP note:      Length: 4.2 cm  Width: 3.1 cm  Depth: 1.5 cm
Prose note:     Meas 4.2x3.1x1.5cm, moderate drainage
Envive note:    Stage III pressure injury sacrum measuring 4.2 × 3.1 × 1.5 cm with moderate serous drainage
Multi-wound:    Wound 1: DFU right heel... Wound 2: Stage II PU coccyx...
```

We built a multi-pattern regex extraction engine that handles all of them — 6 measurement patterns, 11 wound type patterns, 6 stage patterns, 4 drainage levels, and 35 anatomical location keywords. Candidates are scored by how many fields they fill, and the best one wins.

---

## The API Challenge: 429s

The PCC API rate-limits aggressively — **30% of all requests return HTTP 429**. A naive pipeline fails silently and produces blank records.

Our `api_client.py` handles this with:
- Up to **20 retries** per call, respecting the `Retry-After` header
- **15 concurrent workers** so retries on one patient don't block others
- A **failed call log** — any call that exhausts all retries is written to `failed_api_calls` in the DB

---

## Routing Logic

Three outcomes, clearly defined:

| Decision | Criteria |
|---|---|
| ✅ **Auto Accept** | Medicare Part B active + wound type + location + measurements (L×W) + drainage all documented |
| ⚠️ **Flag for Review** | Coverage present but one or more wound fields missing or ambiguous |
| ❌ **Reject** | No Medicare Part B, or no extractable wound data at all |

The reason is always plain English — a biller can read exactly why a patient was routed the way they were, without opening the chart.

---

## The Dashboard

A live Flask web app at `http://localhost:5000`.

**What a biller sees on the home screen:**
- Four stat cards: Auto Accept / Flag for Review / Reject / Total at a glance
- A **donut chart** showing the decision breakdown visually
- A full filterable results table — filter by decision or facility
- A **"Missing Wound Data" table** at the bottom — every patient with a null field, with per-column checkmarks showing exactly what's missing

**On each patient's detail page:**
- Coverage status, wound measurements, ICD-10 diagnoses, and all clinical notes
- An **AI Follow-Up button** — one click calls OpenAI GPT-4o-mini and generates 3–5 clinician-grade next steps based on that patient's specific wound data

**The Retry button** — in the missing data table, each row has a Retry button. It re-fires all four PCC API calls for that patient live, re-runs extraction and eligibility, and updates the row inline without a page reload. No data is permanently lost to a 429.

---

## The Data

| Table | Rows |
|---|---|
| `patients` | 300 |
| `diagnoses` | 875 |
| `coverage` | 300 |
| `clinical_notes` | 474 |
| `assessments` | 300 |
| `wound_extractions` | 288 |
| `eligibility_results` | 300 |

All queryable via the built-in SQL runner in the dashboard, or via the REST API (`/api/results`, `/api/patient/FA-001`, `/api/tables`).

---

## Why It Matters

A biller today opens 300 charts. They read notes in four different formats, check insurance records, do math on wound dimensions, and decide — one patient at a time.

Care Claim does that in minutes, not days. The **auto-accept** cohort goes straight to billing with no human review. The **flag-for-review** cohort gets a prioritized worklist with the exact gap highlighted. The **reject** cohort is filtered out before it wastes anyone's time.

And when the API fails — as it always does in production — no data is silently dropped. The pipeline catches it, logs it, and gives you a one-click path to recover it.

**That's the difference between a script and a production system.**

---

## Team

- Jitender Thakur
- Forrest Pan
- Frank Yu
- Oluwapelumi Adesiyan
