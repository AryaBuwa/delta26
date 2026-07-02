'use client'
// app/page.tsx — Homepage: tournament overview + all match cards
// Updated July 2, 2026: scroll fix, AI correct indicators, prediction status,
// learning progress card, AI vs human strip, latest AI prediction card
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
import { isLive, formatKickoff, cn } from '@/lib/utils'

// ── TYPES ─────────────────────────────────────────────────────────────────────
interface StatsData {
  ai_accuracy: number | null
  human_accuracy: number | null
  matches_evaluated: number
  model_version: number
  accuracy_at_start: number | null
  accuracy_current: number | null
  improvement: number | null
  last_retrained: string | null
}

// ── PREDICTION STATUS LABEL ───────────────────────────────────────────────────
function getPredictionStatus(state: string): { label: string; color: string } {
  switch (state) {
    case 'SCHEDULED': return { label: 'Awaiting Kickoff', color: 'var(--text-dim)' }
    case 'LIVE':
    case 'LIVE_2H':
    case 'ET_1H':
    case 'ET_2H': return { label: 'Prediction Active', color: 'var(--accent)' }
    case 'HT':
    case 'ET_HT': return { label: 'Prediction Active', color: 'var(--accent)' }
    case 'PENALTIES': return { label: 'Live Match', color: 'var(--accent)' }
    case 'FINISHED':
    case 'FT': return { label: 'Prediction Complete', color: 'var(--text-dim)' }
    default: return { label: 'Prediction Active', color: 'var(--accent)' }
  }
}

// ── AI CORRECT INDICATOR ──────────────────────────────────────────────────────
function AICorrectIndicator({ match }: { match: Match }) {
  const finished = match.state === 'FINISHED' || match.state === 'FT'
  if (!finished || !match.ai_prediction) return null

  const { home_win, away_win } = match.ai_prediction
  const draw = match.ai_prediction.draw

  // Determine actual result
  const homeScore = match.score?.home ?? 0
  const awayScore = match.score?.away ?? 0
  const actualResult = homeScore > awayScore ? 'home' : awayScore > homeScore ? 'away' : 'draw'

  // Determine AI predicted result (highest probability)
  const maxProb = Math.max(home_win, draw, away_win)
  const aiPredicted = maxProb === home_win ? 'home' : maxProb === away_win ? 'away' : 'draw'

  const correct = actualResult === aiPredicted

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 4,
      fontFamily: 'var(--font-mono)',
      fontSize: 9,
      letterSpacing: '0.08em',
      color: correct ? '#4ade80' : '#f87171',
    }}>
      <span>{correct ? '✓' : '✗'}</span>
      <span>{correct ? 'AI Correct' : 'AI Incorrect'}</span>
    </div>
  )
}

// ── MATCH CARD (homepage compact version) ─────────────────────────────────────
function MatchCard({ match }: { match: Match }) {
  const live = isLive(match.state)
  const finished = match.state === 'FINISHED' || match.state === 'FT'
  const predStatus = getPredictionStatus(match.state)

  return (
    <Link href={`/match/${match.match_id}`} style={{ textDecoration: 'none' }} scroll={false}>
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
                {match.phase === 'r32' ? 'R32' :
                 match.phase === 'r16' ? 'R16' :
                 match.phase === 'qf' ? 'QF' :
                 match.phase === 'sf' ? 'SF' :
                 match.phase === 'final' ? 'FINAL' :
                 `Group ${match.group}`}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {/* AI correct indicator for finished matches */}
            <AICorrectIndicator match={match} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
              {match.state === 'SCHEDULED'
                ? formatKickoff(match.kickoff_utc)
                : match.venue?.split(',')[0] ?? ''}
            </span>
          </div>
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

        {/* Scheduled: show AI prediction + prediction status */}
        {match.state === 'SCHEDULED' && match.ai_prediction && (
          <div className="px-4 pb-3">
            <div className="flex justify-between" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
              <span>AI: {Math.round(match.ai_prediction.home_win * 100)}%</span>
              <span>Draw {Math.round(match.ai_prediction.draw * 100)}%</span>
              <span>{Math.round(match.ai_prediction.away_win * 100)}%</span>
            </div>
          </div>
        )}

        {/* Bottom bar: prediction status + tap hint */}
        <div
          className="flex items-center justify-between px-4 py-2"
          style={{ borderTop: '1px solid var(--line-dim)' }}
        >
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            color: predStatus.color,
            letterSpacing: '0.08em',
          }}>
            {predStatus.label}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em' }}>
            Tap to vote + view analysis →
          </span>
        </div>
      </motion.div>
    </Link>
  )
}

