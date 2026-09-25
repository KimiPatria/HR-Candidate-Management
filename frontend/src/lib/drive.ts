/**
 * Picking a file out of Google Drive or OneDrive without leaving the app.
 *
 * The shape is the same for both drives, and the important part of it is where the
 * download happens: HERE, in the browser, against the drive's own API, with a token the
 * user has just consented to. The bytes then go up to our backend as an ordinary file
 * upload.
 *
 * The obvious alternative - send the file's URL and an access token to the backend and
 * let it fetch - is worse twice over. It puts a user's drive credential in our process
 * and our logs, and it turns our server into something that will fetch a URL a client
 * chose, which is a request-forgery primitive pointed at our own network. Neither is
 * worth the round trip it saves.
 *
 * Both SDKs are loaded on demand, the first time somebody actually opens a picker, so a
 * deployment with no drive configured ships none of this.
 */

import type { DriveProvider } from "./api";

/** A file the user picked, already downloaded and ready to upload. */
export interface PickedFile {
  provider: DriveProvider;
  file: File;
  /** Where the file lives in the drive, so the document list can link back to it. */
  sourceUrl: string;
}

export class DriveCancelled extends Error {
  constructor() {
    super("Cancelled");
    this.name = "DriveCancelled";
  }
}

/** What the backend can extract text from. Anything else is refused before the download
 *  rather than after, so the user finds out while the picker is still on screen. */
const READABLE_EXTENSIONS = [".pdf", ".docx", ".txt", ".md", ".csv"];

const UNREADABLE =
  "That file type cannot be read. Pick a PDF, Word document, or plain text file " +
  "(.pdf, .docx, .txt, .md, .csv), or a Google Doc.";

function assertReadable(name: string): void {
  const lower = name.toLowerCase();
  if (!READABLE_EXTENSIONS.some((ext) => lower.endsWith(ext))) {
    throw new Error(UNREADABLE);
  }
}

const loaded = new Map<string, Promise<void>>();

/** Load a third-party script once per page, no matter how many callers ask. */
function loadScript(src: string): Promise<void> {
  const existing = loaded.get(src);
  if (existing) return existing;
  const promise = new Promise<void>((resolve, reject) => {
    const el = document.createElement("script");
    el.src = src;
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => {
      // Let a failed load be retried: a network blip should not disable the button for
      // the rest of the session.
      loaded.delete(src);
      reject(new Error(`Could not load ${src}`));
    };
    document.head.appendChild(el);
  });
  loaded.set(src, promise);
  return promise;
}

// --------------------------------------------------------------------- Google Drive

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    google?: any;
    gapi?: any;
    OneDrive?: any;
  }
}

const GSI_SRC = "https://accounts.google.com/gsi/client";
const GAPI_SRC = "https://apis.google.com/js/api.js";
/*
 * Per-file access, not read-everything.
 *
 * `drive.file` grants the app only the files the user actually picks - which is exactly
 * what this feature does - where `drive.readonly` would grant standing read access to
 * their entire Drive. That is a real difference in what an HR user is consenting to, and
 * it is also the difference between a client id that works after a five-minute setup and
 * one that needs Google's restricted-scope verification, including a third-party security
 * assessment, before anybody outside the test-user list can sign in.
 *
 * Files opened through the Picker fall under this scope, and it covers the download and
 * export calls below.
 */
const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file";

/**
 * Google's native formats are not files - a Doc has no bytes to download - so they are
 * exported on the way out. Everything here becomes something `extract.py` can read.
 */
const GOOGLE_EXPORTS: Record<string, { mimeType: string; extension: string }> = {
  "application/vnd.google-apps.document": { mimeType: "text/plain", extension: ".txt" },
  "application/vnd.google-apps.presentation": { mimeType: "text/plain", extension: ".txt" },
  "application/vnd.google-apps.spreadsheet": { mimeType: "text/csv", extension: ".csv" },
};

export interface GoogleConfig {
  clientId: string;
  apiKey: string;
  appId: string;
}

