import {
  AnchorButton,
  Button,
  ButtonGroup,
  Callout,
  Dialog,
  DialogBody,
  DialogFooter,
  HTMLSelect,
  Intent,
  NonIdealState,
  Spinner,
  Tag,
} from "@blueprintjs/core";
import { useCallback, useEffect, useState } from "react";

import {
  deleteDocument,
  documentDownloadUrl,
  getDocumentTranslation,
  listDocuments,
  LANGUAGE_LABELS,
  readDocument,
  reindexDocument,
  translateDocument,
  type DocumentContent,
  type DocumentScope,
  type InterviewDocument,
  type Language,
} from "../lib/api";
import { statusIcon, statusIntent, statusLabel } from "../lib/status";

/**
 * What has been added, and what it says.
 *
 * The second half is the point. Indexing is not a shredder: the extracted text stays on
 * the row, and this is what lets somebody coming back to an interview weeks later read
 * the job description the questions were generated from instead of inferring it from the
 * questions. Every document opens, whatever state it is in - including a failed one,
 * where seeing the three characters that came out of a scanned PDF is the diagnosis.
 */

const SOURCE_LABELS: Record<string, string> = {
  upload: "Uploaded file",
  pasted: "Pasted text",
  google_drive: "Google Drive",
  onedrive: "OneDrive",
};

const SOURCE_ICONS: Record<string, "document" | "clipboard" | "cloud"> = {
  upload: "document",
  pasted: "clipboard",
  google_drive: "cloud",
  onedrive: "cloud",
};

const TRANSLATABLE: Language[] = ["en", "id", "zh"];

/** Poll while anything is still indexing - it runs as a background task on the server. */
export function useDocuments(scope: DocumentScope) {
  const [documents, setDocuments] = useState<InterviewDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setDocuments(await listDocuments(scope));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load documents");
    }
    // `scope` is a fresh object on every render at some call sites, so depend on the
    // one field that actually identifies it rather than on identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope.base]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const settling = (documents ?? []).some(
    (d) => d.index_status === "pending" || d.index_status === "indexing",
  );
  useEffect(() => {
    if (!settling) return;
    const timer = setInterval(() => void reload(), 1500);
    return () => clearInterval(timer);
  }, [settling, reload]);

  return { documents, error, settling, reload };
}

