import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PageHeader } from "./Page";
import { api } from "./lib";
import { useJob } from "./useJob";
import { BackIcon } from "./icons";
import MulticamStart from "./multicam-start";
import MulticamSources from "./multicam-sources";
import MulticamSync from "./multicam-sync";
import MulticamSummary from "./multicam-summary";
import MulticamResults, { DeliverActions } from "./multicam-deliver";
import type { McJobKind, McPreviewsResp, McSession, McSessionSummary } from "./multicam-types";

const JOB_LABEL: Record<McJobKind, string> = {
  sync: "Syncing",
  plan: "Cutting",
  render: "Rendering",
  preview: "Preparing previews",
};

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const post = <T,>(body: Record<string, unknown>) => api<T>("/multicam", { method: "POST", body: JSON.stringify(body) });

export default function MulticamPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const openId = searchParams.get("session");

  const [sessions, setSessions] = useState<McSessionSummary[]>([]);
  const [session, setSession] = useState<McSession | null>(null);
  const [stills, setStills] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; kind: McJobKind } | null>(null);
  const jobState = useJob(job?.id ?? null);
  const [peopleDraft, setPeopleDraft] = useState<string[]>(["Host", "Guest"]);
  const [sourcesOpen, setSourcesOpen] = useState(false);

  // A running job owns the session until it finishes, so edits can't race it and get overwritten.
  const locked = busy || job !== null;

  async function call<T = McSession>(body: Record<string, unknown>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await post<T>(body);
    } catch (e) {
      setError(errorText(e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  function adopt(next: McSession | null) {
    if (!next) return;
    setSession(next);
    if (next.active_job) setJob({ id: next.active_job.id, kind: next.active_job.action });
  }

  const mutate = (body: Record<string, unknown>) =>
    session && call({ ...body, session_id: session.session_id }).then(adopt);

  function refreshList() {
    post<{ sessions: McSessionSummary[] }>({ action: "list" }).then((r) => setSessions(r.sessions)).catch(() => {});
  }

  function startJob(kind: McJobKind, extra: Record<string, unknown> = {}, sessionId = session?.session_id) {
    if (!sessionId) return;
    call<{ job_id: string }>({ action: kind, session_id: sessionId, ...extra }).then((r) => {
      if (r) setJob({ id: r.job_id, kind });
    });
  }

  useEffect(refreshList, []);

  useEffect(() => {
    setStills({});
    setSourcesOpen(false);
    if (!openId) {
      setSession(null);
      setJob(null);
      return;
    }
    if (session?.session_id !== openId) call({ action: "show", session_id: openId }).then(adopt);
  }, [openId]);

  const cutReady = !!session && session.cuts.length > 0;

  useEffect(() => {
    if (!session) return;
    post<McPreviewsResp>({ action: "previews", session_id: session.session_id })
      .then((r) => setStills(r.previews.cameras))
      .catch(() => {});
  }, [session?.session_id, session?.sources.map((s) => s.id).join()]);

  useEffect(() => {
    if (!job || !jobState) return;
    if (jobState.status === "error") {
      setError(jobState.error || `${JOB_LABEL[job.kind]} failed. Check the files and try again.`);
      setJob(null);
      return;
    }
    if (jobState.status !== "done") return;
    const result = jobState.result as unknown as McSession;
    setJob(null);
    setSession(result);
    refreshList();
    // A clean sync goes straight to cutting: that's the step everyone takes next.
    const allSynced = result.sources.every((s) => s.role === "ignore" || s.offset !== null);
    if (job.kind === "sync" && allSynced) startJob("plan", {}, result.session_id);
  }, [jobState?.status]);

  async function startSession(seed: Record<string, unknown>): Promise<boolean> {
    const r = await call({ action: "new", ...seed, people: peopleDraft.filter((n) => n.trim()) });
    if (!r) return false;
    adopt(r);
    setSearchParams({ session: r.session_id });
    refreshList();
    return true;
  }

  async function deleteSession(id: string) {
    if (!window.confirm("Delete this multicam edit? Your recordings stay where they are.")) return;
    if (await call({ action: "delete", session_id: id })) {
      if (session?.session_id === id) setSearchParams({});
      refreshList();
    }
  }

  function onSync() {
    if (cutReady && !window.confirm("Sync again? This replaces the current cut.")) return;
    startJob("sync");
  }

  async function onMakeClips() {
    const video = session?.outputs.video;
    if (!video) return;
    try {
      await api("/ui-state", {
        method: "POST",
        body: JSON.stringify({
          _source: "ui",
          videoPath: video,
          filePath: video,
          transcript: null,
          rawTranscriptText: "",
          suggestions: [],
          deselectedIndices: [],
          results: [],
          energyData: {},
          phase: "idle",
          silenceOriginal: null,
          silencePlan: null,
        }),
      });
      navigate("/episode");
    } catch (e) {
      setError(`Couldn't open the episode for clips: ${errorText(e)}`);
    }
  }

  const progress = job
    ? `${jobState?.message && jobState.message !== "Starting..." ? jobState.message : JOB_LABEL[job.kind]}${
        jobState?.progress ? ` (${Math.round(jobState.progress)}%)` : ""
      }`
    : null;
  const synced = !!session?.sources.some((s) => s.sync?.status);
  const unsynced = session?.sources.filter((s) => s.role !== "ignore" && s.offset === null) || [];
  const showSources = !session || !cutReady || sourcesOpen || unsynced.length > 0;

  return (
    <div className="app">
      <PageHeader
        title={session ? session.name : "Multicam edit"}
        back={session && (
          <button className="btn btn-ghost btn-sm" disabled={busy} onClick={() => setSearchParams({})} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <BackIcon /> All multicam edits
          </button>
        )}
        actions={session && cutReady && (
          <DeliverActions
            locked={locked}
            exportable={!session.cuts.some((c) => {
              const cam = session.sources.find((s) => s.id === c.source_id);
              return !!cam && (!!cam.parent || cam.members.length > 0);
            })}
            onRender={() => startJob("render", { stems: true })}
            onExport={(format) => mutate({ action: "export", format })}
          />
        )}
      />

      {error && <div className="set-note err" style={{ marginBottom: 16 }}>{error}</div>}
      {progress && (
        <div className="set-note" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <div className="spinner sm" /> {progress}
        </div>
      )}

      {!session ? (
        <MulticamStart
          sessions={sessions}
          people={peopleDraft}
          onPeopleChange={setPeopleDraft}
          busy={locked}
          onStart={startSession}
          onOpen={(id) => setSearchParams({ session: id })}
          onDelete={deleteSession}
        />
      ) : (
        <div className="stream-in">
          {!showSources && (
            <div className="section card" style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 16px" }}>
              <span className="pill pill-green">Synced</span>
              <span className="hint">
                {session.sources.filter((s) => s.role !== "ignore" && !s.parent && !s.members.length).length} files · {session.people.map((p) => p.name).join(", ")}
              </span>
              <button className="btn btn-ghost btn-sm" style={{ marginLeft: "auto" }} onClick={() => setSourcesOpen(true)}>
                Edit sources
              </button>
            </div>
          )}
          {showSources && (
            <>
              <MulticamSources session={session} previewCameras={stills} locked={locked} onEdit={mutate} onSync={onSync} />
              {synced && <MulticamSync session={session} locked={locked} onEdit={mutate} onCut={() => startJob("plan")} />}
              {cutReady && sourcesOpen && (
                <button className="btn btn-ghost btn-sm" style={{ marginBottom: 16 }} onClick={() => setSourcesOpen(false)}>Hide sources</button>
              )}
            </>
          )}
          {cutReady && (
            <>
              <MulticamSummary session={session} locked={locked} onEdit={mutate} onRecut={(extra) => startJob("plan", extra)} />
              <MulticamResults session={session} locked={locked} onMakeClips={onMakeClips} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
