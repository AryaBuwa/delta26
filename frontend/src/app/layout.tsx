import type { Metadata } from 'next'
import './globals.css'
import { Toaster } from 'react-hot-toast'
import { Nav } from '@/components/Nav'

export const metadata: Metadata = {
  title: 'DEΔTA — AI vs Humans · FIFA World Cup 2026',
  description: 'An AI that experiences the World Cup in real time. Who predicts better — the algorithm or the crowd? Vote and find out.',
  openGraph: {
    title: 'DEΔTA — AI vs Humans · FIFA World Cup 2026',
    description: 'Real-time AI prediction research across 104 World Cup matches.',
    type: 'website',
  },
  robots: { index: true, follow: true },
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Sora:wght@300;400;600;700;800&display=swap" rel="stylesheet" />
        <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1" />
      </head>
      <body>
        <Nav />
        <main>{children}</main>
        <footer style={{
          borderTop: '1px solid var(--line-dim)',
          padding: '24px 16px',
          marginTop: 64,
          textAlign: 'center',
        }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', letterSpacing: '0.08em', lineHeight: 2 }}>
            Independent research project for educational purposes.
            Not affiliated with FIFA or UEFA. No prizes, gambling, or real money involved.
            All match data sourced from public sources. AI-generated content is clearly labelled.
            <br />
            <a href="/credits" style={{ color: 'var(--text-dim)', textDecoration: 'underline' }}>Credits</a>
            {' · '}
            <a href="/about" style={{ color: 'var(--text-dim)', textDecoration: 'underline' }}>About</a>
            {' · '}
            <a href="/privacy" style={{ color: 'var(--text-dim)', textDecoration: 'underline' }}>Privacy</a>
            {' · '}
            <a href="https://github.com/aryabuwa" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--text-dim)', textDecoration: 'underline' }}>GitHub</a>
            {' · '}
            <a href="https://buymeacoffee.com" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent)', textDecoration: 'none' }}>☕ Support</a>
          </p>
        </footer>
        <Toaster
          position="bottom-center"
          toastOptions={{
            style: {
              background: 'var(--bg-overlay)',
              color: 'var(--text-primary)',
              border: '1px solid var(--line-soft)',
              fontFamily: 'var(--font-mono)',
              fontSize: 12,
              letterSpacing: '0.04em',
            },
          }}
        />
      </body>
    </html>
  )
}
