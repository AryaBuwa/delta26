// lib/utils.ts — Project Delta — shared utility functions

import type { MatchState, MatchPhase, Confidence, ConfidenceRange } from '@/types'

// ── TIME ──────────────────────────────────────────────────────────────────────
export function formatKickoff(utcString: string): string {
  return new Date(utcString).toLocaleTimeString(undefined, {
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })
}

export function formatKickoffFull(utcString: string): string {
  return new Date(utcString).toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  })
}

export function minutesUntilKickoff(utcString: string): number {
  return Math.max(0, Math.round((new Date(utcString).getTime() - Date.now()) / 60_000))
}

export function isMatchToday(utcString: string): boolean {
  const d = new Date(utcString)
  const now = new Date()
  return d.getDate() === now.getDate()
    && d.getMonth() === now.getMonth()
    && d.getFullYear() === now.getFullYear()
}

// ── MATCH STATE ───────────────────────────────────────────────────────────────
export function getStateLabel(state: MatchState): string {
  const labels: Record<MatchState, string> = {
    SCHEDULED: 'Upcoming',  LIVE: 'Live',        HT: 'Half Time',
    LIVE_2H: 'Live',        FT: 'Full Time',     ET_1H: 'Extra Time',
    ET_HT: 'ET Half Time',  ET_2H: 'Extra Time', PENALTIES: 'Penalties',
    FINISHED: 'Final',      VOID: 'Void',
  }
  return labels[state]
}

export function isLive(state: MatchState): boolean {
  return ['LIVE', 'LIVE_2H', 'ET_1H', 'ET_2H', 'PENALTIES'].includes(state)
}

export function isVotingOpen(state: MatchState): boolean {
  return ['SCHEDULED', 'LIVE', 'LIVE_2H'].includes(state)
}

export function isPredictionLocked(state: MatchState): boolean {
  return ['FT', 'ET_1H', 'ET_HT', 'ET_2H', 'PENALTIES', 'FINISHED', 'VOID'].includes(state)
}

export function getCardTopColor(state: MatchState): string {
  if (isLive(state)) return 'var(--accent)'
  if (state === 'HT' || state === 'ET_HT') return 'var(--accent-orange)'
  return 'var(--line-soft)'
}

// ── PHASE LABELS ──────────────────────────────────────────────────────────────
export function getPhaseLabel(phase: MatchPhase): string {
  const labels: Record<MatchPhase, string> = {
    group: 'Group Stage', r32: 'Round of 32',  r16: 'Round of 16',
    qf: 'Quarter-Final',  sf: 'Semi-Final',    third: 'Third Place',
    final: 'Final',
  }
  return labels[phase]
}

// ── CONFIDENCE ────────────────────────────────────────────────────────────────
export function toConfidenceRange(c: Confidence): ConfidenceRange {
  const fmt = (v: number) => {
    const pct = Math.round(v * 100)
    return `${Math.max(2, pct - 4)}–${Math.min(98, pct + 4)}%`
  }
  return { home_win: fmt(c.home_win), draw: fmt(c.draw), away_win: fmt(c.away_win) }
}

export function getLeadingSide(c: Confidence): 'home' | 'draw' | 'away' {
  if (c.home_win >= c.away_win && c.home_win >= c.draw) return 'home'
  if (c.away_win >= c.home_win && c.away_win >= c.draw) return 'away'
  return 'draw'
}

// ── EVENT ICONS ───────────────────────────────────────────────────────────────
export function getEventIcon(type: string): string {
  const icons: Record<string, string> = {
    goal: '⚽', own_goal: '⚽', yellow_card: '🟨', red_card: '🟥',
    substitution: '↕', penalty_scored: '⚽', penalty_missed: '✗', var_review: '📺',
  }
  return icons[type] ?? '·'
}

// ── MISC ──────────────────────────────────────────────────────────────────────
export function cn(...classes: (string | undefined | false | null)[]): string {
  return classes.filter(Boolean).join(' ')
}

export function clamp(v: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, v))
}

export function truncate(s: string, max: number): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}
