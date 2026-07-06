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
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId]);
  return state;
}
