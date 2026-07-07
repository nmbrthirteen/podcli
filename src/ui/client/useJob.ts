import { useEffect, useState } from "react";

export interface JobState {
  status?: string;
  progress?: number;
  message?: string;
  error?: string;
  result?: { file_path?: string; [key: string]: unknown };
}

export function useJob(jobId: string | null): JobState | null {
  const [state, setState] = useState<JobState | null>(null);
  useEffect(() => {
    if (!jobId) {
      setState(null);
      return;
    }
    const es = new EventSource(`/api/job/${jobId}/stream`);
    es.onmessage = (e) => {
      const d = JSON.parse(e.data) as JobState;
      setState(d);
      if (d.status === "done" || d.status === "error") es.close();
    };
    es.onerror = () => {
      es.close();
      // Surface a dropped connection so consumers don't hang on the last status.
      setState((s) =>
        s && (s.status === "done" || s.status === "error")
          ? s
          : { ...s, status: "error", error: s?.error || "Lost connection to the job stream" },
      );
    };
    return () => es.close();
  }, [jobId]);
  return state;
}
