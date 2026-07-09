import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { getToken, getUser, setSession } from "../auth.js";
import UsageMeta from "./UsageMeta.jsx";
import Chart from "./Chart.jsx";
import AskProgress from "./AskProgress.jsx";
import SendButton from "./SendButton.jsx";
import ClarificationPanel from "./ClarificationDialog.jsx";
import { IconPlus } from "./Icons.jsx";
import {
  startAskJob,
  updateAskJobProgress,
  updateAskJobThread,
  finishAskJob,
  setAskJobError,
  getAskJob,
  matchesAskJob,
  subscribeAskJob,
} from "../askStore.js";
import SqlNotebookCells from "./SqlNotebookCells.jsx";
import { extractPinnedTableIds } from "../tableMentions.js";

function FollowUpSuggestions({ suggestions, onPick, disabled }) {
  if (!suggestions?.length) return null;
  return (
    <div className="thread-suggestions compact">
      <div className="thread-suggestion-chips">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            className="thread-suggestion-chip"
            disabled={disabled}
            onClick={() => onPick(s)}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Compact agent sidebar — scoped to one thread; full experience on Thread tab. */
export default function ThreadAgentPanel({
  projectId,
  projectName,
  threadId: threadIdProp = null,
  compact = true,
}) {
  const activeIdRef = useRef(projectId);
  activeIdRef.current = projectId;
  const threadIdRef = useRef(threadIdProp);
  threadIdRef.current = threadIdProp;

  const [turns, setTurns] = useState([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [forceFresh, setForceFresh] = useState(false);
  const [askProgress, setAskProgress] = useState(null);
  const [clarification, setClarification] = useState(null);
  const [pendingQ, setPendingQ] = useState("");
  const [workspaceTables, setWorkspaceTables] = useState([]);
  const chatEndRef = useRef(null);

  const threadHref = `/projects/${projectId}${threadIdProp ? `?thread=${threadIdProp}` : ""}`;

  const applyProgressEvent = (event) => {
    setAskProgress((prev) => {
      const next = { ...(prev || { keywords: [], tables: [], viewedTables: [] }) };
      if (event.type === "status") {
        next.status = event.message;
        next.phase = "plan";
      }
      if (event.type === "search_tables") {
        next.status = event.message || next.status;
        next.keywords = event.keywords || [];
        next.tables = event.tables || [];
        next.phase = "plan";
      }
      if (event.type === "reasoning") next.reasoning = event.text;
      if (event.type === "view_tables") {
        next.viewedTables = event.tables || [];
        next.phase = "plan";
      }
      if (event.type === "match_columns") {
        next.status = event.message || next.status;
        next.matchedColumns = event.tables || [];
        next.phase = "columns";
      }
      if (event.type === "join_hints") {
        next.status = event.message || next.status;
        next.joinRelations = event.relations || [];
        next.phase = "joins";
      }
      if (event.type === "generating_sql") {
        next.sqlMessage = event.message;
        next.phase = "sql";
        next.awaitingApproval = false;
      }
      if (event.type === "validating_sql") {
        next.sqlMessage = event.message;
        next.validationLabel = event.label || "";
        next.phase = "validate";
      }
      if (event.type === "sql_verified") {
        next.sqlMessage = event.message;
        next.verifiedSql = event.sql;
        next.phase = "validate";
      }
      if (event.type === "running_query") next.phase = "query";
      if (event.type === "analyzing") next.phase = "analyze";
      if (event.type === "chain_plan") {
        next.chainSteps = event.steps || [];
        next.status = event.message || next.status;
        next.phase = "chain";
      }
      if (event.type === "chain_step") {
        next.chainStep = event.step;
        next.chainTotal = event.total;
        next.chainLabel = event.label;
        next.status = event.message || next.status;
        next.phase = "chain";
      }
      if (event.type === "cache_hit") {
        next.status = event.message;
        next.phase = "cache";
        next.fromCache = true;
      }
      updateAskJobProgress(next);
      return next;
    });
  };

  const appendTurn = (text, res) => {
    const normalized = normalizeAskResult(res);
    if (!normalized) return;
    const turn = {
      ...normalized,
      question: normalized.question || text,
    };
    setTurns((prev) => {
      const key = text.trim();
      const idx = prev.findIndex((t) => t.question.trim() === key);
      if (idx >= 0) {
        const next = [...prev];
        next[idx] = turn;
        return next;
      }
      return [...prev, turn];
    });
    setPendingQ("");
    if (typeof res.credits_remaining === "number") {
      const u = getUser();
      const token = getToken();
      if (u && token) setSession(token, { ...u, credits_balance: res.credits_remaining });
      window.dispatchEvent(new Event("nexa-credits-updated"));
    }
  };

  const runAsk = async (question, pid = projectId, clarOpts = null) => {
    const text = question.trim();
    if (!text || activeIdRef.current !== pid) return;

    setLoading(true);
    setError("");
    setPendingQ(text);
    startAskJob({ projectId: pid, threadId: threadIdRef.current, question: text });
    setAskProgress({ status: "Starting…", keywords: [], tables: [], viewedTables: [], phase: "plan" });
    let approvalPayload = null;
    let clarPayload = null;
    const onEvent = (event) => {
      if (event.type === "awaiting_clarification") {
        clarPayload = event;
        return;
      }
      if (event.type === "awaiting_approval") {
        approvalPayload = { question: event.question, sql: event.sql };
        applyProgressEvent({
          type: "sql_verified",
          message: "SQL passed validation — running on BigQuery…",
          sql: event.sql,
        });
        return;
      }
      applyProgressEvent(event);
    };
    try {
      let res = await api.askStream(pid, text, onEvent, {
        forceFresh,
        threadId: threadIdRef.current,
        clarificationChoice: clarOpts?.clarificationChoice,
        clarificationText: clarOpts?.clarificationText,
        refinedQuestion: clarOpts?.refinedQuestion,
        pinnedTableIds: extractPinnedTableIds(text, workspaceTables),
      });
      if (activeIdRef.current !== pid) return;
      if (clarPayload) {
        setClarification({ ...clarPayload, projectId: pid, originalQuestion: text });
        setLoading(false);
        setAskProgress(null);
        setPendingQ("");
        return;
      }
      if (approvalPayload) {
        res = await api.askConfirmStream(
          pid,
          approvalPayload.question,
          approvalPayload.sql,
          applyProgressEvent,
          { threadId: threadIdRef.current },
        );
      }
      if (res) {
        const normalized = normalizeAskResult(res);
        if (normalized) {
          appendTurn(text, normalized);
          if (normalized.thread_id) updateAskJobThread(normalized.thread_id);
        }
      }
    } catch (e) {
      if (activeIdRef.current === pid) {
        setError(e.message);
        setPendingQ("");
        setAskJobError(e.message);
      }
    } finally {
      if (activeIdRef.current === pid && !clarPayload) {
        setLoading(false);
        setAskProgress(null);
        finishAskJob();
      }
    }
  };

  const handleClarification = async (choiceId, choiceText, refinedQuestion) => {
    const pending = clarification;
    if (!pending) return;
    setClarification(null);
    setLoading(true);
    await runAsk(pending.originalQuestion, pending.projectId, {
      clarificationChoice: choiceId,
      clarificationText: choiceText,
      refinedQuestion,
    });
  };

  useEffect(() => {
    let cancelled = false;
    api.listWorkspaceTables()
      .then((tables) => {
        if (!cancelled) setWorkspaceTables(tables || []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const job = getAskJob();
    if (matchesAskJob(projectId, threadIdProp) && job?.loading) {
      setPendingQ(job.question);
      setAskProgress(job.progress);
      setLoading(true);
    } else {
      setLoading(false);
      setPendingQ("");
    }
    setError("");

    api.getMemory(projectId, threadIdProp).then((mem) => {
      if (cancelled || activeIdRef.current !== projectId) return;
      if (getAskJob()?.loading && getAskJob()?.projectId === Number(projectId)) return;
      setTurns(mem.map(memoryToTurn));
    });

    return () => {
      cancelled = true;
    };
  }, [projectId, threadIdProp]);

  useEffect(() => {
    return subscribeAskJob((job) => {
      if (!matchesAskJob(projectId, threadIdProp)) return;
      if (job?.loading) {
        setPendingQ(job.question);
        setAskProgress(job.progress);
        setLoading(true);
      } else if (!job) {
        setLoading(false);
        setAskProgress(null);
      }
    });
  }, [projectId, threadIdProp]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading, pendingQ]);

  const ask = async () => {
    const question = q.trim();
    if (!question || loading) return;
    setQ("");
    await runAsk(question);
  };

  return (
    <aside className={`notebook-agent-pane${compact ? " compact" : ""}`}>
      <header className="notebook-agent-head">
        <span className="notebook-agent-title">Agent</span>
        <Link to={threadHref} className="muted small notebook-agent-thread-link">
          Open Thread tab →
        </Link>
      </header>

      <div className="notebook-agent-messages">
        {turns.length === 0 && !loading && !pendingQ && (
          <div className="notebook-agent-empty">
            <p className="muted small">
              Ask data questions for <strong>{projectName}</strong>. Answers stay in your
              thread — not copied into notebook cells.
            </p>
            <Link to={threadHref} className="secondary small">
              Go to Thread for full view
            </Link>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className="notebook-agent-turn">
            <div className="notebook-agent-q">{t.question}</div>
            <div className="notebook-agent-a">
              <UsageMeta
                bytes_estimate={t.bytes_estimate}
                credits_used={t.credits_used}
                from_cache={t.from_cache}
              />
              <p>{t.analysis}</p>
              {t.chart_spec?.chart && t.chart_spec.chart !== "none" && (
                <Chart spec={t.chart_spec} rows={t.rows} vizRows={t.viz_rows} />
              )}
              {t.sql && (
                <details className="sql-details" open={!!t.sql_steps?.length}>
                  <summary>SQL{t.sql_steps?.length ? ` (${t.sql_steps.length} cells)` : ""}</summary>
                  {t.sql_steps?.length ? (
                    <SqlNotebookCells steps={t.sql_steps} combinedSql={t.sql} />
                  ) : (
                    <pre className="code-block">{t.sql}</pre>
                  )}
                </details>
              )}
              <FollowUpSuggestions
                suggestions={t.suggestions}
                onPick={(s) => runAsk(s)}
                disabled={loading}
              />
            </div>
          </div>
        ))}

        {pendingQ && (
          <div className="notebook-agent-turn pending">
            <div className="notebook-agent-q">{pendingQ}</div>
            <div className="notebook-agent-a">
              <AskProgress progress={askProgress} active={loading} compact />
            </div>
          </div>
        )}

        {error && (
          <div className="error small">
            {error}
            {error.toLowerCase().includes("no tables") && (
              <>
                {" "}
                <Link to={`/projects/${projectId}/data`}>Data →</Link>
              </>
            )}
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      <div className="notebook-agent-composer">
        <TableMentionInput
          rows={2}
          placeholder="Ask a data question… (@table to pin a table)"
          value={q}
          onChange={setQ}
          tables={workspaceTables}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask();
            }
          }}
        />
        {clarification && (
          <ClarificationPanel
            prompt={clarification.prompt}
            options={clarification.options}
            allowCustom={clarification.allow_custom !== false}
            confirmMode={clarification.confirm_mode === true}
            originalQuestion={clarification.originalQuestion}
            loading={loading}
            onChoose={handleClarification}
            onCancel={() => {
              setClarification(null);
              setLoading(false);
              setAskProgress(null);
              setPendingQ("");
              finishAskJob();
            }}
          />
        )}
        <div className="notebook-agent-composer-foot">
          <button type="button" className="pill-btn" title="Add"><IconPlus /></button>
          <label className="toggle-label fresh-query-toggle" title="Skip cache">
            <input
              type="checkbox"
              checked={forceFresh}
              onChange={(e) => setForceFresh(e.target.checked)}
            />
            Fresh
          </label>
          <SendButton compact onClick={ask} disabled={loading || !q.trim()} />
        </div>
      </div>
    </aside>
  );
}
