'use client'
// app/match/[id]/page.tsx — Individual match screen
// Handles all states: SCHEDULED, LIVE, HT, LIVE_2H, FT, ET_*, PENALTIES, FINISHED, VOID

import { useEffect, useState, useCallback } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { motion, AnimatePresence } from 'framer-motion'
import type { Match } from '@/types'
import { getMatch } from '@/lib/api'
import { useMatchStore } from '@/lib/store'
import { useMatchSSE } from '@/hooks/useSSE'
import { isLive, formatKickoff, cn } from '@/lib/utils'
import { StateTag, Spinner } from '@/components/ui'
import { ScoreBlock } from '@/components/match/ScoreBlock'
import { ConfidenceBar } from '@/components/match/ConfidenceBar'
import { EventTicker } from '@/components/match/EventTicker'
import { AIContext } from '@/components/match/AIContext'
import { PenaltyTracker } from '@/components/match/PenaltyTracker'
import { VoteButtons } from '@/components/voting/VoteButtons'
import { PenaltyMicroVote } from '@/components/voting/PenaltyMicroVote'

// ── HELPERS ───────────────────────────────────────────────────────────────────

function canVote(state: string, minute: string): boolean {
  const locked = ['FT', 'ET_2H', 'FINISHED', 'VOID']
  if (locked.includes(state)) return false
  try {
    if (parseInt(minute.replace('+', '')) >= 85) return false
  } catch { /* ignore */ }
  return true
}

function getPhaseLabel(phase: string): string {
  const labels: Record<string, string> = {
    group: 'Group Stage', r32: 'Round of 32', r16: 'Round of 16',
    qf: 'Quarter-Final', sf: 'Semi-Final', third: 'Third Place', final: 'Final',
  }
  return labels[phase] || phase.toUpperCase()
}

// ── MATCH HEADER ──────────────────────────────────────────────────────────────

function MatchHeader({ match }: { match: Match }) {
  return (
    <div style={{ marginBottom: 24 }}>
      {/* Back + breadcrumb */}
      <div className="flex items-center gap-2 mb-6">
        <Link href="/" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textDecoration: 'none', letterSpacing: '0.1em' }}>
          ← ALL MATCHES
        </Link>
        <span style={{ color: 'var(--line-soft)', fontSize: 9 }}>·</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
          {match.group ? `GROUP ${match.group}` : getPhaseLabel(match.phase)}
        </span>
      </div>

      {/* State + venue */}
      <div className="flex items-center justify-between mb-4">
        <StateTag state={match.state} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
          {match.venue?.split(',')[0] || ''}
        </span>
      </div>
    </div>
  )
}

// ── SCHEDULED VIEW ────────────────────────────────────────────────────────────

function ScheduledView({ match }: { match: Match }) {
  return (
    <div className="flex flex-col gap-6">
      {/* Score block — shows teams + kickoff time */}
      <div className="card" style={{ padding: '32px 24px' }}>
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
        />
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
            {formatKickoff(match.kickoff_utc)}
          </span>
        </div>
      </div>

      {/* AI prediction if available */}
      {match.ai_prediction && (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
            AI Prediction · <span style={{ color: 'var(--accent)' }}>AI Generated</span>
          </p>
          <ConfidenceBar
            confidence={{
              home_win: match.ai_prediction.home_win,
              draw: match.ai_prediction.draw,
              away_win: match.ai_prediction.away_win,
            }}
            home={match.home}
            away={match.away}
            modelVersion={match.ai_prediction.model_version}
            trainingCount={match.ai_prediction.training_match_count}
            locked={match.ai_prediction.locked}
          />
        </div>
      )}

      {/* Voting */}
      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          Your Prediction
        </p>
        <VoteButtons
          matchId={match.match_id}
          home={match.home}
          away={match.away}
          disabled={false}
        />
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 10, textAlign: 'center' }}>
          Locks at 85' · Anonymous · Research data only
        </p>
      </div>

      {/* Vote distribution — zero for now */}
      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          Crowd Prediction
        </p>
        <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          <span>{match.home.name} 0%</span>
          <span>Draw 0%</span>
          <span>{match.away.name} 0%</span>
        </div>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 8 }}>
          0 votes
        </p>
      </div>
    </div>
  )
}

