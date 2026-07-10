"""Analyze workspace tables (sample + YAML) and generate business_rules for Ask."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import credentials

credentials.bootstrap_gcp_credentials()
import bq
import model_yaml
import yaml
from semantic_layer import load_semantic_catalog

OUT = Path(__file__).parent / "generated_table_business_rules.json"
YAML_PATH = Path(__file__).parent / "workspace_models.yaml"


def _short(fq: str) -> str:
    return fq.rsplit(".", 1)[-1]


def _safe_ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""):
        return ""
    return name


def sample_table(fq: str, cols: list[str]) -> dict[str, Any]:
    """Lightweight profile: approx rows, sample values, null rates for key cols."""
    info: dict[str, Any] = {"full_table_id": fq, "error": None}
    try:
        meta = bq.table_metadata(fq)
        info["table_type"] = meta.get("table_type")
        info["num_rows"] = meta.get("num_rows")
        bq_cols = [c["name"] for c in (meta.get("columns") or [])]
        info["columns"] = bq_cols
        colset = set(bq_cols)

        # Prefer YAML cols order, else BQ
        probe_cols = [c for c in cols if c in colset][:12]
        if not probe_cols:
            probe_cols = bq_cols[:12]

        # Sample a few rows
        select_list = ", ".join(f"`{c}`" for c in probe_cols) if probe_cols else "*"
        sample_sql = f"SELECT {select_list} FROM `{fq}` LIMIT 5"
        df = bq.run_query(bq.prepare_sql(sample_sql))
        info["sample"] = df.astype(str).to_dict(orient="records") if len(df) else []

        # Distinct users + null rates for common filter columns
        stats_parts: list[str] = []
        if "user_id" in colset:
            stats_parts.append("COUNT(DISTINCT `user_id`) AS distinct_users")
            stats_parts.append("COUNT(*) AS row_count")
        else:
            stats_parts.append("COUNT(*) AS row_count")

        for c in (
            "pause_status",
            "learning_portal_onboarding_access_given_datetime",
            "attendance_status",
            "lp_status",
            "payment_status",
            "completion_status",
            "rating_on_scale_of_0_to_10",
            "is_active_user",
            "has_platform_time",
        ):
            if c in colset and _safe_ident(c):
                stats_parts.append(
                    f"COUNTIF(`{c}` IS NULL) AS null_{c}"
                )
                if c in ("attendance_status", "lp_status", "payment_status", "completion_status"):
                    # top values later
                    pass

        stats_sql = f"SELECT {', '.join(stats_parts)} FROM `{fq}`"
        # Cap scan cost for huge tables — still OK for analytics workspace
        sdf = bq.run_query(bq.prepare_sql(stats_sql))
        if len(sdf):
            info["stats"] = {k: (None if str(v) == "nan" else v) for k, v in sdf.iloc[0].to_dict().items()}

        # Enum-ish columns: top values
        enums: dict[str, list] = {}
        for c in ("attendance_status", "lp_status", "payment_status", "completion_status", "pause_status", "job_type", "placed_through"):
            if c not in colset:
                continue
            top_sql = (
                f"SELECT CAST(`{c}` AS STRING) AS v, COUNT(*) AS n "
                f"FROM `{fq}` GROUP BY 1 ORDER BY n DESC LIMIT 8"
            )
            tdf = bq.run_query(bq.prepare_sql(top_sql))
            enums[c] = [
                {"value": None if str(r["v"]) in ("None", "nan", "<NA>") else r["v"], "n": int(r["n"])}
                for _, r in tdf.iterrows()
            ]
        info["enums"] = enums
    except Exception as e:
        info["error"] = str(e)[:300]
    return info


def infer_rules(short: str, desc: str, measures: list, dims: list, profile: dict) -> str:
    """Build concise business rules from YAML + sample profile."""
    lines: list[str] = []
    desc_l = (desc or "").lower()
    colset = set(profile.get("columns") or [])
    enums = profile.get("enums") or {}
    stats = profile.get("stats") or {}

    # Grain heuristics
    grain = None
    if short == "z_ccbp_academy_users_master_data":
        grain = "One row per Academy user (user_id). This table IS the active learning-portal user universe."
        lines.append(grain)
        lines.append("Every row is an active learning portal user.")
        lines.append("Do not add WHERE filters for active portal user counts.")
        lines.append("Use COUNT(DISTINCT user_id) with no pause_status or onboarding-access filters.")
        lines.append("Use pause_status / learning_portal_onboarding_access_given_datetime only if the question explicitly asks about paused users or onboarding access.")
        return "\n".join(lines)

    if "one row per" in desc_l or "1 row" in desc_l or "grain" in desc_l:
        # pull a short grain sentence from description
        for sent in re.split(r"[.\n]", desc or ""):
            s = sent.strip()
            if re.search(r"one row|1 row|grain|per user", s, re.I):
                grain = s[:220]
                break
    if not grain:
        if "user_id" in colset and any(k.endswith("_key") or "date" in k for k in colset):
            if any(x in short for x in ("day", "daily", "date")):
                grain = "Likely one row per user per day (or user×day×page)."
            elif any(x in short for x in ("month", "monthly")):
                grain = "Likely one row per user per month."
            elif "attendance" in short or "slot" in short:
                grain = "Likely one row per user per live-class slot."
            elif "job" in short:
                grain = "Likely one row per user×job interaction."
            elif "placement" in short:
                grain = "Likely one row per placement / offer record (user may have multiple)."
            elif "form_response" in short or "feedback" in short or "nps" in short:
                grain = "Likely one row per form submission or feedback answer."
            elif "cloudwatch" in short or "interaction" in short:
                grain = "Event-level: one row per user interaction event (not unique users)."
            else:
                grain = "User-attributed fact table — confirm grain before counting."
        elif "user_id" in colset:
            grain = "Contains user_id — prefer COUNT(DISTINCT user_id) for user counts."
        else:
            grain = "Check primary key / grain before aggregating."

    lines.append(f"Grain: {grain}")

    # Measure-driven rules
    for m in measures[:6]:
        mid = getattr(m, "id", "") or ""
        mdesc = (getattr(m, "description", "") or "").strip()
        filters = list(getattr(m, "filters", None) or [])
        func = (getattr(m, "func", "") or "").lower()
        of_col = getattr(m, "of_column", "") or ""
        if mid in ("count_of_records",):
            continue
        bit = f"Measure `{mid}`: {func}"
        if of_col:
            bit += f"({of_col})"
        if filters:
            bit += f" with filters {filters}"
        if mdesc:
            bit += f" — {mdesc[:160]}"
        lines.append(bit)

    # Domain-specific defaults from short name + enums
    if "live_classes_attendance" in short or short.endswith("attendance_and_time_spent_details"):
        lines.append("For attendance / attended / joined questions: filter attendance_status = 'JOINED'.")
        if "attendance_status" in enums:
            vals = ", ".join(f"{e['value']} ({e['n']})" for e in enums["attendance_status"][:5])
            lines.append(f"attendance_status values seen: {vals}")
        lines.append("Date column is typically slot_date (or slot_start_time). Use COUNT(DISTINCT user_id).")

    if short == "academy_users_day_and_page_wise_time_spent_details":
        lines.append("Page×day engagement grain — NOT the default for 'active portal users'.")
        lines.append("Only use lp_status = 'ACTIVE' when the question explicitly mentions lp_status.")
        lines.append("Do not use this table for general active learning portal user counts (use master_data).")

    if short == "y_academy_user_daily_engagement_time_spent":
        lines.append("Daily platform time-spent. For 'active on platform' use has_platform_time / time_spent_on_platform_in_minutes > 0.")
        lines.append("Date column: calendar_date. COUNT(DISTINCT user_id) for unique active users.")

    if "placement" in short and "eligibility" not in short and "profile" not in short:
        lines.append("Presence in this table indicates a placement/offer record.")
        lines.append("For placed-user counts: COUNT(DISTINCT user_id) — do not invent extra status filters unless asked.")
        if "date_of_placement" in colset:
            lines.append("Date filter column: date_of_placement when a time range is asked.")

    if "jobs_details" in short:
        lines.append("Job application lifecycle table (user×job).")
        lines.append("For applications/applied counts use COUNT(*) or COUNT(DISTINCT user_id) as asked; filter by applied datetime columns when present.")

    if "nps" in short:
        lines.append("NPS / rating responses. Prefer rating_on_scale_of_0_to_10 when scoring.")
        lines.append("Ignore NULL ratings for average NPS. Topic search may need UNION across NPS tables.")

    if "contextual_feedback" in short:
        lines.append("Long-form feedback: group by user_answer / question_text; filter with LIKE on question_text or feedback_trigger.")
        lines.append("Do not confuse with NPS tables.")

    if "cloudwatch" in short or "interaction" in short:
        lines.append("Interaction/event log — COUNT(*) = events; COUNT(DISTINCT user_id) = unique users who interacted.")
        lines.append("Do not treat as attendance or master user universe.")

    if "form_response" in short or short.endswith("_form_responses"):
        lines.append("Form submissions. One user may submit multiple times — use COUNT(*) for responses, COUNT(DISTINCT user_id) for unique submitters.")
        if "form_submission_datetime" in colset:
            lines.append("Timestamp: form_submission_datetime.")

    if "salesforce" in short or "tatatele" in short or "call_details" in short:
        lines.append("Ops/CRM activity table — not learning-portal activity. Join to master on user_id when restricting to Academy users.")

    if "certificate" in short or "completion" in short:
        lines.append("Progress/completion grain is usually user×course (or user×course×version). Filter completion_status when counting completers.")

    if "nbfc" in short or "payment" in short or "npc_master" in short or "installment" in short:
        lines.append("Payments / NBFC / retention finance table. Use payment_status / due_date carefully; do not mix with portal activity.")

    if "discussion" in short:
        lines.append("Discussions may include non-Academy users — join/filter to master_data when question says Academy-only.")

    if "leaderboard" in short or "league" in short or "aipl" in short:
        lines.append("Gamification / leaderboard snapshots — grain is user×period (day or month).")

    if "recording" in short and "watched" in short:
        lines.append("Recording watch events — not live attendance. Use watched_date / first_watched_time for dates.")

    if "virtual_meet" in short:
        lines.append("Virtual meetup attendance/chat — separate from live classes attendance table.")

    if "question_attempt" in short or "question_wise" in short or "question_set" in short:
        lines.append("Learning assessment attempts — grain is user×question (or attempt). Prefer this for question-set analytics, not portal active users.")

    if "search_bar" in short:
        lines.append("Search events — COUNT(*) searches; COUNT(DISTINCT user_id) unique searchers.")

    if "bot" in short or "chat" in short:
        lines.append("AI chatbot sessions/tickets/conversations — support analytics, not portal MAU.")

    if "profile_basic" in short or "profile_education" in short:
        lines.append("Profile attributes (demographics/education). Join to facts for breakdowns; not for activity counts.")

    if "relative_day_progress" in short:
        lines.append("Checkpoint snapshots at fixed days since onboarding (Day 1..180). Filter capturing_day / capturing_point.")

    if "month_wise_login" in short or "month_wise_streak" in short:
        lines.append("Monthly summary metrics — one row per user×month. Use month column for time filters.")

    if "topin" in short or "assessment" in short:
        lines.append("Assessment attempts — filter attempted_tag / scores as asked; one row per user×assessment (or section).")

    if "eligibility" in short:
        lines.append("Placement eligibility best attempts by stage — use stage status/score columns; not the placements outcomes table.")

    if "event_engagement" in short or "nw_events" in short:
        lines.append("Event registration/engagement — not live class attendance.")

    if "link_id_access" in short:
        lines.append("SSO link access log — each row is an access event.")

    if "dpd" in short:
        lines.append("DPD/collections Salesforce tasks — finance ops, not learning activity.")

    # Stats hint
    if stats.get("row_count") is not None and stats.get("distinct_users") is not None:
        try:
            rc = int(stats["row_count"])
            du = int(stats["distinct_users"])
            if rc > 0 and du > 0:
                ratio = rc / du
                if ratio > 1.5:
                    lines.append(f"Approx {rc:,} rows / {du:,} distinct users (~{ratio:.1f} rows/user) — do not COUNT(*) when asking for unique users.")
                else:
                    lines.append(f"Approx {rc:,} rows / {du:,} distinct users — near user grain.")
        except Exception:
            pass

    # Always-on SQL hygiene
    lines.append("Use exact column names from schema. Prefer COUNT(DISTINCT user_id) for unique users.")
    lines.append("Add date filters only when the question mentions a time period.")

    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for ln in lines:
        key = ln.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(ln.strip())
    return "\n".join(out)


def main() -> None:
    catalog = load_semantic_catalog()
    # Unique FQs from YAML importable models
    text = YAML_PATH.read_text(encoding="utf-8")
    models: list[dict] = []
    seen_fq: set[str] = set()
    for chunk in model_yaml._split_model_documents(text):
        doc = yaml.safe_load(chunk)
        if not isinstance(doc, dict):
            continue
        if str(doc.get("type") or "").lower() == "logical_model":
            continue
        if not (doc.get("base_sql_table") or doc.get("base_table") or doc.get("sql_table")):
            continue
        try:
            m = model_yaml.parse_model_document(doc)
        except model_yaml.ModelYamlError:
            continue
        fq = m["full_table_id"]
        if fq in seen_fq:
            continue
        seen_fq.add(fq)
        models.append(m)

    print(f"Analyzing {len(models)} unique tables...")
    results: dict[str, Any] = {}

    for i, m in enumerate(models, 1):
        fq = m["full_table_id"]
        short = _short(fq)
        print(f"[{i}/{len(models)}] {short} ...", flush=True)
        cols = list((m.get("column_descriptions") or {}).keys())
        profile = sample_table(fq, cols)
        sem = catalog.get(short)
        measures = list(sem.measures) if sem else []
        dims = list(sem.dimensions) if sem else []
        desc = (m.get("description") or (sem.description if sem else "") or "").strip()
        rules = infer_rules(short, desc, measures, dims, profile)
        results[fq] = {
            "short": short,
            "business_rules": rules,
            "num_rows": profile.get("num_rows"),
            "table_type": profile.get("table_type"),
            "stats": profile.get("stats"),
            "enums": profile.get("enums"),
            "error": profile.get("error"),
            "sample_preview": (profile.get("sample") or [])[:2],
        }
        if profile.get("error"):
            print(f"  ! {profile['error'][:120]}")
        else:
            print(f"  rows~={profile.get('num_rows')} rules_chars={len(rules)}")

    OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
