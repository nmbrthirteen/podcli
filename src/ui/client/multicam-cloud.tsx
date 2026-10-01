import React, { useEffect, useState } from "react";
import type { McSession } from "./multicam-types";

/** The studio cuts automatically; this hands the edit to the podcli cloud editor and renders what comes back. */
export default function MulticamCloud({
  session,
  locked,
  onSend,
  onPull,
}: {
  session: McSession;
  locked: boolean;
  onSend: () => void;
  onPull: () => void;
}) {
  const [signedIn, setSignedIn] = useState(false);

  useEffect(() => {
    fetch("/api/pro/account")
      .then((r) => r.json())
      .then((a: { signedIn?: boolean }) => setSignedIn(Boolean(a.signedIn)))
      .catch(() => setSignedIn(false));
  }, []);

  if (!signedIn) return null;
  const sent = session.cloud?.url;

  return (
    <div className="section card">
      <div className="card-title" style={{ marginBottom: 4 }}>Steer the cut in podcli cloud</div>
      <p className="card-desc">
        {sent
          ? "Edit the cut and trim the conversation in the browser, then render it here from your camera files."
          : "Sends small previews and the transcript, never your camera files. Then edit the cut and trim the conversation in the browser."}
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {sent && (
          <a className="btn btn-primary btn-sm" href={sent} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>
            Open the cloud edit
          </a>
        )}
        {sent && (
          <button className="btn btn-ghost btn-sm" disabled={locked} onClick={onPull}>Render the cloud edit</button>
        )}
        <button className={`btn ${sent ? "btn-ghost" : "btn-primary"} btn-sm`} disabled={locked} onClick={onSend}>
          {sent ? "Send it again" : "Edit in podcli cloud"}
        </button>
      </div>
    </div>
  );
}
