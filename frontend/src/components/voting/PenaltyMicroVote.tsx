'use client'
import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'
import type { Match } from '@/types'
import { castPenaltyVote } from '@/lib/api'
import { useFingerprint } from '@/hooks/useFingerprint'

interface Props {
  match: Match
  round: number
  onVoted?: () => void
}

const WINDOW = 60

export function PenaltyMicroVote({ match, round, onVoted }: Props) {
  const { fingerprint } = useFingerprint()
  const [timeLeft, setTimeLeft] = useState(WINDOW)
  const [voted, setVoted] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (voted || timeLeft <= 0) return
    const t = setInterval(() => setTimeLeft((p) => p - 1), 1000)
    return () => clearInterval(t)
  }, [voted, timeLeft])

  if (timeLeft <= 0 || voted) return null

  async function handleVote(pick: 'home' | 'away') {
    if (submitting) return
    setSubmitting(true)
    try {
      await castPenaltyVote({ match_id: match.match_id, pick, round, fingerprint_hash: fingerprint })
      setVoted(true)
      onVoted?.()
      toast.success('Penalty vote recorded', { duration: 1500 })
    } catch {
      toast.error('Vote failed')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      className="border-t px-5 py-4" style={{ borderColor: 'rgba(255,71,71,0.25)', background: 'rgba(255,71,71,0.04)' }}>
      <div className="flex items-center justify-between mb-3">
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--accent-red)' }}>
          Penalty micro-vote · Round {round}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: timeLeft < 15 ? 'var(--accent-red)' : 'var(--text-dim)' }}>
          {timeLeft}s
        </span>
      </div>
      <div style={{ height: 2, background: 'var(--bg-surface)', marginBottom: 12 }}>
        <motion.div style={{ height: '100%', background: 'var(--accent-red)', transformOrigin: 'left', scaleX: timeLeft / WINDOW }}
          transition={{ duration: 1, ease: 'linear' }} />
      </div>
      <p style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 10, fontFamily: 'var(--font-mono)' }}>
        AI: Coin flip. 50/50.
      </p>
      <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 1fr' }}>
        {(['home', 'away'] as const).map((side) => (
          <button key={side} className="vote-btn" onClick={() => handleVote(side)} disabled={submitting}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>{side}</span>
            <span style={{ fontSize: 12, fontWeight: 700 }}>{side === 'home' ? match.home.name : match.away.name}</span>
          </button>
        ))}
      </div>
    </motion.div>
  )
}
