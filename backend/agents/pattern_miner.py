"""PatternMinerAgent — learn SQL patterns from successful verification logs."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

import config
from db import LearnedTemplate, SessionLocal, SqlVerificationLog
from memory_lookup import normalize_question

_STOP_WORDS = frozenset(
    """
    how many what is are the a an of by in for show me get total give find
    do does did can could would should will with from that this those these
    and or not on at to into about all any some their them they we you your
    our us be been being have has had was were am i my it its
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class PatternMatch:
    """A stored question/SQL pattern matched against a new question."""

    question: str
    sql: str
    score: float
    frequency: int
    expected_row_count: int | None
    source: str  # "log" | "template"
    template_id: int | None = None
    name: str = ""


def extract_keywords(question: str) -> frozenset[str]:
    """Tokenize a question, dropping stop words and very short tokens."""
    q = normalize_question(question)
    tokens = _WORD_RE.findall(q)
    return frozenset(t for t in tokens if len(t) >= 2 and t not in _STOP_WORDS)


def keyword_overlap_score(question_kw: frozenset[str], pattern_kw: frozenset[str]) -> float:
    if not question_kw or not pattern_kw:
        return 0.0
    overlap = len(question_kw & pattern_kw)
    return overlap / len(question_kw)


@dataclass
class _LoadedPattern:
    question: str
    sql: str
    keywords: frozenset[str]
    frequency: int
    expected_row_count: int | None
    source: str
    template_id: int | None = None
    name: str = ""


