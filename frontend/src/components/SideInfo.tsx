import { useEffect } from "react";
import { useJobsStore } from "../store/jobsStore";
import { useProfileStore } from "../store/profileStore";

export default function SideInfo() {
  const profile = useProfileStore((s) => s.profile);
  const loadProfile = useProfileStore((s) => s.load);
  const gaps = useJobsStore((s) => s.skillGaps);
  const appliedToday = useJobsStore((s) => s.stats?.applied_today ?? 0);

  useEffect(() => {
    void loadProfile();
  }, [loadProfile]);

  return (
    <aside>
      <h3>Today</h3>
      <p>{appliedToday} applied</p>

      <h3>Skill gaps</h3>
      {gaps && gaps.missing_skills.length > 0 ? (
        <ul>
          {gaps.missing_skills.map((g) => (
            <li key={g.skill}>
              {g.skill} <small>({g.jobs} jobs{g.learning ? ", learning" : ""})</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">None yet.</p>
      )}

      <h3>Profile</h3>
      {profile ? (
        <>
          <p>
            {profile.personal.name}
            <br />
            <small>{profile.total_experience_years} yrs · {profile.skills.length} verified skills</small>
          </p>
          <div className="tags">
            {profile.skills.slice(0, 12).map((s) => (
              <span key={s.name} className="tag">
                {s.name}
              </span>
            ))}
          </div>
        </>
      ) : (
        <p className="muted">Loading…</p>
      )}
    </aside>
  );
}
