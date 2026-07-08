/** Global in-flight ask state — survives route changes within the app. */

const listeners = new Set();

let job = null;

function emit() {
  listeners.forEach((fn) => {
    try {
      fn(job);
    } catch (_) {
      /* ignore subscriber errors */
    }
  });
}

export function getAskJob() {
  return job;
}

export function subscribeAskJob(fn) {
  listeners.add(fn);
  fn(job);
  return () => listeners.delete(fn);
}

export function startAskJob({ projectId, threadId, question, standalone = false }) {
  job = {
    projectId: projectId != null ? Number(projectId) : null,
    threadId: threadId != null ? Number(threadId) : null,
    standalone: !!standalone,
    question: (question || "").trim(),
    progress: {
      status: "Starting…",
      keywords: [],
      tables: [],
      viewedTables: [],
      phase: "plan",
    },
    loading: true,
    error: null,
  };
  emit();
}

export function updateAskJobProgress(progress) {
  if (!job) return;
  job = { ...job, progress: { ...job.progress, ...progress } };
  emit();
}

export function setAskJobError(message) {
  if (!job) return;
  job = { ...job, error: message || null, loading: false };
  emit();
}

export function updateAskJobThread(threadId) {
  if (!job) return;
  job = { ...job, threadId: threadId != null ? Number(threadId) : null };
  emit();
}

export function finishAskJob() {
  job = null;
  emit();
}

/** True when this project/thread has an in-flight ask. */
export function matchesAskJob(projectId, threadId, standalone = false) {
  if (!job?.loading) return false;
  if (job.standalone || standalone) {
    return job.threadId === Number(threadId);
  }
  if (job.projectId !== Number(projectId)) return false;
  const a = job.threadId;
  const b = threadId != null ? Number(threadId) : null;
  if (a == null || b == null) return true;
  return a === b;
}
