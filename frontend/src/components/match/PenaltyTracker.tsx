'use client'
// components/match/PenaltyTracker.tsx
import type { PenaltyState, Team } from '@/types'

interface Props {
  penaltyState: PenaltyState
  home: Team
  away: Team
}

function KickDot({ scored, taken }: { scored: boolean; taken: boolean }) {
  if (!taken) return (
    <div style={{
      width: 10, height: 10, borderRadius: '50%',
      border: '1px solid var(--line-soft)',
    }} />
  )
  return (
    <div style={{
      width: 10, height: 10, borderRadius: '50%',
      background: scored ? 'var(--accent-green)' : 'var(--accent-red)',
    }} />
  )
}

export function PenaltyTracker({ penaltyState, home, away }: Props) {
  const { kicks, home_scored, away_scored } = penaltyState
  const homeKicks = kicks.filter(k => k.team === 'home')
  const awayKicks = kicks.filter(k => k.team === 'away')
  const MAX = 5

  const renderDots = (teamKicks: typeof kicks) => {
    return Array.from({ length: MAX }, (_, i) => {
      const kick = teamKicks[i]
      return (
        <KickDot
          key={i}
          taken={!!kick}
          scored={kick?.scored ?? false}
        />
      )
    })
  }

  return (
    <div className="border-t px-5 py-4" style={{ borderColor: 'var(--line-dim)' }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 12 }}>
        Penalty Shootout
      </div>

      <div className="flex items-center justify-between gap-4">
        {/* Home */}
        <div className="flex flex-col gap-2 flex-1">
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{home.code}</span>
          <div className="flex gap-1.5">{renderDots(homeKicks)}</div>
        </div>

        {/* Score */}
        <div className="flex flex-col items-center gap-1">
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 500, letterSpacing: '-0.02em', color: 'var(--text-primary)', lineHeight: 1 }}>
            {home_scored} <span style={{ color: 'var(--text-dim)' }}>–</span> {away_scored}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--accent-red)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>Penalties</span>
        </div>

        {/* Away */}
        <div className="flex flex-col items-end gap-2 flex-1">
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>{away.code}</span>
          <div className="flex gap-1.5 justify-end">{renderDots(awayKicks)}</div>
        </div>
      </div>

      {/* Last kick */}
      {kicks.length > 0 && (() => {
        const last = kicks[kicks.length - 1]
        return (
          <div className="mt-3" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            {last.player} ({last.team === 'home' ? home.code : away.code}) — {last.scored ? '✓ Scored' : '✗ Missed'}
          </div>
        )
      })()}
    </div>
  )
}
