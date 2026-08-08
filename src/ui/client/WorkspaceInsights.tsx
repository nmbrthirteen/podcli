import React, { useEffect, useState } from "react";
import { PenLine, TrendingUp, Users } from "lucide-react";
import { labelStyle } from "./lib";

/**
 * Workspace-wide performance and learned house style.
 *
 * Renders nothing at all when signed out. A free user sees the Analytics page
 * they have always seen — no locked panel, no upsell banner, no greyed-out
 * button. This section exists because the workspace data exists.
 */

type Breakdown = { key: string; clips: number; retention: number | null };

type Payload = {
  signedIn: boolean;
  insights?: {
    sampleSize: number;
    byContentType: Breakdown[];
    byLength: Breakdown[];
    guidance: string[];
    topClips: Array<{ title: string; retention: number; content_type: string }>;
  };
  preferences?: {
    observations: string[];
    discardRate: number | null;
    titleEdits: Array<{ before: string; after: string }>;
  };
};

export default function WorkspaceInsights() {
  const [data, setData] = useState<Payload | null>(null);

  useEffect(() => {
    fetch("/api/pro/insights")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setData)
      .catch(() => setData({ signedIn: false }));
  }, []);

  if (!data?.signedIn || !data.insights) return null;

  const { insights, preferences } = data;
  // The types say these arrays are always there. The server can answer with a
  // partial payload, and reading a missing one throws out of this panel and
  // takes the page it sits on with it.
  const hasModel = (insights.guidance?.length ?? 0) > 0;
  const hasStyle = (preferences?.observations?.length ?? 0) > 0;

  // Signed in but nothing learned yet. Say why, and say what changes it —
  // an empty panel with no explanation reads as broken.
  if (!hasModel && !hasStyle) {
    return (
      <div className="section card">
        <div style={labelStyle}>Workspace</div>
        <div className="hint">
          {insights.sampleSize === 0
            ? "Connect YouTube and publish a few clips. Once performance data arrives, podcli starts picking moments based on what works for this channel."
            : `Tracking ${insights.sampleSize} published clip${insights.sampleSize === 1 ? "" : "s"}. A few more and patterns become reliable enough to act on.`}
        </div>
      </div>
    );
  }

  return (
    <>
      {hasModel && (
        <div className="section card">
          <div style={labelStyle}>
            <Users className="ico" strokeWidth={1.8} size={13} /> What works on this channel
          </div>
          <div className="hint" style={{ marginBottom: 14 }}>
            From {insights.sampleSize} published clips across your workspace. podcli uses this
            when picking moments.
          </div>
          {insights.guidance.map((line) => (
            <div key={line} className="bar-row" style={{ display: "flex", gap: 8 }}>
              <TrendingUp className="ico" strokeWidth={1.8} size={14} style={{ marginTop: 2, flexShrink: 0 }} />
              <span>{line}</span>
            </div>
          ))}
        </div>
      )}

      {hasStyle && (
        <div className="section card">
          <div style={labelStyle}>
            <PenLine className="ico" strokeWidth={1.8} size={13} /> House style
          </div>
          <div className="hint" style={{ marginBottom: 14 }}>
            Learned from edits your team made to generated output. Nobody configured these.
          </div>
          {preferences!.observations.map((line) => (
            <div key={line} className="bar-row">{line}</div>
          ))}

          {preferences!.titleEdits.length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary className="hint" style={{ cursor: "pointer" }}>
                Recent title rewrites ({preferences!.titleEdits.length})
              </summary>
              <div style={{ marginTop: 10 }}>
                {preferences!.titleEdits.slice(0, 6).map((edit, i) => (
                  <div key={i} className="bar-row" style={{ fontSize: 13 }}>
                    <div style={{ color: "var(--text2)", textDecoration: "line-through" }}>
                      {edit.before}
                    </div>
                    <div>{edit.after}</div>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}
    </>
  );
}
