import { useState, useRef, useEffect } from "react";

/** MB scanned + model credits per query with ⋯ detail popover */
export default function UsageMeta({ bytes_estimate, credits_used, credits_remaining, from_cache }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const showCredits = true;

  useEffect(() => {
    if (!open) return;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("click", close);
    return () => document.removeEventListener("click", close);
  }, [open]);

  if (typeof bytes_estimate !== "number" && credits_used == null) return null;

  const mb = from_cache ? 0 : (bytes_estimate || 0) / 1048576;
  const credits = typeof credits_used === "number" ? credits_used : 0;
  const scanLabel = from_cache ? "Cached — 0 MB scanned" : `~${mb.toFixed(1)} MB scanned`;

  return (
    <span className="usage-meta" ref={ref}>
      <span className="meta-pill usage-meta-pill">
        {scanLabel}
        {showCredits && <span className="usage-credits"> · {credits.toFixed(3)} credits</span>}
      </span>
      <button
        type="button"
        className="usage-dots"
        aria-label="Usage details"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
      >
        ⋯
      </button>
      {open && (
        <div className="usage-popover">
          <p><strong>BigQuery scan</strong><br />{from_cache ? "0 bytes (cached)" : `${(bytes_estimate || 0).toLocaleString()} bytes (~${mb.toFixed(2)} MB)`}</p>
          {showCredits && (
            <>
              <p><strong>Credits used</strong><br />{credits.toFixed(4)}</p>
              {typeof credits_remaining === "number" && (
                <p><strong>Balance after</strong><br />{credits_remaining.toFixed(2)} credits</p>
              )}
            </>
          )}
        </div>
      )}
    </span>
  );
}
