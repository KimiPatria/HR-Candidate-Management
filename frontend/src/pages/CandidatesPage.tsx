import { useEffect, useState } from "react";

import { api, type Candidate } from "../lib/api";

export default function CandidatesPage() {
  const [rows, setRows] = useState<Candidate[] | null>(null);

  useEffect(() => {
    api.get<Candidate[]>("/candidates").then(setRows).catch(() => setRows([]));
  }, []);

  return (
    <div className="card">
      <h2>Candidates</h2>
      <div className="notice">
        Stub page. Every session that has been created shows up here; scoring, rubric
        evaluation and shortlisting are not built yet.
      </div>
      {rows === null && <p className="muted">Loading...</p>}
      {rows?.length === 0 && <p className="muted">No candidate sessions yet.</p>}
      {rows && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Email</th>
              <th>Position</th>
              <th>Status</th>
              <th>Interviewed</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.name}</td>
                <td className="muted">{row.email ?? "-"}</td>
                <td>{row.position_title}</td>
                <td>
                  <span className={`badge ${row.session_status}`}>{row.session_status}</span>
                </td>
                <td className="muted">
                  {row.interviewed_at
                    ? new Date(row.interviewed_at).toLocaleString()
                    : "not started"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
