import { STATUSES } from "../api/types";
import { useJobsStore } from "../store/jobsStore";

export default function StatusTabs() {
  const filter = useJobsStore((s) => s.filter);
  const setFilter = useJobsStore((s) => s.setFilter);
  const byStatus = useJobsStore((s) => s.stats?.by_status);
  const total = useJobsStore((s) => s.stats?.total ?? 0);

  return (
    <nav className="tabs">
      <button className={filter === "all" ? "active" : ""} onClick={() => setFilter("all")}>
        all ({total})
      </button>
      {STATUSES.map((st) => (
        <button key={st} className={filter === st ? "active" : ""} onClick={() => setFilter(st)}>
          {st.replace("_", " ")} ({byStatus?.[st] ?? 0})
        </button>
      ))}
    </nav>
  );
}
