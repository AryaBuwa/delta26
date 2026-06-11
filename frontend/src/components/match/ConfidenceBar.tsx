'use client'
// components/match/ConfidenceBar.tsx
import { motion } from 'framer-motion'
import type { Confidence, Team } from '@/types'
import { toConfidenceRange } from '@/lib/utils'

interface Props {
  confidence: Confidence
  home: Team
  away: Team
  shift?: string
  modelVersion?: number
  trainingCount?: number
  locked?: boolean
}

export function ConfidenceBar({ confidence, home, away, shift, modelVersion, trainingCount, locked }: Props) {
  const range = toConfidenceRange(confidence)
  const homePct = Math.round(confidence.home_win * 100)
  const drawPct = Math.round(confidence.draw * 100)
  const awayPct = Math.round(confidence.away_win * 100)

  return (
    <div className="px-5 pb-4 flex flex-col gap-2">
      {/* Team labels */}
      <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, letterSpacing: '0.08em', color: 'var(--text-dim)', textTransform: 'uppercase' }}>
        <span>{home.code}</span>
        <span>Draw</span>
        <span>{away.code}</span>
      </div>

      {/* Bar */}
      <div className="conf-bar rounded-none">
        <motion.div
          className="h-full"
          style={{ background: 'var(--accent-blue)' }}
          initial={{ flex: 0 }}
          animate={{ flex: homePct }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
        <motion.div
          className="h-full"
          style={{ background: 'rgba(255,255,255,0.14)' }}
          initial={{ flex: 0 }}
          animate={{ flex: drawPct }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
        <motion.div
          className="h-full"
          style={{ background: 'var(--accent)' }}
          initial={{ flex: 0 }}
          animate={{ flex: awayPct }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        />
      </div>

      {/* Percentages */}
      <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
        <span style={{ color: confidence.home_win > confidence.away_win ? 'var(--accent)' : undefined }}>
          {range.home_win}
        </span>
        <span>{range.draw}</span>
        <span style={{ color: confidence.away_win > confidence.home_win ? 'var(--accent)' : undefined }}>
          {range.away_win}
        </span>
      </div>

      {/* Shift + meta */}
      <div className="flex items-center justify-between">
        {shift && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--accent-green)', letterSpacing: '0.06em' }}>
            {shift}
          </span>
        )}
        <div className="flex items-center gap-2 ml-auto">
          {locked && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
              Locked 85&apos;
            </span>
          )}
          {modelVersion && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
              v{modelVersion} · {trainingCount ?? 0} matches
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
