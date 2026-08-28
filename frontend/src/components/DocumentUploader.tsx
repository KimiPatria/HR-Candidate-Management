import { useEffect, useRef, useState } from "react";

import { api, type DocType, type InterviewDocument } from "../lib/api";

const LABELS: Record<DocType, string> = {
  job_requirement: "Job requirements",
  knowledge_base: "Knowledge base",
};

export default function DocumentUploader({
  interviewId,
  documents,
  onChange,
}: {
  interviewId: string;
  documents: InterviewDocument[];
  onChange: () => void;
}) {
  const [docType, setDocType] = useState<DocType>("job_requirement");
  const [pasted, setPasted] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // Indexing runs as a background task, so poll while anything is still in flight.
  const settling = documents.some(
    (d) => d.index_status === "pending" || d.index_status === "indexing",
  );
  useEffect(() => {
    if (!settling) return;
    const timer = setInterval(onChange, 1500);
    return () => clearInterval(timer);
  }, [settling, onChange]);

  async function submitFile(file: File) {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("doc_type", docType);
      form.append("file", file);
      await api.upload(`/interviews/${interviewId}/documents/upload`, form);
      if (fileInput.current) fileInput.current.value = "";
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function submitPaste() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/interviews/${interviewId}/documents/paste`, {
        doc_type: docType,
        content_text: pasted,
      });
      setPasted("");
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save text");
    } finally {
      setBusy(false);
    }
  }

  async function remove(documentId: string) {
    await api.del(`/interviews/${interviewId}/documents/${documentId}`);
    onChange();
  }

  async function reindex(documentId: string) {
    await api.post(`/interviews/${interviewId}/documents/${documentId}/reindex`);
    onChange();
  }

  return (
    <div className="card">
      <h2>Reference documents</h2>
      {error && <div className="notice error">{error}</div>}

      <label htmlFor="doc-type">Document type</label>
      <select
        id="doc-type"
        value={docType}
        onChange={(e) => setDocType(e.target.value as DocType)}
      >
        <option value="job_requirement">
          Job requirements (drives the question plan)
        </option>
        <option value="knowledge_base">
          Knowledge base (company and role facts the AI may cite)
        </option>
      </select>

      <label htmlFor="doc-file">Upload a file (.pdf, .docx, .txt, .md)</label>
      <input
        id="doc-file"
        ref={fileInput}
        type="file"
        accept=".pdf,.docx,.txt,.md"
        disabled={busy}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) void submitFile(file);
        }}
      />

      <label htmlFor="doc-paste">Or paste text</label>
      <textarea
        id="doc-paste"
        value={pasted}
        placeholder="Paste the job description or company notes here..."
        onChange={(e) => setPasted(e.target.value)}
      />
      <button disabled={busy || !pasted.trim()} onClick={submitPaste}>
        Add pasted text
      </button>

      <ul className="list" style={{ marginTop: 16 }}>
        {documents.length === 0 && <p className="muted">No documents yet.</p>}
        {documents.map((doc) => (
          <li key={doc.id} style={{ cursor: "default" }}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div>
                <div>{doc.filename ?? `${LABELS[doc.doc_type]} (pasted)`}</div>
                <div className="meta">
                  {LABELS[doc.doc_type]} · {doc.chunk_count} chunks
                </div>
              </div>
              <div className="row">
                <span className={`badge ${doc.index_status}`}>{doc.index_status}</span>
                {doc.index_status === "failed" && (
                  <button className="secondary" onClick={() => reindex(doc.id)}>
                    Retry
                  </button>
                )}
                <button className="danger" onClick={() => remove(doc.id)}>
                  Remove
                </button>
              </div>
            </div>
            {doc.index_error && <div className="notice error">{doc.index_error}</div>}
          </li>
        ))}
      </ul>
    </div>
  );
}
