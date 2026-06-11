// app/about/page.tsx
import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'About — DEΔTA',
  description: 'How the AI prediction system works. Research methodology for FIFA World Cup 2026.',
}

export default function AboutPage() {
  const sections = [
    {
      title: 'The question',
      body: `Does a machine that learns from every match of a tournament get better at predicting outcomes than humans watching the same matches? The interesting part isn't who wins. It's the gap between statistical pattern recognition and human intuition — and whether that gap closes, widens, or inverts as the tournament progresses.`,
    },
    {
      title: 'The system',
      body: `DEΔTA is a live AI research system running across all 104 FIFA World Cup 2026 matches. It fetches real-time data from 18 public sources, parses match events using Groq LLMs, and updates a Dixon-Coles + XGBoost ensemble model after every match. The model learns. The predictions change. The gap is measured.`,
    },
    {
      title: 'Your vote',
      body: `Each prediction you make is collected anonymously and compared to the AI's prediction at the same moment. Votes are time-stamped, scored for bot likelihood, and archived. After the tournament, the full dataset will be analysed for the research paper.`,
    },
    {
      title: 'The model',
      body: `Dixon-Coles statistical model (baseline) + Monte Carlo simulation (10,000 runs per match) + XGBoost contextual features. Retrained manually after every match using accumulated tournament data. Model version shown on every prediction. Confidence shown as a range — never 0% or 100%.`,
    },
  ]

  return (
    <div className="page" style={{ paddingTop: 40, paddingBottom: 64, maxWidth: 640 }}>
      <div style={{ marginBottom: 32 }}>
        <h1 style={{ fontFamily: 'var(--font-sans)', fontSize: 28, fontWeight: 800, letterSpacing: '-0.03em', color: 'var(--text-primary)', marginBottom: 8 }}>
          How it works
        </h1>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
          DEΔTA · AI Research System · FIFA World Cup 2026
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        {sections.map(({ title, body }) => (
          <div key={title} style={{ borderLeft: '2px solid var(--line-soft)', paddingLeft: 20 }}>
            <h2 style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--accent)', marginBottom: 8 }}>
              {title}
            </h2>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.7 }}>
              {body}
            </p>
          </div>
        ))}
      </div>

      <div style={{ marginTop: 40, padding: '16px 20px', border: '1px solid var(--line-dim)', borderRadius: 4 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.08em', lineHeight: 1.9 }}>
          This is an independent research project for educational purposes.
          Not affiliated with FIFA or UEFA. No prizes, gambling, or real money involved.
          All match data sourced from public sources. AI-generated content is clearly labelled.
          All rights remain with respective data owners.
        </p>
      </div>
    </div>
  )
}
