// types/index.ts — Project Delta — single source of truth

// ── MATCH STATES ──────────────────────────────────────────────────────────────
export type MatchState =
  | 'SCHEDULED' | 'LIVE' | 'HT' | 'LIVE_2H' | 'FT'
  | 'ET_1H' | 'ET_HT' | 'ET_2H' | 'PENALTIES' | 'FINISHED' | 'VOID'

export type MatchPhase = 'group' | 'r32' | 'r16' | 'qf' | 'sf' | 'third' | 'final'

export type EventType =
  | 'goal' | 'own_goal' | 'yellow_card' | 'red_card' | 'substitution'
  | 'penalty_scored' | 'penalty_missed' | 'var_review'
  | 'kickoff' | 'half_time' | 'full_time' | 'extra_time' | 'penalty_shootout'

// ── CORE DATA SHAPES ──────────────────────────────────────────────────────────
export interface Score { home: number; away: number }

export interface Team {
  name: string
  code: string
  fifa_rank: number
}

export interface MatchEvent {
  type: EventType
  minute: string
  player: string
  team: string
  sentiment?: 'dramatic' | 'routine' | 'controversial'
  context?: string
}

export interface Confidence {
  home_win: number
  draw: number
  away_win: number
}

export interface ConfidenceRange {
  home_win: string
  draw: string
  away_win: string
}

// ── PENALTY ───────────────────────────────────────────────────────────────────
export interface PenaltyKick {
  team: 'home' | 'away'
  player: string
  scored: boolean
  order: number
}

export interface PenaltyState {
  home_scored: number
  home_missed: number
  away_scored: number
  away_missed: number
  kicks: PenaltyKick[]
  current_team: 'home' | 'away'
  round: number
}

// ── LIVE MATCH DATA (from SSE) ────────────────────────────────────────────────
export interface LiveMatchData {
  match_id: string
  timestamp: string
  state: MatchState
  score: Score
  minute: string
  period: number
  events: MatchEvent[]
  ai_context: string
  model_confidence: Confidence
  confidence_shift: string
  source_used: string
  parse_latency_ms: number
  model_version: number
  training_match_count: number
  penalty_state?: PenaltyState
}

// ── AI PREDICTION ─────────────────────────────────────────────────────────────
export interface AIPrediction {
  home_win: number
  draw: number
  away_win: number
  confidence_range: ConfidenceRange
  predicted_scorer?: string
  predicted_score?: string
  model_version: number
  training_match_count: number
  reasoning?: string
  locked: boolean
  locked_at_minute?: number
}

// ── FULL MATCH ────────────────────────────────────────────────────────────────
export interface Match {
  match_id: string
  phase: MatchPhase
  group?: string
  match_number: number
  home: Team
  away: Team
  kickoff_utc: string
  venue: string
  city: string
  state: MatchState
  score: Score
  minute: string
  events: MatchEvent[]
  pre_match_brief?: string | null
  post_match_debrief?: string | null
  ai_prediction?: AIPrediction | null
  live?: LiveMatchData
  went_to_penalties?: boolean
  penalties?: { home: number; away: number } | null
}

// ── TOURNAMENT ────────────────────────────────────────────────────────────────
export interface TournamentDay {
  date: string
  matches: Match[]
}

export interface MatchesResponse {
  days: TournamentDay[]
  total_matches: number
  completed_matches: number
  live_count: number
}

// ── VOTING ────────────────────────────────────────────────────────────────────
export type VotePick = 'home' | 'draw' | 'away'

export interface VotePayload {
  match_id: string
  pick: VotePick
  turnstile_token?: string
  fingerprint_hash: string
  session_id: string
}

export interface VoteResponse {
  success: boolean
  message: string
  distribution?: VoteDistribution
}

export interface VoteDistribution {
  home: number
  draw: number
  away: number
  total: number
  home_pct: string
  draw_pct: string
  away_pct: string
}

export interface VoteTotals {
  match_id: string
  distribution: VoteDistribution
}

export interface PenaltyVotePayload {
  match_id: string
  pick: 'home' | 'away'
  round: number
  turnstile_token?: string
  fingerprint_hash: string
}

// ── PRE / POST MATCH ──────────────────────────────────────────────────────────
export interface TeamNewsItem {
  text: string
  source_name: string
  source_url: string
  team: 'home' | 'away'
}

export interface PreMatchBriefData {
  match_id: string
  generated_at: string
  team_news: TeamNewsItem[]
  key_battles: string[]
  ai_reasoning: string
  prediction_summary: string
}

export interface ModelUpdate {
  type: 'rating' | 'scorer_probability' | 'form'
  team?: string
  player?: string
  delta: number
  description: string
}

export interface PostMatchDebriefData {
  match_id: string
  ai_correct_winner: boolean
  ai_correct_scorer: boolean
  what_model_got_wrong: string
  model_updates: ModelUpdate[]
  summary: string
}

// ── SSE ───────────────────────────────────────────────────────────────────────
export interface SSEMessage {
  type:
    | 'match_update'
    | 'goal'
    | 'state_change'
    | 'prediction_update'
    | 'vote_update'
    | 'penalty_update'
    | 'heartbeat'
    | 'error'
  match_id: string
  data: Partial<Match & LiveMatchData>
  timestamp: string
}

// ── ADMIN ─────────────────────────────────────────────────────────────────────
export interface SourceStatus {
  name: string
  status: 'ok' | 'blocked' | 'slow' | 'unknown'
  last_success?: string
  block_count_today: number
}

export interface SystemStatus {
  api_healthy: boolean
  db_healthy: boolean
  sse_connections: number
  render_cpu_pct: number
  render_memory_pct: number
  render_hours_used: number
  groq_key1_pct: number
  groq_key2_pct: number
  sources: SourceStatus[]
  last_check: string
}

export interface AccuracyPoint {
  version: number
  accuracy: number
  match_id: string
  deployed: boolean
}

export interface ModelStatus {
  current_version: number
  current_accuracy: number
  training_match_count: number
  last_trained_at: string
  last_trained_match: string
  deploy_threshold: number
  accuracy_history: AccuracyPoint[]
}

export interface TrustBucket { label: string; count: number; pct: number }
export interface VotesPerMatch { match_id: string; label: string; total: number; verified: number }

export interface ResearchStats {
  total_votes: number
  verified_votes: number
  probable_votes: number
  excluded_votes: number
  votes_today: number
  votes_per_match: VotesPerMatch[]
  trust_distribution: TrustBucket[]
  sheets_last_sync: string
  sheets_sync_ok: boolean
}

export interface PostDraft {
  platform: 'linkedin' | 'x'
  content: string
  match_id: string
  generated_at: string
}

export interface AdminData {
  system: SystemStatus
  model: ModelStatus
  research: ResearchStats
  drafts: PostDraft[]
}
