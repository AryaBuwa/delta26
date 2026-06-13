import type { Metadata } from 'next'

export const metadata: Metadata = {
  title: 'Credits — DEΔTA',
  description: 'Data sources, libraries, and attribution for DEΔTA.',
}

const sources = [
  { name: 'FIFA.com',              url: 'https://www.fifa.com',                          note: 'Official tournament data' },
  { name: 'BBC Sport',             url: 'https://www.bbc.com/sport/football',             note: 'Live text commentary' },
  { name: 'ESPN FC',               url: 'https://www.espn.com/soccer',                   note: 'Live updates' },
  { name: 'Reuters Sports',        url: 'https://www.reuters.com/sports/soccer',          note: 'Wire agency coverage' },
  { name: 'AP News Sports',        url: 'https://apnews.com/sports/soccer',              note: 'Wire agency coverage' },
  { name: 'Sofascore',             url: 'https://www.sofascore.com',                     note: 'Live events, detailed stats' },
  { name: 'FlashScore',            url: 'https://www.flashscore.com',                    note: 'Live scores' },
  { name: 'FotMob',                url: 'https://www.fotmob.com',                        note: 'Live match data' },
  { name: 'LiveScore',             url: 'https://www.livescore.com',                     note: 'Live scores' },
  { name: 'WhoScored',             url: 'https://www.whoscored.com',                     note: 'Match statistics' },
  { name: 'Sky Sports',            url: 'https://www.skysports.com/football',            note: 'Match coverage' },
  { name: 'The Guardian',          url: 'https://www.theguardian.com/football',          note: 'Live blogs' },
  { name: 'Goal.com',              url: 'https://www.goal.com',                          note: 'Match updates' },
  { name: 'CBS Sports',            url: 'https://www.cbssports.com/soccer',              note: 'Live match tracker' },
  { name: 'AS English',            url: 'https://en.as.com/soccer',                     note: 'European perspective' },
  { name: 'Sportstar',             url: 'https://sportstar.thehindu.com/football',       note: 'The Hindu sports coverage' },
  { name: 'Indian Express Sports', url: 'https://indianexpress.com/sports/football',     note: 'Indian sports coverage' },
  { name: 'NDTV Sports',           url: 'https://sports.ndtv.com/football',              note: 'Indian sports coverage' },
]

const tech = [
  { name: 'Groq',       url: 'https://groq.com',                     note: 'LLM inference — llama-3.1' },
  { name: 'Vercel',     url: 'https://vercel.com',                   note: 'Frontend hosting' },
  { name: 'Render',     url: 'https://render.com',                   note: 'Backend hosting' },
  { name: 'Tavily',     url: 'https://tavily.com',                   note: 'Search fallback' },
  { name: 'Next.js',    url: 'https://nextjs.org',                   note: 'Frontend framework' },
  { name: 'FastAPI',    url: 'https://fastapi.tiangolo.com',         note: 'Backend framework' },
  { name: 'XGBoost',    url: 'https://xgboost.readthedocs.io',       note: 'ML model' },
  { name: 'MLflow',     url: 'https://mlflow.org',                   note: 'Experiment tracking' },
  { name: 'DVC',        url: 'https://dvc.org',                      note: 'Model versioning' },
  { name: 'StatsBomb',  url: 'https://statsbomb.com',                note: 'Open match data' },
]

function CreditTable({ items, title }: { items: typeof sources; title: string }) {
  return (
    <div>
      <h2 style={{
        fontFamily: 'var(--font-mono)',
        fontSize: 9,
        letterSpacing: '0.16em',
        textTransform: 'uppercase',
        color: 'var(--text-dim)',
        marginBottom: 16,
      }}>
        {title}
      </h2>
      <div style={{ display: 'flex', flexDirection: 'column' }}>
        {items.map(({ name, url, note }) => (
          <div
            key={name}
            className="flex items-baseline gap-3"
            style={{ padding: '8px 0', borderBottom: '1px solid var(--line-dim)' }}
          >
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontFamily: 'var(--font-mono)',
                fontSize: 11,
                color: 'var(--accent-blue)',
                textDecoration: 'none',
                minWidth: 160,
              }}
            >
              {name}
            </a>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>{note}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function CreditsPage() {
  return (
    <div className="page" style={{ paddingTop: 40, paddingBottom: 64, maxWidth: 640 }}>
      <div style={{ marginBottom: 32 }}>
        <h1 style={{
          fontFamily: 'var(--font-sans)',
          fontSize: 28,
          fontWeight: 800,
          letterSpacing: '-0.03em',
          color: 'var(--text-primary)',
          marginBottom: 8,
        }}>
          Credits
        </h1>
        <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7, maxWidth: 480 }}>
          All match commentary and news is fetched from publicly available sources in real time.
          AI processing extracts structured match events. No article text is stored or reproduced.
          Source links are credited and displayed for full article access.
        </p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 40 }}>
        <CreditTable items={sources} title="Data sources (18)" />
        <CreditTable items={tech} title="Technology" />
      </div>

      <div style={{
        marginTop: 40,
        padding: '16px 20px',
        border: '1px solid var(--line-dim)',
        borderRadius: 4,
      }}>
        <p style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 9,
          color: 'var(--text-dim)',
          letterSpacing: '0.08em',
          lineHeight: 1.9,
        }}>
          This project claims no ownership over any match data, player data, or news content.
          Built for research and learning purposes only. Not affiliated with FIFA or UEFA.
        </p>
      </div>
    </div>
  )
}