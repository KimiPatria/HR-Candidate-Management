import {
  Button,
  Callout,
  FileInput,
  Intent,
  Spinner,
  Tab,
  Tabs,
  TextArea,
} from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";

import {
  getIntegrations,
  importDriveDocument,
  pasteDocument,
  uploadDocument,
  type DocumentScope,
  type Integrations,
} from "../lib/api";
import {
  DriveCancelled,
  pickFromGoogleDrive,
  pickFromOneDrive,
  type PickedFile,
} from "../lib/drive";

/**
 * The three ways a document gets in: paste it, upload it, or pick it out of a drive.
 *
 * One at a time, behind tabs, rather than all three stacked. Three input surfaces in a
 * column read as three things to fill in; a tab strip reads as one choice, which is what
 * it actually is.
 */

const ACCEPT = ".pdf,.docx,.txt,.md,.csv";

type SourceId = "paste" | "upload" | "drive";

export default function DocumentSources({
  scope,
  onAdded,
  pasteLabel = "Paste the job description here...",
}: {
  scope: DocumentScope;
  onAdded: () => void;
  pasteLabel?: string;
}) {
  const [tab, setTab] = useState<SourceId>("paste");
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  async function run(work: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await work();
      onAdded();
    } catch (err) {
      // A user closing a drive picker chose not to add a file. That is not an error and
      // must not raise a red banner over a page they were only browsing.
      if (err instanceof DriveCancelled) return;
      setError(err instanceof Error ? err.message : "Could not add that document");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="doc-sources">
      {error && (
        <Callout intent={Intent.DANGER} icon="error" compact style={{ marginBottom: 12 }}>
          {error}
        </Callout>
      )}

      <Tabs
        id="doc-sources"
        selectedTabId={tab}
        onChange={(id) => setTab(id as SourceId)}
        animate={false}
      >
        <Tab
          id="paste"
          title="Paste text"
          icon="clipboard"
          panel={
            <div className="stack-tight">
              <TextArea
                value={pasted}
                fill
                style={{
                  minHeight: 150,
                  fontFamily: "var(--bp-font-family-monospace, monospace)",
                }}
                placeholder={pasteLabel}
                onChange={(e) => setPasted(e.target.value)}
              />
              <Button
                intent={Intent.PRIMARY}
                icon="add"
                text="Add text"
                loading={busy}
                disabled={!pasted.trim()}
                onClick={() =>
                  void run(async () => {
                    await pasteDocument(scope, pasted);
                    setPasted("");
                  })
                }
              />
            </div>
          }
        />

        <Tab
          id="upload"
          title="Upload file"
          icon="cloud-upload"
          panel={
            <div className="stack-tight">
              <FileInput
                fill
                disabled={busy}
                hasSelection={fileName !== null}
                text={fileName ?? "Choose a file..."}
                buttonText="Browse"
                inputProps={{ accept: ACCEPT, ref: fileInput }}
                onInputChange={(e) => {
                  const file = e.currentTarget.files?.[0];
                  if (!file) return;
                  setFileName(file.name);
                  void run(async () => {
                    await uploadDocument(scope, file);
                    if (fileInput.current) fileInput.current.value = "";
                    setFileName(null);
                  });
                }}
              />
              <div className="bp6-text-muted bp6-text-small">
                PDF, Word, or plain text. A scanned PDF with no selectable text will be
                rejected — run it through OCR, or paste the text instead.
              </div>
            </div>
          }
        />

        <Tab
          id="drive"
          title="From a drive"
          icon="cloud"
          panel={
            <DriveTab
              busy={busy}
              // The pick itself runs inside `run` so that a closed picker, a refused
              // download and a rejected file all land in the same place - outside it,
              // a cancellation would surface as an unhandled rejection in the console
              // and nothing at all on screen.
              onPick={(pick) =>
                void run(async () => {
                  const picked = await pick();
                  await importDriveDocument(
                    scope,
                    picked.provider,
                    picked.file,
                    picked.sourceUrl,
                  );
                })
              }
            />
          }
        />
      </Tabs>
    </div>
  );
}

/**
 * Google Drive and OneDrive, or an explanation of what is missing.
 *
 * Which drives appear is decided by the server (see api/integrations.py): a drive with
 * no client id configured is absent rather than present and broken, because a button
 * that opens a picker and fails is worse than no button.
 */
function DriveTab({
  busy,
  onPick,
}: {
  busy: boolean;
  /** Handed the pick as a thunk rather than its result, so the caller owns the whole
   *  attempt - popup, download and upload - under one error boundary. */
  onPick: (pick: () => Promise<PickedFile>) => void;
}) {
  const [config, setConfig] = useState<Integrations | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getIntegrations()
      .then((c) => !cancelled && setConfig(c))
      .catch(() => !cancelled && setFailed(true));
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <Callout intent={Intent.WARNING} icon="offline" compact>
        Could not check which drives are connected. Upload the file or paste its text
        instead.
      </Callout>
    );
  }
  if (!config) {
    return <Spinner size={20} />;
  }

  const google = config.google_drive;
  const onedrive = config.onedrive;

  if (!google.enabled && !onedrive.enabled) {
    return (
      <Callout icon="cog" title="No drives connected" compact>
        Neither Google Drive nor OneDrive is set up for this deployment. Add
        GOOGLE_CLIENT_ID, GOOGLE_API_KEY and GOOGLE_APP_ID, or MICROSOFT_CLIENT_ID, to the
        backend environment to turn this on. Until then, upload the file or paste its
        text.
      </Callout>
    );
  }

  return (
    <div className="stack-tight">
      <div className="row">
        {google.enabled && (
          <Button
            icon="cloud"
            text="Google Drive"
            loading={busy}
            onClick={() =>
              onPick(() =>
                pickFromGoogleDrive({
                  clientId: google.client_id,
                  apiKey: google.api_key,
                  appId: google.app_id,
                }),
              )
            }
          />
        )}
        {onedrive.enabled && (
          <Button
            icon="cloud"
            text="OneDrive"
            loading={busy}
            onClick={() => onPick(() => pickFromOneDrive({ clientId: onedrive.client_id }))}
          />
        )}
      </div>
      <div className="bp6-text-muted bp6-text-small">
        You sign in to the drive yourself and pick one file. Only that file is read, and
        it is copied here — the original is left where it is. Google Docs, Sheets and
        Slides are converted to text on the way in.
      </div>
    </div>
  );
}
