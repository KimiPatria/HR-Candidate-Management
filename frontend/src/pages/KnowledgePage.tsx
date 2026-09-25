import { Callout, Intent, Section, SectionCard, Spinner, Tag } from "@blueprintjs/core";

import DocumentList, { useDocuments } from "../components/DocumentList";
import DocumentSources from "../components/DocumentSources";
import { companyScope } from "../lib/api";

/**
 * The company knowledge base: what the interviewer knows about the business.
 *
 * Its own page because it moves on its own clock. A job description is written per
 * position and read once; what the company does, how the teams are organised, what the
 * benefits are, changes once or twice a year and applies to every candidate who ever
 * interviews. Keeping a copy of it inside each interview meant re-uploading the same
 * handbook per position and having nowhere to correct it once.
 */
export default function KnowledgePage() {
  const { documents, error, settling, reload } = useDocuments(companyScope);

  return (
    <div className="page-narrow stack">
      <header className="page-header">
        <h1>Company knowledge</h1>
        <p className="bp6-text-muted">
          Facts the interviewer may draw on with any candidate, for any role. Added once
          and shared by every interview — you do not need to repeat this per position.
        </p>
      </header>

      {error && (
        <Callout intent={Intent.DANGER} icon="error" title="Something went wrong">
          {error}
        </Callout>
      )}

      <Section title="Add to the knowledge base" icon="add">
        <SectionCard>
          <DocumentSources
            scope={companyScope}
            onAdded={reload}
            pasteLabel="Paste company background, team structure, benefits, values..."
          />
        </SectionCard>
      </Section>

      <Section
        title="In the knowledge base"
        icon="book"
        rightElement={
          settling ? (
            <Tag minimal intent={Intent.PRIMARY} icon="refresh">
              indexing
            </Tag>
          ) : documents?.length ? (
            <Tag minimal round>
              {documents.length}
            </Tag>
          ) : undefined
        }
      >
        <SectionCard padded={false}>
          {documents === null ? (
            <div style={{ padding: 32, textAlign: "center" }}>
              <Spinner size={24} />
            </div>
          ) : (
            <DocumentList
              scope={companyScope}
              documents={documents}
              // The knowledge base is not tied to any one interview's language, so
              // nothing is excluded from the translate menu here.
              language=""
              onChange={reload}
              emptyDescription="Add the company background the interviewer should be able to draw on. This is optional — interviews run without it."
            />
          )}
        </SectionCard>
      </Section>
    </div>
  );
}
