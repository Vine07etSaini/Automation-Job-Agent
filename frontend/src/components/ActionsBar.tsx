import { useState } from "react";
import { useJobsStore } from "../store/jobsStore";

export default function ActionsBar() {
  const running = useJobsStore((s) => s.running);
  const runSearch = useJobsStore((s) => s.runSearch);
  const applyApproved = useJobsStore((s) => s.applyApproved);
  const approvedCount = useJobsStore((s) => s.stats?.by_status.approved ?? 0);
  const [source, setSource] = useState("sample");

  return (
    <div className="actions">
      <select value={source} onChange={(e) => setSource(e.target.value)} disabled={running !== null}>
        <option value="sample">sample (offline)</option>
        <option value="naukri">naukri (opens a browser)</option>
      </select>
      <button onClick={() => runSearch(source, 10)} disabled={running !== null}>
        {running === "search" ? "Searching…" : "Search jobs"}
      </button>
      <button
        className="primary"
        onClick={() => {
          // Applying touches the outside world, so always confirm.
          if (window.confirm(`Apply to up to 5 approved jobs (${approvedCount} approved)?`)) void applyApproved(5);
        }}
        disabled={running !== null || approvedCount === 0}
      >
        {running === "apply" ? "Applying…" : `Apply approved (${approvedCount})`}
      </button>
    </div>
  );
}
