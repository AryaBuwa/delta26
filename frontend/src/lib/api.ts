// lib/api.ts — Project Delta
// Typed API client for all backend communication

import axios, { AxiosError } from 'axios'
import type {
  Match,
  MatchesResponse,
  VotePayload,
  VoteResponse,
  VoteTotals,
  PenaltyVotePayload,
  PreMatchBriefData,
  PostMatchDebriefData,
  SystemStatus,
  ModelStatus,
  ResearchStats,
  PostDraft,
  SSEMessage,
} from '@/types'

// ── BASE CONFIG ───────────────────────────────────────────────────────────────
const BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export const api = axios.create({
  baseURL: BASE,
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
})

function handleError(err: unknown): never {
  if (err instanceof AxiosError) {
    const msg = err.response?.data?.detail || err.response?.data?.error || err.message
    throw new Error(msg)
  }
  throw err
}

function adminHeaders(password: string) {
  return { headers: { Authorization: `Bearer ${password}` } }
}

// ── MATCHES ───────────────────────────────────────────────────────────────────
export async function getMatches(): Promise<MatchesResponse> {
  try {
    const { data } = await api.get<MatchesResponse>('/api/matches')
    return data
  } catch (err) { handleError(err) }
}

export async function getMatch(id: string): Promise<Match> {
  try {
    const { data } = await api.get<Match>(`/api/matches/${id}`)
    return data
  } catch (err) { handleError(err) }
}

export async function getPreMatchBrief(matchId: string): Promise<PreMatchBriefData> {
  try {
    const { data } = await api.get<PreMatchBriefData>(`/api/matches/${matchId}/brief`)
    return data
  } catch (err) { handleError(err) }
}

export async function getPostMatchDebrief(matchId: string): Promise<PostMatchDebriefData> {
  try {
    const { data } = await api.get<PostMatchDebriefData>(`/api/matches/${matchId}/debrief`)
    return data
  } catch (err) { handleError(err) }
}

// ── VOTING ────────────────────────────────────────────────────────────────────
export async function castVote(payload: VotePayload): Promise<VoteResponse> {
  try {
    const { data } = await api.post<VoteResponse>('/api/vote', payload)
    return data
  } catch (err) { handleError(err) }
}

export async function castPenaltyVote(payload: PenaltyVotePayload): Promise<VoteResponse> {
  try {
    const { data } = await api.post<VoteResponse>('/api/vote/penalty', payload)
    return data
  } catch (err) { handleError(err) }
}

export async function getVoteTotals(matchId: string): Promise<VoteTotals> {
  try {
    const { data } = await api.get<VoteTotals>(`/api/matches/${matchId}/votes`)
    return data
  } catch (err) { handleError(err) }
}

// ── ADMIN ─────────────────────────────────────────────────────────────────────
export async function getSystemStatus(password: string): Promise<SystemStatus> {
  try {
    const { data } = await api.get<SystemStatus>('/admin/status', adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

export async function getModelStatus(password: string): Promise<ModelStatus> {
  try {
    const { data } = await api.get<ModelStatus>('/admin/model', adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

export async function getResearchStats(password: string): Promise<ResearchStats> {
  try {
    const { data } = await api.get<ResearchStats>('/admin/research', adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

export async function getPostDrafts(password: string): Promise<PostDraft[]> {
  try {
    const { data } = await api.get<PostDraft[]>('/admin/drafts', adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

export async function triggerRetrain(password: string): Promise<{ message: string; job_id?: string }> {
  try {
    const { data } = await api.post('/admin/retrain', {}, adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

export async function triggerSheetSync(password: string): Promise<{ message: string }> {
  try {
    const { data } = await api.post('/admin/sync-sheets', {}, adminHeaders(password))
    return data
  } catch (err) { handleError(err) }
}

// ── HEALTH ────────────────────────────────────────────────────────────────────
export async function getHealth(): Promise<{ status: string }> {
  try {
    const { data } = await api.get<{ status: string }>('/health', { timeout: 5000 })
    return data
  } catch {
    return { status: 'down' }
  }
}

// ── SSE ───────────────────────────────────────────────────────────────────────
export interface SSEConnection { close: () => void }

export function connectMatchSSE(
  matchId: string,
  onMessage: (msg: SSEMessage) => void,
  onError?: (err: Event) => void
): SSEConnection {
  const source = new EventSource(`${BASE}/stream/${matchId}`)
  source.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)) } catch { /* skip malformed */ }
  }
  source.onerror = (err) => onError?.(err)
  return { close: () => source.close() }
}

export function connectGlobalSSE(
  onMessage: (msg: SSEMessage) => void,
  onError?: (err: Event) => void
): SSEConnection {
  const source = new EventSource(`${BASE}/stream/all`)
  source.onmessage = (e) => {
    try { onMessage(JSON.parse(e.data)) } catch { /* skip */ }
  }
  source.onerror = (err) => onError?.(err)
  return { close: () => source.close() }
}

// ── KEEP-ALIVE (Render free tier — ping every 13 min) ─────────────────────────
let _keepAlive: ReturnType<typeof setInterval> | null = null

export function startKeepAlive() {
  if (_keepAlive) return
  _keepAlive = setInterval(() => {
    api.get('/health', { timeout: 5000 }).catch(() => {})
  }, 13 * 60 * 1000)
}

export function stopKeepAlive() {
  if (_keepAlive) { clearInterval(_keepAlive); _keepAlive = null }
}
