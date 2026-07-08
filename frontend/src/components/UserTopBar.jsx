import { useEffect, useState } from "react";
import { getUser, isAdmin } from "../auth.js";

/** Top bar: logged-in user + credits used on the latest query. */
export default function UserTopBar() {
  const [user, setUser] = useState(() => getUser());
  const [lastQuery, setLastQuery] = useState(null);

  useEffect(() => {
    const onUser = () => setUser(getUser());
    const onQuery = (e) => setLastQuery(e.detail || null);
    window.addEventListener("nexa-credits-updated", onUser);
    window.addEventListener("nexa-query-credits", onQuery);
    return () => {
      window.removeEventListener("nexa-credits-updated", onUser);
      window.removeEventListener("nexa-query-credits", onQuery);
    };
  }, []);

  if (!user) return null;

  const displayName = (user.name || "").trim() || user.email?.split("@")[0] || "User";
  const admin = isAdmin();
  const creditsUsed =
    lastQuery && typeof lastQuery.credits_used === "number"
      ? lastQuery.credits_used
      : null;

  return (
    <header className="user-topbar">
      <div className="user-topbar-spacer" />
      <div className="user-topbar-meta">
        {creditsUsed != null && (
          <span className="user-topbar-credits" title="Model / scan credits for the last query">
            Last query: {creditsUsed.toFixed(3)} credits
            {lastQuery?.from_cache ? " (cached)" : ""}
          </span>
        )}
        <span className={`user-topbar-badge ${admin ? "admin" : ""}`}>
          {admin ? "Admin" : "User"}
        </span>
        <span className="user-topbar-name" title={user.email}>
          {displayName}
        </span>
      </div>
    </header>
  );
}
