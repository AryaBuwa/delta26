import type { Metadata } from 'next'

export const metadata: Metadata = { title: 'Privacy — DEΔTA' }

export default function PrivacyPage() {
  return (
    <div className="page" style={{ paddingTop: 40, paddingBottom: 64, maxWidth: 600 }}>
      <h1 style={{ fontFamily: 'var(--font-sans)', fontSize: 28, fontWeight: 800, letterSpacing: '-0.03em', marginBottom: 32 }}>
        Privacy
      </h1>
      {[
        ['What we collect', 'Browser fingerprint hash (not raw — one-way hash only), vote picks, timestamps, session ID. No name, email, or account required.'],
        ['What we do not collect', 'No personal data. No IP addresses stored beyond rate limiting. No tracking pixels. No third-party analytics. No cookies beyond session.'],
        ['Votes', 'Each vote is stored with a hashed fingerprint to prevent duplicates. The raw fingerprint is never stored. Votes are used solely for research analysis.'],
        ['Research data', 'Anonymised vote data may be published after the tournament as part of an academic research dataset. All fingerprint data is hashed before any export.'],
        ['Bot prevention', 'reCAPTCHA v3 runs silently in the background to score requests. No personal data stored beyond reCAPTCHA\'s own privacy policy.'],
        ['Data retention', 'Vote data retained for the duration of the research study. Session data cleared on browser close.'],
        ['Contact', 'Independent research project. For data enquiries, contact via GitHub.'],
      ].map(([title, body]) => (
        <div key={title as string} style={{ marginBottom: 24, borderLeft: '2px solid var(--line-soft)', paddingLeft: 16 }}>
          <h2 style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 6 }}>
            {title}
          </h2>
          <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7 }}>{body}</p>
        </div>
      ))}
    </div>
  )
}
