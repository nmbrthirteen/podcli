import { useEffect, useState } from "react";

export interface JobResult {
  file_path?: string;
}

export interface JobState<R = JobResult> {
  status?: string;
  progress?: number;
  message?: string;
  error?: string;
  result?: R;
}

export function useJob<R = JobResult>(jobId: string | null): JobState<R> | null {
  const [state, setState] = useState<JobState<R> | null>(null);
  useEffect(() => {
    if (!jobId) {
      setState(null);

      return;
    }

    const es = new EventSource(`/api/job/${jobId}/stream`);
    es.onmessage = (e) => {
      // SAFETY: the job stream sends the job record, whose result is R for the job this hook was given.
      const d = JSON.parse(e.data) as JobState<R>;
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
