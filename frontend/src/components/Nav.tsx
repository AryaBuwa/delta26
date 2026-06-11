'use client'
// components/Nav.tsx
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { cn } from '@/lib/utils'

export function Nav() {
  const path = usePathname()

  return (
    <nav className="nav">
      <div className="page flex items-center justify-between" style={{ height: 52 }}>
        {/* Wordmark */}
        <Link href="/" style={{ textDecoration: 'none' }}>
          <span style={{
            fontFamily: 'var(--font-sans)',
            fontSize: 20,
            fontWeight: 800,
            letterSpacing: '-0.04em',
            color: 'var(--text-primary)',
          }}>
            DE<span style={{ color: 'var(--accent)' }}>Δ</span>TA
          </span>
        </Link>

        {/* Right side */}
        <div className="flex items-center gap-5">
          <Link
            href="/about"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              textDecoration: 'none',
              color: path === '/about' ? 'var(--text-primary)' : 'var(--text-dim)',
            }}
          >
            About
          </Link>
          <Link
            href="/credits"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              textDecoration: 'none',
              color: path === '/credits' ? 'var(--text-primary)' : 'var(--text-dim)',
            }}
          >
            Credits
          </Link>
          <a
            href="https://buymeacoffee.com"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.1em',
              color: 'var(--accent)',
              textDecoration: 'none',
              border: '1px solid rgba(232,255,71,0.25)',
              padding: '4px 10px',
              borderRadius: 'var(--radius-sm)',
            }}
          >
            ☕
          </a>
        </div>
      </div>
    </nav>
  )
}
