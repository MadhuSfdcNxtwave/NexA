import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { getAskJob, subscribeAskJob, stopAskJob } from "../askStore.js";
import { getFriendlyStageLabel } from "./AskProgress.jsx";
import { IconStop } from "./Icons.jsx";

/** Shows when a question is processing and the user navigated away from Thread. */
export default function AskJobBanner() {
  const [job, setJob] = useState(() => getAskJob());
  const { pathname } = useLocation();

  useEffect(() => subscribeAskJob(setJob), []);

  if (!job?.loading) return null;

  const threadPath = job.standalone || !job.projectId
    ? `/threads/${job.threadId}`
    : `/projects/${job.projectId}${job.threadId ? `?thread=${job.threadId}` : ""}`;
  const onThreadPage =
    job.standalone || !job.projectId
      ? pathname === `/threads/${job.threadId}`
      : pathname === `/projects/${job.projectId}` ||
        pathname === `/projects/${job.projectId}/`;

  if (onThreadPage) return null;

  const status = getFriendlyStageLabel(job.progress);
  const tables = (job.progress?.viewedTables || [])
    .map((t) => t.short_name || t.full_table_id?.split(".").pop())
    .filter(Boolean);

  return (
    <div className="ask-job-banner" role="status">
      <span className="ask-job-banner-dot" aria-hidden />
      <span className="ask-job-banner-text">
        <strong>In progress:</strong> {job.question.slice(0, 80)}
        {job.question.length > 80 ? "…" : ""}
        <span className="muted"> — {status}</span>
        {tables.length > 0 && (
          <span className="ask-job-banner-tables"> · {tables.join(", ")}</span>
        )}
      </span>
      <button
        type="button"
        className="ask-job-banner-stop"
        onClick={() => stopAskJob()}
        title="Stop"
      >
        <IconStop /> Stop
      </button>
      <Link to={threadPath} className="ask-job-banner-link">
        View thread →
      </Link>
    </div>
  );
}
