import { Button, Callout, Intent, Menu, MenuItem, Popover, Spinner, Tag } from "@blueprintjs/core";
import { useCallback, useRef, useState } from "react";

import { LANGUAGE_LABELS, translateTranscript, type Language } from "../lib/api";
import Explain from "./Explain";

/**
 * Reading a transcript in a language you do not speak.
 *
 * This is content translation, not UI translation: the app's own chrome stays English,
 * and what changes is the candidate's and interviewer's words. There is deliberately no
 * i18n library involved.
 *
 * Two rules the shape of this encodes:
 *
 *  - The original is never thrown away. `translations` is an overlay keyed by turn id,
 *    so a reviewer can flip back, and a turn with no translation (an empty one, or one
 *    added after the last fetch) falls through to its own text rather than vanishing.
 *  - A failed translation is stated, not hidden. The backend answers 200 with
 *    `translated: false` when the provider is unreachable, because the useful outcome is
 *    the original transcript plus a banner - not a broken panel.
 *
 * Translations are cached server-side per (turn, language), so re-selecting a language
 * costs nothing; this hook also keeps what it has already fetched in memory so switching
 * back and forth does not even make the request.
 */

const LANGUAGES: Language[] = ["en", "id", "zh"];

export interface TranscriptTranslationState {
  /** Null means "show the original". */
  target: Language | null;
  translations: Record<string, string>;
  loading: boolean;
  /** Set when the provider could not be reached; the text on screen is the original. */
  failure: string | null;
  select: (target: Language | null) => void;
}

export function useTranscriptTranslation(
  sessionId: string | null,
): TranscriptTranslationState {
  const [target, setTarget] = useState<Language | null>(null);
  const [translations, setTranslations] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // Per-language results already fetched in this session, so toggling back to a language
  // is instant and free rather than a round trip that returns the same rows.
  const cache = useRef<Record<string, Record<string, string>>>({});

  const select = useCallback(
    async (next: Language | null) => {
      setFailure(null);
      setTarget(next);
      if (next === null || !sessionId) {
        setTranslations({});
        return;
      }
      const cached = cache.current[next];
      if (cached) {
        setTranslations(cached);
        return;
      }
      setLoading(true);
      try {
        const result = await translateTranscript(sessionId, next);
        if (!result.translated) {
          setFailure(result.detail ?? "Translation is unavailable right now.");
          setTranslations({});
          setTarget(null);
          return;
        }
        cache.current[next] = result.translations;
        setTranslations(result.translations);
      } catch (err) {
        setFailure(err instanceof Error ? err.message : "Could not translate this transcript");
        setTranslations({});
        setTarget(null);
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  return { target, translations, loading, failure, select };
}

/** The control that belongs in a transcript Section's `rightElement`. */
export function TranslateControl({
  state,
  sourceLanguage,
  disabled,
}: {
  state: TranscriptTranslationState;
  /** The interview's own language, so the menu can mark which one is the original. */
  sourceLanguage?: string;
  disabled?: boolean;
}) {
  const { target, loading, select } = state;

  return (
    <Popover
      minimal
      placement="bottom-end"
      disabled={disabled || loading}
      content={
        <Menu>
          <MenuItem
            text="Original"
            labelElement={
              sourceLanguage && sourceLanguage in LANGUAGE_LABELS
                ? LANGUAGE_LABELS[sourceLanguage as Language]
                : undefined
            }
            icon={target === null ? "tick" : "blank"}
            onClick={() => select(null)}
          />
          {LANGUAGES.filter((l) => l !== sourceLanguage).map((lang) => (
            <MenuItem
              key={lang}
              text={LANGUAGE_LABELS[lang]}
              icon={target === lang ? "tick" : "blank"}
              onClick={() => select(lang)}
            />
          ))}
        </Menu>
      }
    >
      <Explain
        bare
        text="Translate the transcript for reading. The original is always one click away, and what the interviewer actually said is unchanged."
      >
        <Button
          minimal
          small
          icon={loading ? undefined : "translate"}
          rightIcon="caret-down"
          disabled={disabled}
          text={target ? LANGUAGE_LABELS[target] : "Translate"}
        >
          {loading && <Spinner size={12} />}
        </Button>
      </Explain>
    </Popover>
  );
}

/** Banner shown above a transcript when translation failed or is active. */
export function TranslateNotice({ state }: { state: TranscriptTranslationState }) {
  if (state.failure) {
    return (
      <Callout intent={Intent.WARNING} icon="translate" compact style={{ marginBottom: 8 }}>
        Showing the original — {state.failure}
      </Callout>
    );
  }
  if (!state.target) return null;
  return (
    <Callout intent={Intent.PRIMARY} icon="translate" compact style={{ marginBottom: 8 }}>
      Machine-translated into {LANGUAGE_LABELS[state.target]}. Scoring and evidence always
      quote the original.{" "}
      <Tag minimal round>
        {Object.keys(state.translations).length} turns
      </Tag>
    </Callout>
  );
}
