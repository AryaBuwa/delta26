'use client'
// app/page.tsx — Homepage: tournament overview + all match cards
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { motion } from 'framer-motion'
import type { Match, MatchesResponse } from '@/types'
import { getMatches } from '@/lib/api'
import { useMatchStore } from '@/lib/store'
import { useGlobalSSE } from '@/hooks/useSSE'
import { StateTag } from '@/components/ui'
import { ScoreBlock } from '@/components/match/ScoreBlock'
import { ConfidenceBar } from '@/components/match/ConfidenceBar'
import { isLive, formatKickoff, getPhaseLabel, cn } from '@/lib/utils'

// ── MATCH CARD (homepage compact version) ─────────────────────────────────────
function MatchCard({ match }: { match: Match }) {
  const live = isLive(match.state)
  const finished = match.state === 'FINISHED' || match.state === 'FT'

  return (
    <Link href={`/match/${match.match_id}`} style={{ textDecoration: 'none' }}>
      <motion.div
        className={cn('card', live && 'card-live', !live && !finished && 'card-ft')}
        whileHover={{ borderColor: 'var(--line-hard)' }}
        transition={{ duration: 0.15 }}
        style={{ cursor: 'pointer' }}
      >
        {/* Card top bar */}
        <div
          className="flex items-center justify-between px-4 py-2.5"
          style={{ borderBottom: '1px solid var(--line-dim)' }}
        >
          <div className="flex items-center gap-2">
            <StateTag state={match.state} />
            {match.group && (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                Group {match.group}
              </span>
            )}
          </div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
            {match.state === 'SCHEDULED'
              ? formatKickoff(match.kickoff_utc)
              : match.venue.split(',')[0]}
          </span>
        </div>

        {/* Score */}
        <ScoreBlock
          home={match.home}
          away={match.away}
          score={match.score}
          minute={match.minute}
          state={match.state}
          compact
        />

        {/* Confidence bar — only if not scheduled */}
        {match.state !== 'SCHEDULED' && match.ai_prediction && (
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
        )}

        {/* Scheduled: show AI prediction */}
        {match.state === 'SCHEDULED' && match.ai_prediction && (
          <div className="px-4 pb-3">
            <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
              <span>AI: {Math.round(match.ai_prediction.home_win * 100)}%</span>
              <span>Draw {Math.round(match.ai_prediction.draw * 100)}%</span>
              <span>{Math.round(match.ai_prediction.away_win * 100)}%</span>
            </div>
          </div>
        )}

        {/* Tap to view hint */}
        <div className="px-4 py-2" style={{ borderTop: '1px solid var(--line-dim)' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
            Tap to vote + view analysis →
          </span>
        </div>
      </motion.div>
    </Link>
  )
}

// ── DAY GROUP ─────────────────────────────────────────────────────────────────
function DayGroup({ date, matches }: { date: string; matches: Match[] }) {
  const d = new Date(date + 'T12:00:00') // local noon — avoids timezone shift
  const label = d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
  const hasLive = matches.some(m => isLive(m.state))
  
  return (
    <section>
      <div className="flex items-center gap-3 mb-4">
        <h2 style={{ fontFamily: 'var(--font-mono)', fontSize: 11, letterSpacing: '0.14em', textTransform: 'uppercase', color: hasLive ? 'var(--accent)' : 'var(--text-dim)' }}>
          {label}
        </h2>
        {hasLive && <span className="live-dot" />}
        <div style={{ flex: 1, height: 1, background: 'var(--line-dim)' }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
          {matches.length} match{matches.length !== 1 ? 'es' : ''}
        </span>
      </div>

      <div className="grid gap-3" style={{
        gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
      }}>
        {matches.map(m => <MatchCard key={m.match_id} match={m} />)}
      </div>
    </section>
  )
}

// ── PAGE ──────────────────────────────────────────────────────────────────────
export default function HomePage() {
  const [overview, setOverview] = useState<MatchesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { setMatch } = useMatchStore()
  const matches = useMatchStore((s) => s.matches)

  // Global SSE — updates all live match cards in real time
  const { connected } = useGlobalSSE()

  useEffect(() => {
    getMatches()
      .then(data => {
        // Re-group by local date instead of UTC date
        const allMatches: Match[] = data.days.flatMap(d => d.matches)
        
        const localDays: Record<string, Match[]> = {}
        allMatches.forEach(m => {
          const localDate = new Date(m.kickoff_utc)
            .toLocaleDateString('en-CA') // gives YYYY-MM-DD in local time
          if (!localDays[localDate]) localDays[localDate] = []
          localDays[localDate].push(m)
        })

        const regrouped = {
          ...data,
          days: Object.entries(localDays)
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([date, matches]) => ({ date, matches }))
        }

        regrouped.days.forEach(day => day.matches.forEach(m => setMatch(m)))
        setOverview(regrouped)
      })
      .catch(() => setError('The server wakes up in ~30 seconds. Matches will load automatically.'))
      .finally(() => setLoading(false))
  }, [setMatch])

  // Merge store updates into local view
  const days = overview?.days.map(day => ({
    ...day,
    matches: day.matches.map(m => matches[m.match_id] ?? m),
  }))

  const liveCount = overview?.live_count ?? 0
  const totalDone = overview?.completed_matches ?? 0
  const totalMatches = overview?.total_matches ?? 104

  return (
    <div className="page" style={{ paddingTop: 32, paddingBottom: 64 }}>

      {/* Hero */}
      <div style={{ marginBottom: 40 }}>
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 style={{
              fontFamily: 'var(--font-sans)',
              fontSize: 'clamp(28px, 5vw, 48px)',
              fontWeight: 800,
              letterSpacing: '-0.03em',
              lineHeight: 1.05,
              color: 'var(--text-primary)',
              marginBottom: 10,
            }}>
              AI vs Humans.<br />
              <span style={{ color: 'var(--accent)' }}>104 matches.</span>
            </h1>
            <p style={{ fontSize: 13, color: 'var(--text-muted)', maxWidth: 440, lineHeight: 1.6 }}>
              Who predicts better — an algorithm that learns from every match, or humans watching the same game? Vote on each match. Find out.
            </p>
          </div>

          {/* Tournament stats */}
          <div className="flex gap-4 flex-wrap">
            {[
              { label: 'Live Now', value: liveCount > 0 ? String(liveCount) : '—', accent: liveCount > 0 },
              { label: 'Completed', value: String(totalDone) },
              { label: 'Remaining', value: String(totalMatches - totalDone) },
            ].map(({ label, value, accent }) => (
              <div key={label} className="flex flex-col gap-1" style={{ minWidth: 60 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-dim)' }}>
                  {label}
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: accent ? 'var(--accent)' : 'var(--text-primary)', lineHeight: 1 }}>
                  {value}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* SSE status */}
        <div className="flex items-center gap-2 mt-4">
          <div style={{
            width: 6, height: 6, borderRadius: '50%',
            background: connected ? 'var(--accent-green)' : 'var(--text-dim)',
          }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
            {connected ? 'Live updates active · ~30s delayed' : 'Connecting…'}
          </span>
        </div>
      </div>

      {/* Content */}
      {loading && (
        <div className="flex flex-col gap-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="card" style={{ height: 160, opacity: 0.4, animation: 'pulse 1.5s ease-in-out infinite' }} />
          ))}
        </div>
      )}

      {error && (
        <div style={{ padding: '20px', borderRadius: 4, border: '1px solid rgba(255,71,71,0.3)', background: 'rgba(255,71,71,0.05)', color: 'var(--accent-red)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          {error}
        </div>
      )}

      {days && (
        <div className="flex flex-col gap-10">
          {days.map(({ date, matches: dayMatches }) => (
            <DayGroup key={date} date={date} matches={dayMatches} />
          ))}
        </div>
      )}
    </div>
  )
}
