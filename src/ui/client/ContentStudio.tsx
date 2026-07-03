import React, { useEffect, useState } from "react";
import { api, basename, labelStyle } from "./lib";
import CopyButton from "./CopyButton";

interface ContentResult {
  titles?: string[];
  top_pick?: string;
  description?: string;
  tags?: string;
  hashtags?: string;
  engine?: string;
}

const STORE = "podcli.content-studio";

export default function ContentStudio() {
  const [title, setTitle] = useState("");
  const [transcript, setTranscript] = useState("");
  const [mode, setMode] = useState<"episode" | "shorts">("shorts");
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [result, setResult] = useState<ContentResult | null>(null);
  const [sessionText, setSessionText] = useState("");
  const [sessionName, setSessionName] = useState("");
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) || "null");
      if (saved?.result) {
        setResult(saved.result);
        setTitle(saved.title || "");
        setMode(saved.mode === "episode" ? "episode" : "shorts");
      }
    } catch {}
    api("/ui-state")
      .then((s) => {
        setSessionText(s?.transcript?.transcript || s?.rawTranscriptText || "");
        setSessionName(basename(s?.filePath || s?.videoPath || ""));
      })
      .catch(() => {});
  }, []);

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(text);
      setTimeout(() => setCopied(null), 1500);
    });
  };

  const generate = async () => {
    if (!transcript.trim()) {
      setMsg("Paste a transcript first");
      return;
    }
    setBusy(true);
    setMsg(null);
    setResult(null);
    setStage("Starting generation...");
    const streamId = Math.random().toString(36).slice(2);
    const es = new EventSource("/api/events");
    es.addEventListener("content-partial", (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data);
        if (d.stream_id === streamId && d.partial) setResult(d.partial);
      } catch {}
    });
    es.addEventListener("job-update", (e) => {
      try {
        const d = JSON.parse((e as MessageEvent).data);
        if (d.message) setStage(d.message);
      } catch {}
    });
    try {
      const r = await api<ContentResult>("/content-studio/generate", {
        method: "POST",
        body: JSON.stringify({ title: title || undefined, transcript_text: transcript, mode, stream_id: streamId }),
      });
      if (!r.titles?.length && !r.description) throw new Error("AI CLI returned nothing. Is claude or codex installed?");
      setResult(r);
      try { localStorage.setItem(STORE, JSON.stringify({ title, mode, result: r })); } catch {}
    } catch (e: any) {
      setMsg(`Generation failed: ${e.message}`);
    } finally {
      es.close();
      setBusy(false);
      setStage(null);
    }
  };

  return (
    <div className="app">
      <div className="header">
        <h1>Content studio</h1>
      </div>

      <div style={{ display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ flex: "1 1 380px", minWidth: 320 }}>
          <div className="section">
            <label style={labelStyle}>Episode / clip title (optional)</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Working title or topic"
              style={{ width: "100%" }}
            />

            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: "18px 0 0" }}>
              <label style={{ ...labelStyle, marginBottom: 0 }}>Transcript</label>
              {sessionText && (
                <button className="btn btn-ghost btn-sm" onClick={() => setTranscript(sessionText)} disabled={busy} title={sessionName}>
                  Use current episode{sessionName ? ` · ${sessionName}` : ""}
                </button>
              )}
            </div>
            <textarea
              value={transcript}
              onChange={(e) => setTranscript(e.target.value)}
              placeholder="Paste the transcript here"
              style={{ width: "100%", minHeight: 260, lineHeight: 1.6, marginTop: 8, resize: "vertical" }}
            />

            <div style={{ display: "flex", gap: 10, marginTop: 12, alignItems: "center" }}>
              <select value={mode} onChange={(e) => setMode(e.target.value as "episode" | "shorts")} style={{ flex: 1 }}>
                <option value="episode">Full episode (long-form)</option>
                <option value="shorts">Short / clip</option>
              </select>
              <button className="btn btn-primary btn-sm" onClick={generate} disabled={busy || !transcript.trim()}>
                {busy ? <><div className="spinner sm" /> Generating…</> : "Generate"}
              </button>
            </div>
            {busy && stage && <div style={{ fontSize: 11, color: "var(--text3)", marginTop: 10 }}>{stage}</div>}
            {msg && <div className="set-note ok" style={{ marginTop: 10, wordBreak: "break-word" }}>{msg}</div>}
          </div>
        </div>

        <div style={{ flex: "1 1 420px", minWidth: 320 }}>
          {!result ? (
            <div className="section" style={{ color: "var(--text3)", fontSize: 12, display: "flex", alignItems: "center", gap: 8 }}>
              {busy ? <><div className="spinner sm" /> Waiting for the first lines</> : "Titles, description, tags, and hashtags appear here."}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              {result.titles?.length ? (
                <div className="section">
                  <div style={{ fontSize: 11, color: "var(--text3)", marginBottom: 6 }}>Title options · click to copy</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                    {result.titles.map((t, i) => {
                      const clean = t.replace(/^\d+\.\s*/, "");
                      return (
                        <button key={i} className={`title-option stream-in ${copied === clean ? "selected" : ""}`} onClick={() => copy(clean)}>
                          {t}{copied === clean ? " · copied" : ""}
                        </button>
                      );
                    })}
                  </div>
                  {result.top_pick && <div className="stream-in" style={{ fontSize: 12, color: "var(--accent)", marginTop: 10 }}>{result.top_pick}</div>}
                </div>
              ) : null}

              {result.description ? (
                <div className="section stream-in">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: "var(--text3)" }}>Description</span>
                    <CopyButton text={result.description} />
                  </div>
                  <div style={{ fontSize: 13, color: "var(--text2)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{result.description}</div>
                </div>
              ) : null}

              {result.tags ? (
                <div className="section stream-in">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: "var(--text3)" }}>Tags</span>
                    <CopyButton text={result.tags} />
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text2)", lineHeight: 1.6 }}>{result.tags}</div>
                </div>
              ) : null}

              {result.hashtags ? (
                <div className="section stream-in">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                    <span style={{ fontSize: 11, color: "var(--text3)" }}>Hashtags</span>
                    <CopyButton text={result.hashtags} />
                  </div>
                  <div style={{ fontSize: 12, color: "var(--accent)", lineHeight: 1.6 }}>{result.hashtags}</div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
