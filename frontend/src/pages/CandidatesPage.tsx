import {
  Alert,
  Button,
  Callout,
  HTMLSelect,
  HTMLTable,
  InputGroup,
  Intent,
  NonIdealState,
  Section,
  SectionCard,
  Spinner,
  Tag,
} from "@blueprintjs/core";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import Explain from "../components/Explain";
import VerdictSummary from "../components/VerdictSummary";
import { api, type Candidate } from "../lib/api";
import { statusIcon, statusIntent, statusLabel } from "../lib/status";
import { isPartial, verdictIcon, verdictIntent, verdictLabel } from "../lib/verdict";

type SortKey =
  | "name"
  | "email"
  | "position_title"
  | "session_status"
  | "verdict"
  | "interviewed_at";
type SortDir = "asc" | "desc";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "name", label: "Candidate" },
  { key: "email", label: "Email" },
  { key: "position_title", label: "Position" },
  { key: "session_status", label: "Status" },
  { key: "verdict", label: "Verdict" },
  { key: "interviewed_at", label: "Interviewed" },
];

// Strongest first when sorting descending, which is the direction a shortlist is read
// in. Inconclusive sits below Not a Fit deliberately: it is not a worse candidate, it is
// an unread one, and it belongs with the rows that still need a human.
const VERDICT_RANK: Record<string, number> = {
  strong_fit: 3,
  decent_fit: 2,
  not_a_fit: 1,
  inconclusive: 0,
};