// ── LIVE VIEW ─────────────────────────────────────────────────────────────────

function LiveView({ match }: { match: Match }) {
  const votingOpen = canVote(match.state, match.minute)

  return (
    <div className="flex flex-col gap-6">
      {/* Live score */}
      <div className="card card-live" style={{ padding: '32px 24px' }}>
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
        />
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textAlign: 'center', marginTop: 8 }}>
          ~30s delayed
        </p>
      </div>

      {/* AI confidence */}
      {match.ai_prediction && (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
            AI Confidence · <span style={{ color: 'var(--accent)' }}>AI Generated</span>
          </p>
          <ConfidenceBar
            confidence={{
              home_win: match.ai_prediction.home_win,
              draw: match.ai_prediction.draw,
              away_win: match.ai_prediction.away_win,
            }}
            home={match.home}
            away={match.away}
            modelVersion={match.ai_prediction.model_version}
            trainingCount={match.ai_prediction.training_match_count}
            locked={match.ai_prediction.locked}
          />
        </div>
      )}

      {/* AI context */}
      {match.live?.ai_context && (
        <AIContext context={match.live.ai_context} />
      )}

      {/* Events */}
      {match.events && match.events.length > 0 && (
        <EventTicker events={match.events} />
      )}

      {/* Voting */}
      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          {votingOpen ? 'Your Prediction' : 'Voting Locked'}
        </p>
        <VoteButtons
          matchId={match.match_id}
          home={match.home}
          away={match.away}
          disabled={!votingOpen}
        />
        {votingOpen && (
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 10, textAlign: 'center' }}>
            Locks at 85'
          </p>
        )}
      </div>

      {/* Crowd */}
      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          Crowd Prediction
        </p>
        <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          <span>{match.home.name} 0%</span>
          <span>Draw 0%</span>
          <span>{match.away.name} 0%</span>
        </div>
      </div>
    </div>
  )
}

// ── PENALTY VIEW ──────────────────────────────────────────────────────────────

function PenaltyView({ match }: { match: Match }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="card card-live" style={{ padding: '32px 24px' }}>
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
        />
      </div>
      {match.live?.penalty_state && (
        <PenaltyTracker
          penaltyState={match.live.penalty_state}
          home={match.home}
          away={match.away}
        />
      )}
      <PenaltyMicroVote
        matchId={match.match_id}
        home={match.home}
        away={match.away}
      />
    </div>
  )
}

// ── FINISHED VIEW ─────────────────────────────────────────────────────────────

function FinishedView({ match }: { match: Match }) {
  return (
    <div className="flex flex-col gap-6">
      {/* Final score */}
      <div className="card" style={{ padding: '32px 24px' }}>
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
        />
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Full Time
          </span>
        </div>
      </div>

      {/* AI result */}
      {match.ai_prediction && (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
            AI Prediction · <span style={{ color: 'var(--accent)' }}>AI Generated</span>
          </p>
          <ConfidenceBar
            confidence={{
              home_win: match.ai_prediction.home_win,
              draw: match.ai_prediction.draw,
              away_win: match.ai_prediction.away_win,
            }}
            home={match.home}
            away={match.away}
            modelVersion={match.ai_prediction.model_version}
            trainingCount={match.ai_prediction.training_match_count}
            locked={match.ai_prediction.locked}
          />
        </div>
      )}

      {/* Events */}
      {match.events && match.events.length > 0 && (
        <EventTicker events={match.events} />
      )}

      {/* Voting closed */}
      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          Crowd Prediction — Final
        </p>
        <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-muted)' }}>
          <span>{match.home.name} 0%</span>
          <span>Draw 0%</span>
          <span>{match.away.name} 0%</span>
        </div>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 8 }}>
          Voting closed
        </p>
      </div>

      {/* Post match debrief */}
      {match.pre_match_brief && (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
            Match Analysis · <span style={{ color: 'var(--accent)' }}>AI Generated</span>
          </p>
          <p style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.7 }}>
            {match.pre_match_brief}
          </p>
        </div>
      )}
    </div>
  )
}

