'use client'
import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import toast from 'react-hot-toast'
import type { VotePick, VoteDistribution, Match } from '@/types'
import { useVoteStore } from '@/lib/store'
import { castVote } from '@/lib/api'
import { useFingerprint } from '@/hooks/useFingerprint'
import { cn } from '@/lib/utils'
import { Spinner } from '@/components/ui'

interface Props {
  match: Match
  onVoted?: (dist: VoteDistribution) => void
}

export function VoteButtons({ match, onVoted }: Props) {
  const { fingerprint, sessionId } = useFingerprint()
  const { getVote, hasVoted, setVote } = useVoteStore()
  const [submitting, setSubmitting] = useState(false)
  const [dist, setDist] = useState<VoteDistribution | null>(null)

  const currentVote = getVote(match.match_id)
  const voted = hasVoted(match.match_id)

  const options: { pick: VotePick; label: string; team: string }[] = [
    { pick: 'home', label: 'Home', team: match.home.name },
    { pick: 'draw', label: 'Draw', team: '—' },
    { pick: 'away', label: 'Away', team: match.away.name },
  ]

  async function handleVote(pick: VotePick) {
    if (submitting || !fingerprint || pick === currentVote) return
    setSubmitting(true)
    try {
      const res = await castVote({
        match_id: match.match_id,
        pick,
        fingerprint_hash: fingerprint,
        session_id: sessionId,
        turnstile_token: 'dev-token',
      })
      if (res.success) {
        setVote(match.match_id, pick)
        if (res.distribution) {
          setDist(res.distribution)
          onVoted?.(res.distribution)
        }
        toast.success(voted ? 'Vote updated' : 'Vote recorded', { duration: 2000 })
      } else {
        toast.error(res.message || 'Vote failed')
      }
    } catch {
      toast.error('Could not submit. Try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="px-5 pb-5 pt-3">
      <div className="flex items-center justify-between mb-3">
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
          Who wins? · Locks at 85&apos;
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
          Research data — anonymous
        </span>
      </div>

      <div className="grid gap-1.5" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
        {options.map(({ pick, label, team }) => (
          <button
            key={pick}
            className={cn('vote-btn', currentVote === pick && 'selected')}
            onClick={() => handleVote(pick)}
            disabled={submitting}
          >
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.1em', textTransform: 'uppercase', color: currentVote === pick ? 'var(--accent)' : 'var(--text-dim)' }}>
              {label}
            </span>
            <span style={{ fontSize: 11, fontWeight: 700, color: currentVote === pick ? 'var(--text-primary)' : 'var(--text-muted)', textAlign: 'center' }}>
              {team.length > 10 ? team.slice(0, 9) + '…' : team}
            </span>
            {submitting && currentVote !== pick && <Spinner className="w-3 h-3" />}
          </button>
        ))}
      </div>

      <AnimatePresence>
        {dist && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }} className="mt-3">
            <div className="flex justify-between mb-1">
              {(['home', 'draw', 'away'] as VotePick[]).map((p) => (
                <span key={p} style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: currentVote === p ? 'var(--accent)' : 'var(--text-dim)' }}>
                  {dist[`${p}_pct`]}
                </span>
              ))}
            </div>
            <div style={{ height: 2, background: 'var(--bg-surface)', display: 'flex', gap: 1 }}>
              <div style={{ background: 'var(--accent-blue)', flex: dist.home }} />
              <div style={{ background: 'rgba(255,255,255,0.14)', flex: dist.draw }} />
              <div style={{ background: 'var(--accent)', flex: dist.away }} />
            </div>
            <div className="mt-1.5 text-right" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
              {dist.total.toLocaleString()} votes
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