class PatternMinerAgent:
    """Mine and match successful SQL patterns from verification logs + templates."""

    def __init__(
        self,
        *,
        match_threshold: float | None = None,
        refresh_minutes: float | None = None,
    ):
        self._match_threshold = match_threshold or float(
            getattr(config, "PATTERN_MINER_MATCH_THRESHOLD", 0.65)
        )
        self._refresh_interval = timedelta(
            minutes=refresh_minutes or float(getattr(config, "PATTERN_MINER_REFRESH_MINUTES", 30))
        )
        self._patterns: list[_LoadedPattern] = []
        self._loaded_at: datetime | None = None
        self._lock = threading.Lock()

    # -- public API ---------------------------------------------------------

    def refresh(self, db: Any | None = None) -> int:
        """Reload patterns from DB. Returns count loaded."""
        own_session = db is None
        if own_session:
            db = SessionLocal()
        try:
            patterns = self._load_patterns(db)
            with self._lock:
                self._patterns = patterns
                self._loaded_at = datetime.utcnow()
            return len(patterns)
        finally:
            if own_session and db is not None:
                db.close()

    def find_matching_pattern(self, question: str) -> PatternMatch | None:
        """Return best pattern if keyword overlap >= threshold."""
        self._maybe_refresh()
        q_kw = extract_keywords(question)
        if not q_kw:
            return None

        best: PatternMatch | None = None
        with self._lock:
            candidates = list(self._patterns)

        for pat in candidates:
            score = keyword_overlap_score(q_kw, pat.keywords)
            if score < self._match_threshold:
                continue
            if best is None or score > best.score or (
                score == best.score and pat.frequency > best.frequency
            ):
                best = PatternMatch(
                    question=pat.question,
                    sql=pat.sql,
                    score=round(score, 4),
                    frequency=pat.frequency,
                    expected_row_count=pat.expected_row_count,
                    source=pat.source,
                    template_id=pat.template_id,
                    name=pat.name,
                )
        return best

    def promote_to_template(
        self,
        db: Any,
        *,
        question: str,
        sql: str,
        row_count: int | None,
        name: str,
    ) -> LearnedTemplate:
        """Upsert a learned template for a successful question/SQL pair."""
        trigger = normalize_question(question)
        existing = db.scalar(
            select(LearnedTemplate).where(LearnedTemplate.trigger_question == trigger)
        )
        if existing:
            existing.name = name[:200]
            existing.sql_template = (sql or "")[:8000]
            existing.expected_row_count = row_count
            existing.promoted_at = datetime.utcnow()
            row = existing
        else:
            row = LearnedTemplate(
                name=name[:200],
                trigger_question=trigger,
                sql_template=(sql or "")[:8000],
                expected_row_count=row_count,
            )
            db.add(row)
        db.commit()
        db.refresh(row)
        self.refresh(db)
        return row

    def record_template_use(self, db: Any, template_id: int) -> None:
        row = db.get(LearnedTemplate, template_id)
        if not row:
            return
        row.use_count = (row.use_count or 0) + 1
        row.last_used_at = datetime.utcnow()
        db.commit()

    # -- internals ----------------------------------------------------------

    def _maybe_refresh(self) -> None:
        if self._loaded_at is None:
            self.refresh()
            return
        if datetime.utcnow() - self._loaded_at >= self._refresh_interval:
            self.refresh()

    def _load_patterns(self, db: Any) -> list[_LoadedPattern]:
        patterns: list[_LoadedPattern] = []

        # Promoted templates take priority (source=template).
        for tpl in db.scalars(select(LearnedTemplate).order_by(LearnedTemplate.use_count.desc())):
            if not (tpl.trigger_question and tpl.sql_template):
                continue
            patterns.append(
                _LoadedPattern(
                    question=tpl.trigger_question,
                    sql=tpl.sql_template,
                    keywords=extract_keywords(tpl.trigger_question),
                    frequency=max(tpl.use_count or 0, 1),
                    expected_row_count=tpl.expected_row_count,
                    source="template",
                    template_id=tpl.id,
                    name=tpl.name,
                )
            )

        # Successful verification logs grouped by question + sql.
        log_rows = db.execute(
            select(
                SqlVerificationLog.question,
                SqlVerificationLog.sql,
                func.count().label("freq"),
                func.max(SqlVerificationLog.result_row_count).label("row_count"),
            )
            .where(SqlVerificationLog.passed.is_(True))
            .where(SqlVerificationLog.question != "")
            .where(SqlVerificationLog.sql != "")
            .where(
                (SqlVerificationLog.result_row_count.is_(None))
                | (SqlVerificationLog.result_row_count > 0)
            )
            .group_by(SqlVerificationLog.question, SqlVerificationLog.sql)
            .order_by(func.count().desc())
            .limit(500)
        ).all()

        template_questions = {normalize_question(p.question) for p in patterns}
        for row in log_rows:
            question = (row.question or "").strip()
            sql = (row.sql or "").strip()
            if not question or not sql:
                continue
            norm_q = normalize_question(question)
            if norm_q in template_questions:
                continue
            patterns.append(
                _LoadedPattern(
                    question=question,
                    sql=sql,
                    keywords=extract_keywords(question),
                    frequency=int(row.freq or 1),
                    expected_row_count=int(row.row_count) if row.row_count else None,
                    source="log",
                )
            )
        return patterns


_default_agent: PatternMinerAgent | None = None
_refresh_thread: threading.Thread | None = None


def get_pattern_miner() -> PatternMinerAgent:
    global _default_agent
    if _default_agent is None:
        _default_agent = PatternMinerAgent()
    return _default_agent


def start_pattern_miner_refresh_loop() -> None:
    """Background refresh every N minutes (called once on app startup)."""
    global _refresh_thread
    if _refresh_thread is not None and _refresh_thread.is_alive():
        return

    def _loop() -> None:
        miner = get_pattern_miner()
        while True:
            try:
                count = miner.refresh()
                print(f"[pattern-miner] loaded {count} patterns")
            except Exception as exc:
                print(f"[pattern-miner] refresh failed: {exc}")
            interval = float(getattr(config, "PATTERN_MINER_REFRESH_MINUTES", 30))
            threading.Event().wait(interval * 60)

    _refresh_thread = threading.Thread(target=_loop, daemon=True, name="pattern-miner")
    _refresh_thread.start()
