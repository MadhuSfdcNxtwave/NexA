import { useEffect, useRef, useState } from "react";
import { useNavigate, useLocation, useSearchParams, Link } from "react-router-dom";
import { api } from "../api.js";
import { getToken, getUser, setSession } from "../auth.js";
import UsageMeta from "./UsageMeta.jsx";
import SendButton from "./SendButton.jsx";
import ThreadResultsPanel from "./ThreadResultsPanel.jsx";
import ThreadChatSkeleton from "./ThreadChatSkeleton.jsx";
import ClarificationPanel from "./ClarificationDialog.jsx";
import AskProgress from "./AskProgress.jsx";
import { IconPlus } from "./Icons.jsx";
import {
  startAskJob,
  updateAskJobProgress,
  finishAskJob,
  setAskJobError,
  getAskJob,
  matchesAskJob,
  subscribeAskJob,
} from "../askStore.js";
import { normalizeAskResult, memoryToTurn, mergeTurns } from "../askResult.js";
import TableMentionInput from "./TableMentionInput.jsx";
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
            onClick={(e) => {
              e.stopPropagation();
              onPick(s);
            }}
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function AskSection({
  id,
  project,
  onProjectChange,
  projectName,
  initialQuestion,
  standaloneThreadId,
  threadTitle,
}) {
  const isStandalone = standaloneThreadId != null;
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();

  const scopeRef = useRef(isStandalone ? standaloneThreadId : id);
  scopeRef.current = isStandalone ? standaloneThreadId : id;

  const activeIdRef = useRef(id);
  activeIdRef.current = id;

  const threadParam = isStandalone ? null : searchParams.get("thread");
  const threadId = isStandalone
    ? standaloneThreadId
    : threadParam
      ? Number(threadParam)
      : null;
  const threadIdRef = useRef(threadId);
  threadIdRef.current = threadId;

  const [threads, setThreads] = useState([]);
  const [turns, setTurns] = useState([]);
  const [q, setQ] = useState("");
  const [workspaceTables, setWorkspaceTables] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [forceFresh, setForceFresh] = useState(true);
  const [askProgress, setAskProgress] = useState(null);
  const [clarification, setClarification] = useState(null);
  const [memLoading, setMemLoading] = useState(true);
  const [activeTurnIdx, setActiveTurnIdx] = useState(-1);
  const [pendingQ, setPendingQ] = useState("");
  const [mobilePane, setMobilePane] = useState("results");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [threadOverview, setThreadOverview] = useState("");
  const chatEndRef = useRef(null);
  const pendingQuestionRef = useRef(initialQuestion?.trim() || null);

  const displayName = isStandalone ? (threadTitle || "your data") : projectName;

  const reloadThreads = async () => {
    if (isStandalone) return [];
    try {
      const list = await api.listProjectThreads(id);
      if (activeIdRef.current !== id) return list;
      setThreads(list);
      return list;
    } catch (_) {
      return [];
    }
  };

  const switchThread = (tid) => {
    if (isStandalone) return;
    setSearchParams(tid ? { thread: String(tid) } : {}, { replace: false });
  };

  const newThread = async () => {
    if (isStandalone) return;
    try {
      const t = await api.createThread(id);
      await reloadThreads();
      switchThread(t.id);
    } catch (e) {
      setError(e.message);
    }
  };

  const fetchMemory = () => {
    const tid = isStandalone ? standaloneThreadId : threadId;
    if (tid) {
      return api.getThreadMemory(tid).then((mem) => {
        return api.getThread(tid)
          .then((t) => {
            setThreadOverview(t.overview_kb || "");
            return mem;
          })
          .catch(() => mem);
      });
    }
    setThreadOverview("");
    return api.getMemory(id, threadId);
  };

  const scopeMatches = () =>
    scopeRef.current === (isStandalone ? standaloneThreadId : id);

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
      if (event.type === "sql_verified" || event.type === "sql_ready") {
        next.sqlMessage = event.message || "SQL ready";
        next.verifiedSql = event.sql;
        next.phase = "validate";
      }
      if (event.type === "insight") {
        next.insightPreview = event.data;
        next.phase = "analyze";
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
        setActiveTurnIdx(idx);
        return next;
      }
      setActiveTurnIdx(prev.length);
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

  const syncTurnsFromMemory = async () => {
    try {
      const mem = await fetchMemory();
      if (!scopeMatches()) return;
      const loaded = mem.map(memoryToTurn);
      setTurns((prev) => {
        const merged = mergeTurns(prev, loaded);
        setActiveTurnIdx(merged.length ? merged.length - 1 : -1);
        return merged;
      });
    } catch (_) {
      /* appendTurn may have already updated local state */
    }
  };

  const runAsk = async (question, clarOpts = null) => {
    const text = question.trim();
    if (!text || !scopeMatches()) return;

    setLoading(true);
    setError("");
    setPendingQ(text);
    setMobilePane("agent");
    startAskJob(
      isStandalone
        ? { standalone: true, threadId: standaloneThreadId, projectId: null, question: text }
        : { projectId: id, threadId: threadIdRef.current, question: text },
    );
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
      const streamOpts = {
        forceFresh,
        clarificationChoice: clarOpts?.clarificationChoice,
        clarificationText: clarOpts?.clarificationText,
        refinedQuestion: clarOpts?.refinedQuestion,
        pinnedTableIds: extractPinnedTableIds(text, workspaceTables),
      };

      let res = isStandalone
        ? await api.askThreadStream(standaloneThreadId, text, onEvent, streamOpts)
        : await api.askStream(id, text, onEvent, {
            ...streamOpts,
            threadId: threadIdRef.current,
          });

      if (!scopeMatches()) return;

      if (clarPayload) {
        setClarification({
          ...clarPayload,
          standalone: isStandalone,
          projectId: isStandalone ? null : id,
          originalQuestion: text,
        });
        setLoading(false);
        setAskProgress(null);
        setPendingQ("");
        return;
      }

      if (approvalPayload) {
        res = isStandalone
          ? await api.askThreadConfirmStream(
              standaloneThreadId,
              approvalPayload.question,
              approvalPayload.sql,
              applyProgressEvent,
            )
          : await api.askConfirmStream(
              id,
              approvalPayload.question,
              approvalPayload.sql,
              applyProgressEvent,
              { threadId: threadIdRef.current },
            );
      }

      if (res) {
        appendTurn(text, res);
        setMobilePane("results");
        if (isStandalone) {
          api.getThread(standaloneThreadId)
            .then((t) => setThreadOverview(t.overview_kb || ""))
            .catch(() => {});
        } else {
          if (res.thread_id && res.thread_id !== threadIdRef.current) {
            setSearchParams({ thread: String(res.thread_id) }, { replace: true });
          }
          const tid = res.thread_id || threadIdRef.current;
          if (tid) {
            api.getThread(tid)
              .then((t) => setThreadOverview(t.overview_kb || ""))
              .catch(() => {});
          }
          reloadThreads();
        }
        // Trust the stream result; memory reload can race before Postgres commit.
      } else if (!clarPayload && !approvalPayload) {
        setError("Ask finished without a result — try again with Fresh checked.");
      }
    } catch (e) {
      if (scopeMatches()) {
        setError(e.message);
        setPendingQ("");
        setAskJobError(e.message);
      }
    } finally {
      if (scopeMatches() && !clarPayload) {
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
    await runAsk(pending.originalQuestion, {
      clarificationChoice: choiceId,
      clarificationText: choiceText,
      refinedQuestion,
    });
  };

  const updateSetting = async (patch) => {
    if (isStandalone) return;
    try {
      const updated = await api.updateProjectSettings(id, patch);
      onProjectChange?.(updated);
    } catch (e) {
      setError(e.message);
    }
  };

  const pinToDashboard = async (turn) => {
    if (isStandalone) return;
    try {
      await api.addDashboardItem(id, {
        question: turn.question,
        sql: turn.sql,
        analysis: turn.analysis,
        chart_spec: turn.chart_spec || {},
      });
      setPinMsg("Added to this project's Dashboard tab");
      setTimeout(() => setPinMsg(""), 2000);
    } catch (e) {
      setError(e.message);
    }
  };

  const rerunSql = async (question, sqlText) => {
    if (!question?.trim() || !sqlText?.trim() || loading) return;
    setLoading(true);
    setError("");
    setAskProgress({ status: "Running edited SQL…", phase: "run" });
    try {
      const res = isStandalone
        ? await api.askThreadConfirmStream(
            standaloneThreadId,
            question.trim(),
            sqlText.trim(),
            applyProgressEvent,
          )
        : await api.askConfirmStream(
            id,
            question.trim(),
            sqlText.trim(),
            applyProgressEvent,
            { threadId: threadIdRef.current },
          );
      if (res) appendTurn(question.trim(), res);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setAskProgress(null);
    }
  };

  const clearThread = async () => {
    if (!window.confirm("Clear this thread and start fresh?")) return;
    try {
      if (isStandalone) {
        await api.clearThreadMemory(standaloneThreadId);
      } else {
        await api.clearMemory(id, threadId);
        reloadThreads();
      }
      setTurns([]);
      setActiveTurnIdx(-1);
      setError("");
      setPendingQ("");
    } catch (e) {
      setError(e.message);
    }
  };

  const removeThread = async (tid) => {
    if (isStandalone) return;
    if (!window.confirm("Delete this thread and its history?")) return;
    try {
      await api.deleteThread(id, tid);
      const list = await reloadThreads();
      if (tid === threadIdRef.current) {
        switchThread(list.length ? list[0].id : null);
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteStandalone = async () => {
    if (!isStandalone) return;
    if (!window.confirm("Delete this thread and its history?")) return;
    try {
      await api.deleteStandaloneThread(standaloneThreadId);
      navigate("/threads");
    } catch (e) {
      setError(e.message);
    }
  };

  const askSuggestion = (suggestion) => runAsk(suggestion);

  const ask = async () => {
    const question = q.trim();
    if (!question || loading) return;
    setQ("");
    await runAsk(question);
  };

  useEffect(() => {
    if (isStandalone) return;
    let cancelled = false;
    reloadThreads().then((list) => {
      if (cancelled || activeIdRef.current !== id) return;
      const param = new URLSearchParams(window.location.search).get("thread");
      if (!param && list.length) {
        setSearchParams({ thread: String(list[0].id) }, { replace: true });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [id, isStandalone, setSearchParams]);

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
    const jobThreadId = isStandalone ? standaloneThreadId : threadId;

    const restoreFromJob = () => {
      const job = getAskJob();
      if (matchesAskJob(isStandalone ? null : id, jobThreadId, isStandalone) && job?.loading) {
        setPendingQ(job.question);
        setAskProgress(job.progress);
        setLoading(true);
        setError(job.error || "");
        return true;
      }
      return false;
    };

    if (!restoreFromJob()) {
      setLoading(false);
      setError("");
      setPendingQ("");
    }
    setMemLoading(true);

    fetchMemory()
      .then((mem) => {
        if (cancelled || !scopeMatches()) return;
        if (getAskJob()?.loading) return;

        const loaded = mem.map(memoryToTurn);
        setTurns((prev) => {
          const merged = mergeTurns(prev, loaded);
          setActiveTurnIdx(merged.length ? merged.length - 1 : -1);
          return merged;
        });
        setMemLoading(false);

        const pending = pendingQuestionRef.current;
        if (pending) {
          pendingQuestionRef.current = null;
          const exists = mem.some((m) => m.question.trim() === pending);
          if (!exists && !getAskJob()?.loading) runAsk(pending);
        }
      })
      .catch(() => {
        if (!cancelled) setMemLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isStandalone ? standaloneThreadId : id, isStandalone ? undefined : threadId]);

  useEffect(() => {
    const jobThreadId = isStandalone ? standaloneThreadId : threadId;
    return subscribeAskJob(async (job) => {
      if (!matchesAskJob(isStandalone ? null : id, jobThreadId, isStandalone)) return;
      if (job?.loading) {
        setPendingQ(job.question);
        setAskProgress(job.progress);
        setLoading(true);
        if (job.error) setError(job.error);
      } else if (!job) {
        setLoading(false);
        setAskProgress(null);
        setPendingQ("");
        try {
          const mem = await fetchMemory();
          if (!scopeMatches()) return;
          setTurns((prev) => {
            const loaded = mem.map(memoryToTurn);
            const merged = mergeTurns(prev, loaded);
            setActiveTurnIdx(merged.length ? merged.length - 1 : -1);
            return merged;
          });
        } catch (_) {
          /* keep local turns */
        }
      }
    });
  }, [isStandalone ? standaloneThreadId : id, isStandalone ? undefined : threadId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, loading, pendingQ]);

  const activeTurn = activeTurnIdx >= 0 && activeTurnIdx < turns.length ? turns[activeTurnIdx] : null;
  const resultsLoading = loading && !activeTurn;

  return (
    <div className="thread-page thread-page-split">
      <div className="thread-toolbar">
        {!isStandalone && (
          <div className="thread-tabs" role="tablist" aria-label="Threads">
            {threads.map((t) => (
              <div
                key={t.id}
                role="tab"
                aria-selected={t.id === threadId}
                tabIndex={0}
                className={`thread-tab ${t.id === threadId ? "active" : ""}`}
                onClick={() => switchThread(t.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    switchThread(t.id);
                  }
                }}
                title={t.title}
              >
                <span className="thread-tab-dot" aria-hidden />
                <span className="thread-tab-title">
                  {t.title === "New thread" ? "New thread" : t.title}
                </span>
                {t.turn_count > 0 && <span className="thread-tab-count">{t.turn_count}</span>}
                {threads.length > 1 && (
                  <button
                    type="button"
                    className="thread-tab-close"
                    aria-label={`Delete thread ${t.title}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      removeThread(t.id);
                    }}
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
            <button type="button" className="thread-tab new" onClick={newThread} disabled={loading}>
              <IconPlus /> New thread
            </button>
          </div>
        )}

        {!isStandalone && (
          <button
            type="button"
            className="secondary small"
            onClick={() => setSettingsOpen((v) => !v)}
          >
            Settings {settingsOpen ? "▾" : "▸"}
          </button>
        )}

        <button type="button" className="secondary small" onClick={clearThread} disabled={loading}>
          Clear thread
        </button>

        {isStandalone && (
          <button type="button" className="secondary small" onClick={deleteStandalone} disabled={loading}>
            Delete thread
          </button>
        )}

        {!isStandalone && settingsOpen && (
          <div className="project-settings-panel thread-settings-panel">
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={!!project?.reuse_cached_results}
                onChange={(e) => updateSetting({ reuse_cached_results: e.target.checked })}
              />
              Reuse prior query results for follow-ups only (saves credits)
            </label>
            <p className="muted project-settings-hint">
              When off, every new question runs a fresh BigQuery query (~MB scanned shown per answer).
            </p>
          </div>
        )}
      </div>

      <div className="thread-mobile-tabs" role="tablist" aria-label="Panel">
        <button
          type="button"
          role="tab"
          aria-selected={mobilePane === "results"}
          className={`thread-mobile-tab ${mobilePane === "results" ? "active" : ""}`}
          onClick={() => setMobilePane("results")}
        >
          Results
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mobilePane === "agent"}
          className={`thread-mobile-tab ${mobilePane === "agent" ? "active" : ""}`}
          onClick={() => setMobilePane("agent")}
        >
          Agent
          {(loading || pendingQ) && <span className="thread-mobile-tab-dot" aria-hidden />}
        </button>
      </div>

      <div className="thread-split">
        <aside
          className={`thread-results-pane ${mobilePane === "results" ? "mobile-visible" : "mobile-hidden"}`}
          aria-label="Results"
        >
          <header className="thread-pane-header results-pane-header">
            <span className="thread-pane-title">Analysis</span>
            <span className="thread-pane-meta">Charts · tables · SQL</span>
          </header>
          <ThreadResultsPanel
            turns={turns}
            activeTurnIdx={activeTurnIdx}
            onSelectTurn={(i) => {
              setActiveTurnIdx(i);
            }}
            turn={activeTurn}
            loading={resultsLoading || (loading && !!activeTurn)}
            askProgress={askProgress}
            threadOverview={threadOverview}
            onPin={isStandalone ? undefined : pinToDashboard}
            pinDisabled={loading}
            onRerunSql={rerunSql}
            rerunDisabled={loading}
          />
        </aside>

        <section
          className={`thread-agent-pane ${mobilePane === "agent" ? "mobile-visible" : "mobile-hidden"}`}
          aria-label="Agent"
        >
          <header className="thread-pane-header agent-pane-header">
            <span className="thread-pane-title">NexA Agent</span>
            <span className="thread-pane-meta">
              {turns.length} turn{turns.length === 1 ? "" : "s"}
            </span>
          </header>
          <div className="thread-messages">
            {memLoading && <ThreadChatSkeleton />}

            {!memLoading && turns.length === 0 && !loading && !pendingQ && (
              <div className="thread-empty">
                <div className="thread-empty-icon" aria-hidden>✦</div>
                <h2 className="hero-title small">Ask anything</h2>
                <p className="muted">
                  Natural language → SQL → answers for <strong>{displayName}</strong>.
                </p>
                <div className="thread-empty-hints">
                  <button
                    type="button"
                    className="thread-suggestion-chip"
                    onClick={() => setQ("How many users have learning portal active?")}
                  >
                    Portal active users
                  </button>
                  <button
                    type="button"
                    className="thread-suggestion-chip"
                    onClick={() => setQ("How many users attended live classes yesterday?")}
                  >
                    Live class attendance
                  </button>
                  <button
                    type="button"
                    className="thread-suggestion-chip"
                    onClick={() => setQ("How many distinct users applied to at least one job?")}
                  >
                    Job applications
                  </button>
                </div>
              </div>
            )}

            {!memLoading && turns.map((t, i) => (
              <div
                key={`${i}-${t.question.slice(0, 24)}`}
                className={`thread-turn-card ${i === activeTurnIdx ? "active" : ""}`}
              >
                <button
                  type="button"
                  className="thread-turn-select"
                  onClick={() => {
                    setActiveTurnIdx(i);
                    setMobilePane("results");
                  }}
                >
                  <div className="thread-q">
                    <div className="thread-avatar user">You</div>
                    <div className="thread-bubble q">{t.question}</div>
                  </div>
                  <div className="thread-a compact">
                    <div className="thread-avatar ai">AI</div>
                    <div className="thread-bubble a">
                      <UsageMeta
                        bytes_estimate={t.bytes_estimate}
                        credits_used={t.credits_used}
                        from_cache={t.from_cache}
                      />
                      <p className="thread-preview">{t.analysis}</p>
                    </div>
                  </div>
                </button>
                {i === activeTurnIdx && (
                  <FollowUpSuggestions
                    suggestions={t.suggestions}
                    onPick={askSuggestion}
                    disabled={loading}
                  />
                )}
              </div>
            ))}

            {pendingQ && askProgress && (
              <div className="thread-turn-card pending">
                <div className="thread-q">
                  <div className="thread-avatar user">You</div>
                  <div className="thread-bubble q">{pendingQ}</div>
                </div>
                <div className="thread-a compact hex-work-inline">
                  <div className="thread-avatar ai pulse">AI</div>
                  <div className="thread-bubble a">
                    <AskProgress progress={askProgress} active={loading} compact />
                  </div>
                </div>
              </div>
            )}

            {pendingQ && !askProgress && (
              <div className="thread-turn-card pending">
                <div className="thread-q">
                  <div className="thread-avatar user">You</div>
                  <div className="thread-bubble q">{pendingQ}</div>
                </div>
                <div className="thread-a compact">
                  <div className="thread-avatar ai pulse">AI</div>
                  <div className="thread-bubble a thinking">
                    <span className="thinking-label">Analyzing</span>
                    <span className="thinking-dots" aria-hidden>…</span>
                  </div>
                </div>
              </div>
            )}

            {error && (
              <div className="error">
                {error}
                {!isStandalone && error.toLowerCase().includes("no tables") && (
                  <>
                    {" "}
                    <Link to={`/projects/${id}/data`}>Open Data tab →</Link>
                  </>
                )}
              </div>
            )}

            {!isStandalone && pinMsg && (
              <div className="toast-msg pin-toast">
                {pinMsg}
                {" "}
                <button
                  type="button"
                  className="link-btn"
                  onClick={() => navigate(`/projects/${id}/dashboard`)}
                >
                  Open Dashboard →
                </button>
              </div>
            )}

            <div ref={chatEndRef} />
          </div>

          <div className="thread-composer">
            <div className="hero-prompt compact agent-composer">
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
              <div className="hero-prompt-footer">
                <div className="hero-prompt-left">
                  <label className="toggle-label fresh-query-toggle" title="Skip cached answers">
                    <input
                      type="checkbox"
                      checked={forceFresh}
                      onChange={(e) => setForceFresh(e.target.checked)}
                    />
                    Fresh BQ
                  </label>
                </div>
                <div className="hero-prompt-right">
                  <SendButton compact onClick={() => ask()} disabled={loading || !q.trim()} />
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}
