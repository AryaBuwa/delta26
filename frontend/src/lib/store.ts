'use client'
// lib/store.ts — Project Delta — Zustand global state

import { create } from 'zustand'
import type { Match, VotePick, VoteDistribution } from '@/types'

// ── MATCH STORE ───────────────────────────────────────────────────────────────
interface MatchStoreState {
  matches: Record<string, Match>
  voteDist: Record<string, VoteDistribution>
  setMatch: (match: Match) => void
  updateMatch: (id: string, partial: Partial<Match>) => void
  setVoteDist: (matchId: string, dist: VoteDistribution) => void
  getMatch: (id: string) => Match | undefined
}

export const useMatchStore = create<MatchStoreState>()((set, get) => ({
  matches: {},
  voteDist: {},

  setMatch: (match) =>
    set((s) => ({ matches: { ...s.matches, [match.match_id]: match } })),

  updateMatch: (id, partial) =>
    set((s) => {
      const existing = s.matches[id]
      if (!existing) return s
      return { matches: { ...s.matches, [id]: { ...existing, ...partial } } }
    }),

  setVoteDist: (matchId, dist) =>
    set((s) => ({ voteDist: { ...s.voteDist, [matchId]: dist } })),

  getMatch: (id) => get().matches[id],
}))

// ── VOTE STORE (in-memory — no localStorage per spec) ─────────────────────────
interface VoteStoreState {
  votes: Record<string, VotePick>
  changes: Record<string, number>
  setVote: (matchId: string, pick: VotePick) => void
  getVote: (matchId: string) => VotePick | undefined
  hasVoted: (matchId: string) => boolean
  getChangeCount: (matchId: string) => number
}

export const useVoteStore = create<VoteStoreState>()((set, get) => ({
  votes: {},
  changes: {},

  setVote: (matchId, pick) =>
    set((s) => {
      const prev = s.votes[matchId]
      return {
        votes: { ...s.votes, [matchId]: pick },
        changes: { ...s.changes, [matchId]: prev ? (s.changes[matchId] ?? 0) + 1 : 0 },
      }
    }),

  getVote: (matchId) => get().votes[matchId],
  hasVoted: (matchId) => matchId in get().votes,
  getChangeCount: (matchId) => get().changes[matchId] ?? 0,
}))

// ── UI STORE ──────────────────────────────────────────────────────────────────
interface UIStoreState {
  goalFlash: Record<string, boolean>
  flashGoal: (matchId: string) => void
}

export const useUIStore = create<UIStoreState>()((set) => ({
  goalFlash: {},
  flashGoal: (matchId) => {
    set((s) => ({ goalFlash: { ...s.goalFlash, [matchId]: true } }))
    setTimeout(
      () => set((s) => ({ goalFlash: { ...s.goalFlash, [matchId]: false } })),
      3000
    )
  },
}))

// ── ADMIN AUTH STORE (sessionStorage — clears on tab close) ───────────────────
interface AdminAuthState {
  adminPassword: string | null
  setAdminPassword: (p: string | null) => void
}

export const useAdminAuth = create<AdminAuthState>()((set) => ({
  adminPassword: null,
  setAdminPassword: (p) => set({ adminPassword: p }),
}))