// ── LATEST AI PREDICTION CARD (Priority 1) ────────────────────────────────────
function LatestPredictionCard({ days }: { days: Array<{ date: string; matches: Match[] }> }) {
  // Find the next upcoming match with a prediction
  const allMatches = days.flatMap(d => d.matches)
  const next = allMatches.find(m => m.state === 'SCHEDULED' && m.ai_prediction)
    ?? allMatches.find(m => isLive(m.state) && m.ai_prediction)

  if (!next || !next.ai_prediction) return null

  const pred = next.ai_prediction
  const homeWin = Math.round(pred.home_win * 100)
  const draw = Math.round(pred.draw * 100)
  const awayWin = Math.round(pred.away_win * 100)
  const maxProb = Math.max(homeWin, draw, awayWin)
  const confidence = Math.min(98, Math.max(2, maxProb))
  const live = isLive(next.state)

  return (
    <Link href={`/match/${next.match_id}`} style={{ textDecoration: 'none' }} scroll={false}>
      <motion.div
        whileHover={{ borderColor: 'var(--line-hard)' }}
        transition={{ duration: 0.15 }}
        style={{
          border: '1px solid var(--line-mid)',
          borderRadius: 4,
          background: 'var(--bg-card)',
          padding: '16px 20px',
          cursor: 'pointer',
          marginBottom: 8,
        }}
      >
        <div className="flex items-center justify-between" style={{ marginBottom: 12 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--accent)' }}>
            {live ? '● Live Prediction' : 'Latest AI Prediction'}
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
            v{pred.model_version} · {next.home?.name ?? '?'} vs {next.away?.name ?? '?'}
          </span>
        </div>

        <div className="flex items-center gap-6">
          {/* Home */}
          <div style={{ textAlign: 'center', minWidth: 60 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: homeWin === maxProb ? 'var(--accent)' : 'var(--text-primary)' }}>
              {homeWin}%
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 2 }}>
              {next.home?.name ?? '?'}
            </div>
          </div>

          {/* Draw */}
          <div style={{ textAlign: 'center', minWidth: 40 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: draw === maxProb ? 'var(--accent)' : 'var(--text-muted)' }}>
              {draw}%
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 2 }}>Draw</div>
          </div>

          {/* Away */}
          <div style={{ textAlign: 'center', minWidth: 60 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: awayWin === maxProb ? 'var(--accent)' : 'var(--text-primary)' }}>
              {awayWin}%
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 2 }}>
              {next.away?.name ?? '?'}
            </div>
          </div>

          {/* Divider */}
          <div style={{ flex: 1, height: 1, background: 'var(--line-dim)' }} />

          {/* Meta */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, textAlign: 'right' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
              Model Confidence
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 500, color: 'var(--text-primary)' }}>
              {confidence}%
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>
              {live ? 'Prediction Locked at 85\'' : getPredictionStatus(next.state).label}
            </div>
          </div>
        </div>
      </motion.div>
    </Link>
  )
}