export default function CandidatesPage() {
  const navigate = useNavigate();
  const [rows, setRows] = useState<Candidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [sortKey, setSortKey] = useState<SortKey>("interviewed_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [pendingDelete, setPendingDelete] = useState<Candidate | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    api
      .get<Candidate[]>("/candidates")
      .then(setRows)
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Could not load candidates");
        setRows([]);
      });
  }, []);

  // Filter options come from the data rather than a hardcoded enum, so a new backend
  // status shows up here without a frontend change.
  const statuses = useMemo(
    () => [...new Set((rows ?? []).map((r) => r.session_status))].sort(),
    [rows],
  );

  const visible = useMemo(() => {
    if (!rows) return [];
    const needle = query.trim().toLowerCase();
    const filtered = rows.filter((row) => {
      if (status !== "all" && row.session_status !== status) return false;
      if (!needle) return true;
      return [row.name, row.email, row.position_title]
        .filter(Boolean)
        .some((field) => field!.toLowerCase().includes(needle));
    });

    const direction = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const left = a[sortKey];
      const right = b[sortKey];
      // Nulls (no email, never interviewed) always sort last regardless of direction.
      if (left == null && right == null) return 0;
      if (left == null) return 1;
      if (right == null) return -1;
      if (sortKey === "interviewed_at") {
        return (Date.parse(left) - Date.parse(right)) * direction;
      }
      // Verdicts are tiers, not words: sorting them alphabetically would interleave
      // "not_a_fit" between "inconclusive" and "strong_fit" and make the column useless
      // for building a shortlist.
      if (sortKey === "verdict") {
        return (VERDICT_RANK[left] - VERDICT_RANK[right]) * direction;
      }
      return left.localeCompare(right) * direction;
    });
  }, [rows, query, status, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  const filtering = query.trim() !== "" || status !== "all";

  return (
    <Section
      title={
        <Explain text="Every session created across all interviews. Open a candidate for their score and transcript.">
          Candidates
        </Explain>
      }
      icon="people"
      rightElement={
        rows ? (
          <Tag minimal round>
            {filtering ? `${visible.length} of ${rows.length}` : `${rows.length}`}
          </Tag>
        ) : undefined
      }
    >
      {rows !== null && rows.length > 0 && (
        <SectionCard padded={false}>
          <VerdictSummary candidates={visible} />
        </SectionCard>
      )}

      <SectionCard padded={false}>
        <div className="row" style={{ padding: "10px 16px", gap: 10 }}>
          <InputGroup
            leftIcon="search"
            placeholder="Search name, email or position..."
            value={query}
            onValueChange={setQuery}
            style={{ flex: 1, minWidth: 220 }}
            rightElement={
              query ? (
                <Button
                  variant="minimal"
                  icon="cross"
                  onClick={() => setQuery("")}
                  aria-label="Clear search"
                />
              ) : undefined
            }
          />
          <HTMLSelect
            value={status}
            onChange={(e) => setStatus(e.currentTarget.value)}
            options={[
              { value: "all", label: "All statuses" },
              ...statuses.map((s) => ({ value: s, label: statusLabel(s) })),
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard padded={false}>
        {error && (
          <Callout intent={Intent.DANGER} icon="error" style={{ borderRadius: 0 }}>
            {error}
          </Callout>
        )}

        {rows === null && (
          <NonIdealState icon={<Spinner />} title="Loading candidates" />
        )}

        {rows !== null && rows.length === 0 && !error && (
          <NonIdealState
            icon="inbox"
            title="No candidate sessions yet"
            description="Create an interview link on the Interview setup page and it will appear here."
          />
        )}

        {rows !== null && rows.length > 0 && visible.length === 0 && (
          <NonIdealState
            icon="search"
            title="No matches"
            description="No candidate matches the current search and filter."
            action={
              <Button
                variant="minimal"
                icon="filter-remove"
                text="Clear filters"
                onClick={() => {
                  setQuery("");
                  setStatus("all");
                }}
              />
            }
          />
        )}

        {visible.length > 0 && (
          <HTMLTable interactive striped compact style={{ width: "100%" }}>
            <thead>
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} style={{ padding: 0 }}>
                    <Button
                      variant="minimal"
                      size="small"
                      fill
                      alignText="start"
                      text={col.label}
                      endIcon={
                        col.key === sortKey
                          ? sortDir === "asc"
                            ? "caret-up"
                            : "caret-down"
                          : "double-caret-vertical"
                      }
                      onClick={() => toggleSort(col.key)}
                    />
                  </th>
                ))}
                <th style={{ padding: 0, width: 1 }} />
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr
                  key={row.id}
                  className="candidate-row"
                  onClick={() => navigate(`/candidates/${row.id}`)}
                >
                  <td>
                    {/* A link rather than only a row handler: HR opens candidates in new
                        tabs to compare two of them side by side. */}
                    <Link
                      to={`/candidates/${row.id}`}
                      style={{ fontWeight: 500 }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {row.name}
                    </Link>
                  </td>
                  <td className="bp6-text-muted">{row.email ?? "—"}</td>
                  <td>{row.position_title}</td>
                  <td>
                    <Tag
                      minimal
                      intent={statusIntent(row.session_status)}
                      icon={statusIcon(row.session_status)}
                    >
                      {statusLabel(row.session_status)}
                    </Tag>
                  </td>
                  <td>
                    {row.verdict ? (
                      <span className="verdict-cell">
                        <Tag
                          minimal
                          intent={verdictIntent(row.verdict)}
                          icon={verdictIcon(row.verdict)}
                        >
                          {verdictLabel(row.verdict)}
                        </Tag>
                        {/* Two candidates can hold the same tier on very different
                            amounts of conversation. Without this the list says they are
                            the same, and the difference is only found by opening them. */}
                        {isPartial(row.interview_completeness) && (
                          <Tag
                            minimal
                            round
                            intent={Intent.WARNING}
                            icon="stopwatch"
                            title="The interview ended before it had run its course"
                          >
                            partial
                          </Tag>
                        )}
                      </span>
                    ) : row.evaluation_status === "failed" ? (
                      <Tag minimal intent={Intent.DANGER} icon="error">
                        scoring failed
                      </Tag>
                    ) : (
                      <span className="bp6-text-muted">—</span>
                    )}
                  </td>
                  <td className="bp6-text-muted">
                    {row.interviewed_at
                      ? new Date(row.interviewed_at).toLocaleString()
                      : "not started"}
                  </td>
                  <td>
                    <Button
                      variant="minimal"
                      size="small"
                      intent={Intent.DANGER}
                      icon="trash"
                      aria-label={`Delete ${row.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        setPendingDelete(row);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </HTMLTable>
        )}
      </SectionCard>

      <Alert
        isOpen={pendingDelete !== null}
        intent={Intent.DANGER}
        icon="trash"
        confirmButtonText="Delete candidate"
        cancelButtonText="Cancel"
        loading={deleting}
        onCancel={() => setPendingDelete(null)}
        onConfirm={async () => {
          if (!pendingDelete) return;
          setDeleting(true);
          try {
            await api.del(`/candidates/${pendingDelete.id}`);
            setRows((prev) => (prev ?? []).filter((r) => r.id !== pendingDelete.id));
            setPendingDelete(null);
          } catch (err) {
            setError(err instanceof Error ? err.message : "Could not delete this candidate");
          } finally {
            setDeleting(false);
          }
        }}
      >
        <p>
          Delete <strong>{pendingDelete?.name}</strong> and their transcript and score? This
          cannot be undone.
        </p>
      </Alert>
    </Section>
  );
}
