import LoadingScreen from "./LoadingScreen.jsx";

/** Skeleton placeholder while thread history loads. */
export default function ThreadChatSkeleton() {
  return (
    <div className="thread-chat-skeleton" aria-busy="true">
      {[1, 2].map((i) => (
        <div key={i} className="thread-chat-skeleton-turn">
          <div className="skeleton-avatar" />
          <div className="skeleton-bubble" />
        </div>
      ))}
    </div>
  );
}

export function PageBootLoader({ message }) {
  return <LoadingScreen message={message} overlay />;
}