// ── LEARNING PROGRESS CARD (Priority 3) ───────────────────────────────────────
function LearningProgressCard({ stats }: { stats: StatsData | null }) {
  if (!stats) return null

  const current = stats.accuracy_current != null ? Math.round(stats.accuracy_current * 100) : null
  const start = stats.accuracy_at_start != null ? Math.round(stats.accuracy_at_start * 100) : null
  const improvement = stats.improvement != null ? Math.round(stats.improvement * 100) : null

  return (
    <div style={{
      border: '1px solid var(--line-dim)',
      borderRadius: 4,
      background: 'var(--bg-card)',
      padding: '14px 18px',
    }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 10 }}>
        Learning Progress
      </div>
      <div className="flex items-center gap-4 flex-wrap">
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>Tournament Start</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: 'var(--text-muted)' }}>
            {start != null ? `${start}%` : '—'}
          </div>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)' }}>→</div>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>Current</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: 'var(--text-primary)' }}>
            {current != null ? `${current}%` : '—'}
          </div>
        </div>
        {improvement != null && (
          <>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-dim)' }}>·</div>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>Improvement</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: '#4ade80' }}>
                +{improvement}%
              </div>
            </div>
          </>
        )}
        <div style={{ flex: 1 }} />
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)' }}>Model Version</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500, color: 'var(--text-primary)' }}>
            v{stats.model_version}
          </div>
          {stats.last_retrained && (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginTop: 2 }}>
              Retrained {stats.last_retrained}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── DAY GROUP ─────────────────────────────────────────────────────────────────
function DayGroup({ date, matches }: { date: string; matches: Match[] }) {
  const d = new Date(date + 'T12:00:00')
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
  const [stats, setStats] = useState<StatsData | null>(null)
  const { setMatch } = useMatchStore()
  const matches = useMatchStore((s) => s.matches)
  const { connected } = useGlobalSSE()

  useEffect(() => {
    getMatches()
      .then(data => {
        const allMatches: Match[] = data.days.flatMap(d => d.matches)
        const localDays: Record<string, Match[]> = {}
        allMatches.forEach(m => {
          const localDate = new Date(m.kickoff_utc).toLocaleDateString('en-CA')
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

  // Fetch stats — fails silently if /api/stats doesn't exist yet
  useEffect(() => {
    fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/stats`)
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setStats(d))
      .catch(() => {})
  }, [])

  const days = overview?.days.map(day => ({
    ...day,
    matches: day.matches.map(m => matches[m.match_id] ?? m),
  }))

  const liveCount = overview?.live_count ?? 0
  const totalDone = overview?.completed_matches ?? 0
  const totalMatches = overview?.total_matches ?? 104

  // Sort so upcoming/live days are at top, past days at bottom
  const sortedDays = days ? [...days].sort((a, b) => {
    const aHasUpcoming = a.matches.some(m => m.state === 'SCHEDULED' || isLive(m.state))
    const bHasUpcoming = b.matches.some(m => m.state === 'SCHEDULED' || isLive(m.state))
    if (aHasUpcoming && !bHasUpcoming) return -1
    if (!aHasUpcoming && bHasUpcoming) return 1
    return a.date.localeCompare(b.date)
  }) : []

  return (
    <div className="page" style={{ paddingTop: 32, paddingBottom: 64 }}>

      {/* Hero */}
      <div style={{ marginBottom: 32 }}>
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
              ...(stats?.ai_accuracy != null ? [{ label: 'AI Accuracy', value: `${Math.round(stats.ai_accuracy)}%`, accent: false }] : []),
              ...(stats?.human_accuracy != null ? [{ label: 'Human Accuracy', value: `${Math.round(stats.human_accuracy)}%`, accent: false }] : []),
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

      {/* Latest AI Prediction card (Priority 1) */}
      {days && <LatestPredictionCard days={days} />}

      {/* Learning Progress card (Priority 3) */}
      {stats && (
        <div style={{ marginBottom: 32 }}>
          <LearningProgressCard stats={stats} />
        </div>
      )}

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

      {sortedDays.length > 0 && (
        <div className="flex flex-col gap-10">
          {sortedDays.map(({ date, matches: dayMatches }) => (
            <DayGroup key={date} date={date} matches={dayMatches} />
          ))}
        </div>
      )}
    </div>
  )
}
