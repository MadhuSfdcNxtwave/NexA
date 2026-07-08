/** Full-screen or inline loading indicator. */
export default function LoadingScreen({ message = "Loading…", fullScreen = false, overlay = false }) {
  const cls = [
    "loading-screen",
    fullScreen && "loading-screen-full",
    overlay && "loading-screen-overlay",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} role="status" aria-live="polite">
      <div className="loading-screen-card">
        <div className="loading-ring" aria-hidden />
        <p className="loading-screen-msg">{message}</p>
      </div>
    </div>
  );
}