export async function pickFromGoogleDrive(config: GoogleConfig): Promise<PickedFile> {
  await Promise.all([loadScript(GSI_SRC), loadScript(GAPI_SRC)]);
  await new Promise<void>((resolve) => window.gapi.load("picker", () => resolve()));

  const token = await requestGoogleToken(config.clientId);
  const doc = await showGooglePicker(config, token);

  const isNative = doc.mimeType in GOOGLE_EXPORTS;
  const exportAs = GOOGLE_EXPORTS[doc.mimeType];
  const name = isNative ? `${doc.name}${exportAs.extension}` : doc.name;
  if (!isNative) assertReadable(name);

  const url = isNative
    ? `https://www.googleapis.com/drive/v3/files/${doc.id}/export?mimeType=${encodeURIComponent(exportAs.mimeType)}`
    : `https://www.googleapis.com/drive/v3/files/${doc.id}?alt=media&supportsAllDrives=true`;

  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) {
    throw new Error(`Google Drive refused the download (${res.status})`);
  }
  const blob = await res.blob();
  return {
    provider: "google_drive",
    file: new File([blob], name, { type: blob.type || "application/octet-stream" }),
    sourceUrl: doc.url ?? `https://drive.google.com/file/d/${doc.id}/view`,
  };
}

/**
 * An access token for this one pick, held in memory and never persisted.
 *
 * `requestAccessToken` shows Google's own consent popup. The token comes back to this
 * callback and goes no further: it is used for the single download below and then
 * dropped when this function's scope ends.
 */
function requestGoogleToken(clientId: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const client = window.google.accounts.oauth2.initTokenClient({
      client_id: clientId,
      scope: DRIVE_SCOPE,
      callback: (response: any) => {
        if (response.error) {
          reject(
            response.error === "access_denied"
              ? new DriveCancelled()
              : new Error(`Google sign-in failed: ${response.error}`),
          );
          return;
        }
        resolve(response.access_token);
      },
      error_callback: (err: any) => {
        // Closing the popup is a cancellation, not a failure - it must not raise a red
        // error banner on a page the user simply changed their mind about.
        reject(
          err?.type === "popup_closed" || err?.type === "popup_failed_to_open"
            ? new DriveCancelled()
            : new Error(err?.message ?? "Google sign-in failed"),
        );
      },
    });
    client.requestAccessToken();
  });
}

function showGooglePicker(config: GoogleConfig, token: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const { google } = window;
    const view = new google.picker.DocsView(google.picker.ViewId.DOCS)
      .setIncludeFolders(true)
      .setSelectFolderEnabled(false);

    new google.picker.PickerBuilder()
      .setAppId(config.appId)
      .setOAuthToken(token)
      .setDeveloperKey(config.apiKey)
      .addView(view)
      .addView(new google.picker.DocsUploadView())
      .setCallback((data: any) => {
        if (data.action === google.picker.Action.PICKED) {
          resolve(data.docs[0]);
        } else if (data.action === google.picker.Action.CANCEL) {
          reject(new DriveCancelled());
        }
      })
      .build()
      .setVisible(true);
  });
}

// ------------------------------------------------------------------------- OneDrive

const ONEDRIVE_SRC = "https://js.live.net/v7.2/OneDrive.js";

export interface OneDriveConfig {
  clientId: string;
}

export async function pickFromOneDrive(config: OneDriveConfig): Promise<PickedFile> {
  await loadScript(ONEDRIVE_SRC);

  const picked = await new Promise<any>((resolve, reject) => {
    window.OneDrive.open({
      clientId: config.clientId,
      action: "download",
      multiSelect: false,
      // "query" opens the user's own OneDrive; the picker offers SharePoint from there.
      advanced: { redirectUri: window.location.origin },
      success: (response: any) => {
        const file = response?.value?.[0];
        if (!file) {
          reject(new DriveCancelled());
          return;
        }
        resolve(file);
      },
      cancel: () => reject(new DriveCancelled()),
      error: (err: any) =>
        reject(new Error(err?.message ?? "OneDrive could not open the picker")),
    });
  });

  const name: string = picked.name ?? "onedrive-file";
  assertReadable(name);

  // A pre-authenticated, short-lived URL the picker hands back with the selection. No
  // header, no token of ours - which is the whole reason "download" is the action asked
  // for rather than "share".
  const downloadUrl: string | undefined =
    picked["@microsoft.graph.downloadUrl"] ?? picked["@content.downloadUrl"];
  if (!downloadUrl) {
    throw new Error("OneDrive did not return a download link for that file");
  }

  const res = await fetch(downloadUrl);
  if (!res.ok) {
    throw new Error(`OneDrive refused the download (${res.status})`);
  }
  const blob = await res.blob();
  return {
    provider: "onedrive",
    file: new File([blob], name, { type: blob.type || "application/octet-stream" }),
    sourceUrl: picked.webUrl ?? "",
  };
}
