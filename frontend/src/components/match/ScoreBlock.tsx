'use client'
// components/match/ScoreBlock.tsx
import { motion } from 'framer-motion'
import type { Score, Team, MatchState } from '@/types'

interface Props {
  home: Team
  away: Team
  score: Score
  minute: string
  state: MatchState
  compact?: boolean
}

const stateLabel: Record<MatchState, string | null> = {
  SCHEDULED: null,
  LIVE:      null,
  HT:        'HT',
  LIVE_2H:   null,
  FT:        'FT',
  ET_1H:     null,
  ET_HT:     'ET HT',
  ET_2H:     null,
  PENALTIES: 'PEN',
  FINISHED:  'FT',
  VOID:      'VOID',
}

export function ScoreBlock({ home, away, score, minute, state, compact }: Props) {
  const isLive = ['LIVE', 'LIVE_2H', 'ET_1H', 'ET_2H', 'PENALTIES'].includes(state)
  const isScheduled = state === 'SCHEDULED'
  const label = stateLabel[state]

  return (
    <div className={`grid items-center px-5 ${compact ? 'py-4 gap-3' : 'py-6 gap-4'}`}
      style={{ gridTemplateColumns: '1fr auto 1fr' }}>

      {/* Home team */}
      <div className="flex flex-col gap-1">
        <span style={{
          fontFamily: 'var(--font-sans)',
          fontSize: compact ? 13 : 14,
          fontWeight: 700,
          letterSpacing: '0.01em',
          color: 'var(--text-primary)',
        }}>
          {home.name}
        </span>
        {home.fifa_rank > 0 && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            #{home.fifa_rank}
          </span>
        )}
      </div>

      {/* Score / vs */}
      <div className="flex flex-col items-center gap-1.5">
        {isScheduled ? (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 22, color: 'var(--text-dim)', letterSpacing: '0.04em' }}>
            vs
          </span>
        ) : (
          <motion.div
            key={`${score.home}-${score.away}`}
            initial={{ scale: 1.1 }}
            animate={{ scale: 1 }}
            transition={{ duration: 0.2 }}
            className="score-display"
          >
            {score.home} <span style={{ color: 'var(--text-dim)' }}>–</span> {score.away}
          </motion.div>
        )}

        {/* Minute or state label */}
        {isLive && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            color: 'var(--accent)',
            letterSpacing: '0.12em',
            background: 'rgba(232,255,71,0.08)',
            padding: '2px 8px',
            borderRadius: 2,
          }}>
            {minute}&apos;
          </span>
        )}
        {label && !isLive && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            color: 'var(--text-dim)',
            letterSpacing: '0.14em',
            textTransform: 'uppercase',
          }}>
            {label}
          </span>
        )}
      </div>

      {/* Away team */}
      <div className="flex flex-col items-end gap-1 text-right">
        <span style={{
          fontFamily: 'var(--font-sans)',
          fontSize: compact ? 13 : 14,
          fontWeight: 700,
          letterSpacing: '0.01em',
          color: 'var(--text-primary)',
        }}>
          {away.name}
        </span>
        {away.fifa_rank > 0 && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
            #{away.fifa_rank}
          </span>
        )}
      </div>
    </div>
  )
}