// ── VOID VIEW ─────────────────────────────────────────────────────────────────

function VoidView({ match }: { match: Match }) {
  return (
    <div className="card" style={{ padding: 32, textAlign: 'center' }}>
      <ScoreBlock
        home={match.home}
        away={match.away}
        score={match.score}
        minute={match.minute}
        state={match.state}
      />
      <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-red)', marginTop: 16, letterSpacing: '0.1em' }}>
        Match abandoned or postponed. All predictions cancelled.
      </p>
    </div>
  )
}

// ── HALF TIME VIEW ────────────────────────────────────────────────────────────

function HalfTimeView({ match }: { match: Match }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="card" style={{ padding: '32px 24px' }}>
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
        />
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            {match.state === 'HT' ? 'Half Time' : 'Extra Time Half Time'}
          </span>
        </div>
      </div>

      {match.ai_prediction && (
        <div className="card" style={{ padding: 20 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
            AI Confidence · <span style={{ color: 'var(--accent)' }}>AI Generated</span>
          </p>
          <ConfidenceBar
            confidence={{
              home_win: match.ai_prediction.home_win,
              draw: match.ai_prediction.draw,
              away_win: match.ai_prediction.away_win,
            }}
            home={match.home}
            away={match.away}
            modelVersion={match.ai_prediction.model_version}
            trainingCount={match.ai_prediction.training_match_count}
            locked={match.ai_prediction.locked}
          />
        </div>
      )}

      <div className="card" style={{ padding: 20 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.14em', textTransform: 'uppercase', marginBottom: 12 }}>
          Your Prediction
        </p>
        <VoteButtons
          matchId={match.match_id}
          home={match.home}
          away={match.away}
          disabled={false}
        />
      </div>
    </div>
  )
}

// ── MAIN PAGE ─────────────────────────────────────────────────────────────────

export default function MatchPage() {
  const params = useParams()
  const matchId = params?.id as string
  const router = useRouter()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const { setMatch } = useMatchStore()
  const match = useMatchStore(s => s.matches[matchId])

  // SSE for live updates
  useMatchSSE(matchId, (msg) => {
    if (msg.match_id === matchId && msg.data) {
      setMatch({ ...match, ...msg.data } as Match)
    }
  })

  const load = useCallback(async () => {
    if (!matchId) return
    try {
      const data = await getMatch(matchId)
      setMatch(data)
    } catch {
      setError('Match not found')
    } finally {
      setLoading(false)
    }
  }, [matchId, setMatch])

  useEffect(() => {
    load()
  }, [load])

  if (loading) {
    return (
      <div className="page flex items-center justify-center" style={{ paddingTop: 80 }}>
        <Spinner />
      </div>
    )
  }

  if (error || !match) {
    return (
      <div className="page" style={{ paddingTop: 40 }}>
        <Link href="/" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', textDecoration: 'none', letterSpacing: '0.1em' }}>
          ← ALL MATCHES
        </Link>
        <div style={{ marginTop: 40, padding: 24, border: '1px solid rgba(255,71,71,0.3)', borderRadius: 4 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--accent-red)' }}>
            {error || 'Match not found'}
          </p>
        </div>
      </div>
    )
  }

  const renderContent = () => {
    switch (match.state) {
      case 'SCHEDULED':
        return <ScheduledView match={match} />
      case 'LIVE':
      case 'LIVE_2H':
      case 'ET_1H':
      case 'ET_2H':
        return <LiveView match={match} />
      case 'HT':
      case 'ET_HT':
        return <HalfTimeView match={match} />
      case 'PENALTIES':
        return <PenaltyView match={match} />
      case 'FT':
      case 'FINISHED':
        return <FinishedView match={match} />
      case 'VOID':
        return <VoidView match={match} />
      default:
        return <ScheduledView match={match} />
    }
  }

  return (
    <div className="page" style={{ paddingTop: 24, paddingBottom: 64, maxWidth: 600, margin: '0 auto' }}>
      <MatchHeader match={match} />
      <AnimatePresence mode="wait">
        <motion.div
          key={match.state}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          {renderContent()}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}
