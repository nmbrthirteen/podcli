import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PageHeader } from "./Page";
import { api } from "./lib";
import { useJob } from "./useJob";
import { BackIcon } from "./icons";
import MulticamStart from "./multicam-start";
import MulticamSources from "./multicam-sources";
import MulticamSync from "./multicam-sync";
import MulticamCut from "./multicam-cut";
import MulticamLook from "./multicam-look";
import MulticamDeliver from "./multicam-deliver";
import type { McJobKind, McPreviews, McPreviewsResp, McSession, McSessionSummary } from "./multicam-types";

const emptyPreviews: McPreviews = { cameras: {}, looks: {} };

const JOB_LABEL: Record<McJobKind, string> = {
  sync: "Syncing",
  plan: "Cutting",
  render: "Rendering",
};

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export default function MulticamPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const openId = searchParams.get("session");

  const [sessions, setSessions] = useState<McSessionSummary[]>([]);
  const [session, setSession] = useState<McSession | null>(null);
  const [previews, setPreviews] = useState<McPreviews>(emptyPreviews);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<{ id: string; kind: McJobKind } | null>(null);
  const jobState = useJob(job?.id ?? null);
  const [peopleDraft, setPeopleDraft] = useState<string[]>(["Host", "Guest"]);

  // One lock for the whole page: a running job owns the session until it
  // finishes, so edits can't race it and get overwritten.
  const locked = busy || job !== null;

  async function call<T = McSession>(body: Record<string, unknown>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      return await api<T>("/multicam", { method: "POST", body: JSON.stringify(body) });
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
    api<{ sessions: McSessionSummary[] }>("/multicam", { method: "POST", body: JSON.stringify({ action: "list" }) })
      .then((r) => setSessions(r.sessions))
      .catch(() => {});
  }

  useEffect(() => {
    refreshList();
  }, []);

  useEffect(() => {
    if (!openId) {
      setSession(null);
      setJob(null);
      setPreviews(emptyPreviews);
      return;
    }
    if (session?.session_id !== openId) {
      setPreviews(emptyPreviews);
      call({ action: "show", session_id: openId }).then(adopt);
    }
  }, [openId]);

  const cutReady = !!session && session.cuts.length > 0;
  useEffect(() => {
    if (!session) return;
    const wantLooks = cutReady && Object.keys(previews.looks).length === 0;
    if (Object.keys(previews.cameras).length > 0 && !wantLooks) return;
    api<McPreviewsResp>("/multicam", {
      method: "POST",
      body: JSON.stringify({ action: "previews", session_id: session.session_id, looks: cutReady }),
    })
      .then((r) => setPreviews(r.previews))
      .catch(() => {});
  }, [session?.session_id, cutReady]);

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

  function startJob(kind: McJobKind, extra: Record<string, unknown> = {}, sessionId = session?.session_id) {
    if (!sessionId) return;
    call<{ job_id: string }>({ action: kind, session_id: sessionId, ...extra }).then((r) => {
      if (r) setJob({ id: r.job_id, kind });
    });
  }

  async function startSession(seed: Record<string, unknown>): Promise<boolean> {
    const r = await call({ action: "new", ...seed, people: peopleDraft.filter((n) => n.trim()) });
    if (!r) return false;
    setSession(r);
    setSearchParams({ session: r.session_id });
    refreshList();
    return true;
  }

  function closeSession() {
    setError(null);
    setSearchParams({});
  }

  async function deleteSession(id: string) {
    if (!window.confirm("Delete this multicam edit? Your recordings stay where they are.")) return;
    if (await call({ action: "delete", session_id: id })) {
      if (session?.session_id === id) closeSession();
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
  const syncStarted = !!session?.sources.some((s) => s.sync?.status);

  return (
    <div className="app">
      <PageHeader title="Multicam edit" />

      {error && <div className="set-note err" style={{ marginBottom: 16 }}>{error}</div>}

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
          <button className="btn btn-ghost btn-sm" onClick={closeSession} style={{ display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 16 }}>
            <BackIcon /> All multicam edits
          </button>

          {progress && (
            <div className="set-note" style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
              <div className="spinner sm" /> {progress}
            </div>
          )}

          <MulticamSources
            session={session}
            previewCameras={previews.cameras}
            locked={locked}
            onEdit={mutate}
            onSync={onSync}
          />
          {syncStarted && (
            <MulticamSync session={session} locked={locked} onEdit={mutate} onCut={() => startJob("plan")} />
          )}
          {cutReady && (
            <>
              <MulticamCut
                session={session}
                locked={locked}
                onEdit={mutate}
                onRecut={(extra) => startJob("plan", extra)}
              />
              <MulticamLook session={session} lookPreviews={previews.looks} locked={locked} onEdit={mutate} />
              <MulticamDeliver
                session={session}
                locked={locked}
                onRender={() => startJob("render", { stems: true })}
                onExport={(format) => mutate({ action: "export", format })}
                onMakeClips={onMakeClips}
              />
            </>
          )}
        </div>
      )}
    </div>
  );
}
