import { Link } from "react-router-dom";
import { PublicLayout } from "../components/Layout";
import { LAST_UPDATED, PRIVACY, TERMS, type LegalDocument } from "../content/legal";

/**
 * The two documents §17.1 requires to be accepted at registration.
 *
 * Public routes with no auth guard, deliberately: someone deciding whether to
 * register cannot read terms that require an account, and a link on the
 * registration form that bounced to a login page would make the consent checkbox
 * ornamental. That is what it was before these pages existed — the checkbox named
 * two documents that had never been written.
 */

function Document({ document }: { document: LegalDocument }) {
  return (
    <article className="pb-4">
      <h1 className="text-2xl font-semibold tracking-tight">{document.title}</h1>
      <p className="mt-2 text-[15px] leading-relaxed text-ink-soft">{document.summary}</p>
      <p className="mt-1 text-[13px] text-ink-muted">Last updated {LAST_UPDATED}.</p>

      {document.sections.map((section) => (
        <section key={section.heading} className="mt-7">
          <h2 className="text-[15px] font-semibold">{section.heading}</h2>
          {section.paragraphs.map((paragraph) => (
            <p key={paragraph} className="mt-2 text-[14px] leading-relaxed text-ink-soft">
              {paragraph}
            </p>
          ))}
          {section.list && (
            <ul className="mt-2 list-disc space-y-1.5 pl-5 text-[14px] leading-relaxed text-ink-soft">
              {section.list.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </section>
      ))}

      <nav className="mt-8 flex gap-4 text-[14px]">
        <Link to="/terms" className="text-accent underline">
          Terms of Service
        </Link>
        <Link to="/privacy" className="text-accent underline">
          Privacy Policy
        </Link>
        <Link to="/register" className="text-ink-muted underline">
          Back to registration
        </Link>
      </nav>
    </article>
  );
}

export function TermsPage() {
  return (
    <PublicLayout wide>
      <Document document={TERMS} />
    </PublicLayout>
  );
}

export function PrivacyPage() {
  return (
    <PublicLayout wide>
      <Document document={PRIVACY} />
    </PublicLayout>
  );
}