export default function DocumentList({
  scope,
  documents,
  language,
  onChange,
  emptyDescription,
}: {
  scope: DocumentScope;
  documents: InterviewDocument[];
  /** The language this material is presumably already in - it is offered translation
   *  into the others, never into itself. */
  language: string;
  onChange: () => void;
  emptyDescription: string;
}) {
  const [reading, setReading] = useState<DocumentContent | null>(null);
  const [opening, setOpening] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (documents.length === 0) {
    return (
      <NonIdealState icon="document-open" title="Nothing added yet" description={emptyDescription} />
    );
  }

  async function open(documentId: string) {
    setOpening(documentId);
    setError(null);
    try {
      setReading(await readDocument(scope, documentId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open that document");
    } finally {
      setOpening(null);
    }
  }

  return (
    <>
      {error && (
        <Callout intent={Intent.DANGER} icon="error" compact style={{ margin: "12px 16px" }}>
          {error}
        </Callout>
      )}
      <div>
        {documents.map((doc) => (
          <div key={doc.id} className="doc-row">
            <div className="doc-row-main">
              <div className="doc-row-title">
                {doc.filename ?? "Pasted text"}
              </div>
              <div className="bp6-text-muted bp6-text-small row" style={{ gap: 6 }}>
                <Tag minimal icon={SOURCE_ICONS[doc.source] ?? "document"}>
                  {SOURCE_LABELS[doc.source] ?? doc.source}
                </Tag>
                <span>
                  {doc.char_count.toLocaleString()} characters · {doc.chunk_count} chunks
                </span>
              </div>
            </div>
            <div className="row" style={{ gap: 4 }}>
              <Tag minimal intent={statusIntent(doc.index_status)} icon={statusIcon(doc.index_status)}>
                {statusLabel(doc.index_status)}
              </Tag>
              <ButtonGroup>
                <Button
                  size="small"
                  icon="eye-open"
                  text="View source"
                  loading={opening === doc.id}
                  onClick={() => void open(doc.id)}
                />
                {doc.index_status === "failed" && (
                  <Button
                    size="small"
                    icon="refresh"
                    text="Retry"
                    onClick={async () => {
                      await reindexDocument(scope, doc.id);
                      onChange();
                    }}
                  />
                )}
                <Button
                  size="small"
                  intent={Intent.DANGER}
                  icon="trash"
                  aria-label={`Remove ${doc.filename ?? "pasted text"}`}
                  onClick={async () => {
                    await deleteDocument(scope, doc.id);
                    onChange();
                  }}
                />
              </ButtonGroup>
            </div>
            {doc.index_error && (
              <Callout intent={Intent.DANGER} compact style={{ marginTop: 8, gridColumn: "1 / -1" }}>
                {doc.index_error}
              </Callout>
            )}
          </div>
        ))}
      </div>

      <SourceDialog
        scope={scope}
        document={reading}
        language={language}
        onClose={() => setReading(null)}
      />
    </>
  );
}

/**
 * One document, as it was ingested.
 *
 * The language select translates in place rather than opening a second window: a
 * reviewer checking whether an Indonesian job description says what the English
 * questions assume is comparing two readings of one document, not reading two documents.
 * Translations are cached server-side per (document, language), so re-opening one that
 * has already been translated costs nothing.
 */
function SourceDialog({
  scope,
  document: doc,
  language,
  onClose,
}: {
  scope: DocumentScope;
  document: DocumentContent | null;
  language: string;
  onClose: () => void;
}) {
  const [shown, setShown] = useState<string>("original");
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset to the original whenever a different document is opened, so a language chosen
  // for one document does not silently follow the reader to the next.
  useEffect(() => {
    setShown("original");
    setText(null);
    setError(null);
  }, [doc?.id]);

  async function show(target: string) {
    setShown(target);
    setError(null);
    if (target === "original" || !doc) {
      setText(null);
      return;
    }
    setBusy(true);
    try {
      // Read-through: the cached translation first - including one produced automatically
      // at ingest - and only pay for a new one when there is nothing to read.
      let row;
      try {
        row = await getDocumentTranslation(doc.id, target as Language);
      } catch {
        row = await translateDocument(doc.id, target as Language);
      }
      setText(row.text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not translate this document");
      setShown("original");
    } finally {
      setBusy(false);
    }
  }

  const options = [
    { value: "original", label: "Original" },
    ...TRANSLATABLE.filter((l) => l !== language).map((l) => ({
      value: l,
      label: `Read in ${LANGUAGE_LABELS[l]}`,
    })),
  ];

  return (
    <Dialog
      isOpen={doc !== null}
      onClose={onClose}
      title={doc?.filename ?? "Pasted text"}
      icon={SOURCE_ICONS[doc?.source ?? "pasted"] ?? "document"}
      style={{ width: 760 }}
    >
      <DialogBody>
        <div className="row row-between" style={{ marginBottom: 12 }}>
          <div className="row" style={{ gap: 6 }}>
            <Tag minimal>{SOURCE_LABELS[doc?.source ?? ""] ?? doc?.source}</Tag>
            <Tag minimal icon="search-around">
              {doc?.chunk_count} indexed chunks
            </Tag>
          </div>
          <HTMLSelect
            value={shown}
            options={options}
            disabled={busy}
            onChange={(e) => void show(e.currentTarget.value)}
          />
        </div>

        {error && (
          <Callout intent={Intent.DANGER} icon="error" compact style={{ marginBottom: 12 }}>
            {error}
          </Callout>
        )}
        {shown !== "original" && !busy && text && (
          <Callout intent={Intent.PRIMARY} icon="translate" compact style={{ marginBottom: 12 }}>
            Machine translation, shown for reading. The interviewer works from the
            original.
          </Callout>
        )}

        {busy ? (
          <NonIdealState icon={<Spinner />} title="Translating" />
        ) : (
          <div className="doc-source-text">{text ?? doc?.content_text}</div>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            {doc?.source_url && (
              <AnchorButton
                icon="share"
                text="Open in drive"
                href={doc.source_url}
                target="_blank"
                rel="noreferrer"
              />
            )}
            {doc?.has_original_file && (
              <AnchorButton
                icon="download"
                text="Download original"
                href={documentDownloadUrl(scope, doc.id)}
              />
            )}
            <Button intent={Intent.PRIMARY} text="Close" onClick={onClose} />
          </>
        }
      />
    </Dialog>
  );
}
