import React, { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Check } from "lucide-react";
import { api } from "./lib";

interface Onboarding {
  knowledge: { total: number; present: number; filled: string[]; missing: string[] };
  assets: { count: number; branding: boolean };
  aiCli: { available: boolean };
  clips: { count: number };
  dismissed: boolean;
}

interface Step {
  title: string;
  desc: string;
  done: boolean;
  action: React.ReactNode;
}

const BRAND_FILES = ["01-brand-identity.md", "02-voice-and-tone.md"];

export default function SetupChecklist() {
  const [state, setState] = useState<Onboarding | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<Onboarding>("/onboarding")
      .then(setState)
      .catch(() => setState(null));
  }, []);

  useEffect(load, [load]);

  if (!state || state.dismissed || dismissed) return null;

  const createKnowledge = async () => {
    setCreating(true);
    setError(null);
    try {
      await api("/knowledge/init", { method: "POST" });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the knowledge base");
    } finally {
      setCreating(false);
    }
  };

  const dismiss = () => {
    setDismissed(true);
    api("/ui-state", {
      method: "POST",
      body: JSON.stringify({ _source: "ui", settings: { onboardingDismissed: true } }),
    }).catch(() => { });
  };

  const steps: Step[] = [
    {
      title: "Create the knowledge base",
      desc: "The files podcli reads before it picks a single clip.",
      done: state.knowledge.total > 0 && state.knowledge.present === state.knowledge.total,
      action: (
        <button className="btn btn-primary btn-sm" onClick={createKnowledge} disabled={creating}>
          {creating ? <div className="spinner sm" /> : "Create starter templates"}
        </button>
      ),
    },
    {
      title: "Fill in brand identity and voice",
      desc: "Who the show is for, and the words it never uses.",
      done: BRAND_FILES.every((f) => state.knowledge.filled.includes(f)),
      action: <Link to="/knowledge" className="btn btn-ghost btn-sm">Open knowledge</Link>,
    },
    {
      title: "Add a logo or outro",
      desc: "Branding that gets stamped onto every clip you render.",
      done: state.assets.branding,
      action: <Link to="/assets" className="btn btn-ghost btn-sm">Open assets</Link>,
    },
    {
      title: "Connect an AI CLI",
      desc: "Clip suggestions run through Claude Code or Codex on your machine.",
      done: state.aiCli.available,
      action: <Link to="/mcp" className="btn btn-ghost btn-sm">Setup guide</Link>,
    },
  ];

  const done = steps.filter((s) => s.done).length;
  if (done === steps.length) return null;

  return (
    <div className="section card setup fade-in">
      <div className="setup-head">
        <div>
          <div className="section-label" style={{ marginBottom: 2 }}>Set up your studio</div>
          <div className="meta">{done} of {steps.length} done</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={dismiss}>Dismiss</button>
      </div>

      <ul className="setup-steps">
        {steps.map((s) => (
          <li key={s.title} className={`setup-step${s.done ? " done" : ""}`}>
            <span className="setup-mark">{s.done && <Check size={12} strokeWidth={3} />}</span>
            <div className="setup-body">
              <div className="setup-title">{s.title}</div>
              <div className="setup-desc">{s.desc}</div>
            </div>
            {!s.done && <div className="setup-action">{s.action}</div>}
          </li>
        ))}
      </ul>

      {error && <div className="set-note err">{error}</div>}
    </div>
  );
}
