"use client";

/** Original RFC 5322 headers, on demand, for both PostBox reading layouts. */
import { useState } from "react";
import { FileText, Loader2 } from "lucide-react";
import { postbox } from "@/lib/postbox-api";
import { describePostBoxError } from "@/contexts/postbox-context";

export function MessageHeaders({
  folder, uid, uidValidity,
}: {
  folder: string;
  uid: number;
  uidValidity: number;
}) {
  const [visible, setVisible] = useState(false);
  const [headers, setHeaders] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    if (visible) {
      setVisible(false);
      return;
    }
    setVisible(true);
    if (headers !== null || loading) return;
    setLoading(true);
    setError(null);
    try {
      const result = await postbox.headers(folder, uid, uidValidity);
      setHeaders(result.headers);
    } catch (caught) {
      setError(describePostBoxError(
        caught, "Could not load the original headers. Refresh and try again.",
      ));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="pb-message-headers">
      <button type="button" className="pb-btn pb-btn-plain"
        aria-expanded={visible} onClick={() => void toggle()}>
        <FileText className="h-4 w-4" aria-hidden="true" />
        {visible ? "Hide headers" : "Headers"}
      </button>
      {visible && (
        <div className="pb-message-headers-content">
          {loading && <span role="status" className="flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading headers…
          </span>}
          {error && <p role="alert">{error}</p>}
          {headers !== null && <pre aria-label="Original message headers">{headers}</pre>}
          {!loading && error && (
            <button type="button" className="pb-btn pb-btn-plain"
              onClick={() => { setVisible(false); setError(null); }}>Close</button>
          )}
        </div>
      )}
    </div>
  );
}
