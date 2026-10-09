import { useJobsStore } from "../store/jobsStore";

export default function JobList() {
  const jobs = useJobsStore((s) => s.jobs);
  const loading = useJobsStore((s) => s.loading);
  const selectedId = useJobsStore((s) => s.selectedId);
  const select = useJobsStore((s) => s.select);

  if (!loading && jobs.length === 0) {
    return <section className="list empty">No jobs here yet. Try “Search jobs”.</section>;
  }

  return (
    <section className="list">
      {jobs.map((j) => (
        <button key={j.id} className={`card ${j.id === selectedId ? "selected" : ""}`} onClick={() => select(j.id)}>
          <span className={`score ${scoreClass(j.match_score)}`}>{j.match_score ?? "–"}</span>
          <span className="meta">
            <strong>{j.title}</strong>
            <small>
              {j.company} · {j.location || "n/a"} · {j.portal}
            </small>
          </span>
        </button>
      ))}
    </section>
  );
}

function scoreClass(score: number | null): string {
  if (score === null) return "none";
  return score >= 70 ? "good" : score >= 50 ? "mid" : "low";
}
