import { useJobsStore } from "../store/jobsStore";

export default function JobDetailPane() {
  const detail = useJobsStore((s) => s.detail);
  const selectedId = useJobsStore((s) => s.selectedId);
  const approve = useJobsStore((s) => s.approve);
  const reject = useJobsStore((s) => s.reject);

  if (!selectedId) return <section className="detail empty">Select a job to review it.</section>;
  if (!detail) return <section className="detail empty">Loading…</section>;

  const canApprove = ["pending_review", "rejected", "needs_manual"].includes(detail.status);
  const canReject = !["applied", "rejected"].includes(detail.status);

  return (
    <section className="detail">
      <h2>{detail.title}</h2>
      <p className="sub">
        {detail.company} · {detail.location || "n/a"} · <span className="badge">{detail.status}</span>
      </p>
      {detail.status_reason && <p className="reason">{detail.status_reason}</p>}

      <div className="buttons">
        <button className="primary" disabled={!canApprove} onClick={() => approve(detail.id)}>
          Approve
        </button>
        <button disabled={!canReject} onClick={() => reject(detail.id)}>
          Reject
        </button>
        {detail.url && (
          <a href={detail.url} target="_blank" rel="noreferrer">
            Open posting ↗
          </a>
        )}
      </div>

      {detail.match && (
        <>
          <h3>Match: {detail.match.score}/100</h3>
          <p>{detail.match.explanation}</p>
          <TagRow label="Matched" items={detail.match.matched_mandatory} kind="ok" />
          <TagRow label="Missing" items={detail.match.missing_mandatory} kind="bad" />
          <TagRow label="Nice to have" items={detail.match.matched_nice} kind="ok" />
        </>
      )}

      {detail.guard?.dropped_skills && detail.guard.dropped_skills.length > 0 && (
        <TagRow label="Honesty Guard dropped" items={detail.guard.dropped_skills} kind="bad" />
      )}

      {detail.resume_path && <p className="path">Resume: {detail.resume_path}</p>}

      <h3>Description</h3>
      <pre>{detail.description || "No description."}</pre>
    </section>
  );
}

function TagRow({ label, items, kind }: { label: string; items: string[]; kind: "ok" | "bad" }) {
  if (items.length === 0) return null;
  return (
    <div className="tags">
      <span>{label}:</span>
      {items.map((i) => (
        <span key={i} className={`tag ${kind}`}>
          {i}
        </span>
      ))}
    </div>
  );
}
