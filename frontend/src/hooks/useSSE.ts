'use client'

// hooks/useSSE.ts — Project Delta
// SSE hook: connects to backend stream, syncs to Zustand store

import { useEffect, useRef, useCallback, useState } from 'react'
import { connectMatchSSE, connectGlobalSSE } from '@/lib/api'
import { useMatchStore } from '@/lib/store'
import type { SSEMessage, Match } from '@/types'

interface UseSSEOptions {
  enabled?: boolean
  onGoal?: (matchId: string, data: Partial<Match>) => void
  onStateChange?: (matchId: string, newState: string) => void
}

// ── SINGLE MATCH SSE ──────────────────────────────────────────────────────────
export function useMatchSSE(matchId: string, options: UseSSEOptions = {}) {
  const { enabled = true } = options
  const updateMatch = useMatchStore((s) => s.updateMatch)
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const connRef = useRef<{ close: () => void } | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount = useRef(0)

  const handleMessage = useCallback(
    (msg: SSEMessage) => {
      if (msg.match_id !== matchId) return

      setLastUpdate(new Date())

      switch (msg.type) {
        case 'match_update':
        case 'prediction_update':
        case 'vote_update':
          updateMatch(matchId, msg.data)
          break

        case 'goal':
          updateMatch(matchId, msg.data)
          options.onGoal?.(matchId, msg.data)
          break

        case 'state_change':
          updateMatch(matchId, msg.data)
          if (msg.data.state) {
            options.onStateChange?.(matchId, msg.data.state)
          }
          break

        case 'penalty_update':
          updateMatch(matchId, msg.data)
          break

        case 'heartbeat':
          // connection alive — no state update needed
          break

        case 'error':
          console.warn('SSE error from server:', msg)
          break
      }
    },
    [matchId, updateMatch, options]
  )

  const connect = useCallback(() => {
    if (!enabled || !matchId) return

    connRef.current?.close()

    const conn = connectMatchSSE(
      matchId,
      (msg) => {
        setConnected(true)
        retryCount.current = 0
        handleMessage(msg)
      },
      () => {
        setConnected(false)
        // Exponential backoff: 2s, 4s, 8s, max 30s
        const delay = Math.min(30_000, 2_000 * Math.pow(2, retryCount.current))
        retryCount.current += 1
        retryRef.current = setTimeout(connect, delay)
      }
    )

    connRef.current = conn
  }, [matchId, enabled, handleMessage])

  useEffect(() => {
    connect()
    return () => {
      connRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [connect])

  return { connected, lastUpdate }
}

// ── GLOBAL SSE (all matches) ──────────────────────────────────────────────────
export function useGlobalSSE(options: UseSSEOptions = {}) {
  const { enabled = true } = options
  const setMatch = useMatchStore((s) => s.setMatch)
  const updateMatch = useMatchStore((s) => s.updateMatch)
  const [connected, setConnected] = useState(false)
  const connRef = useRef<{ close: () => void } | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount = useRef(0)

  const handleMessage = useCallback(
    (msg: SSEMessage) => {
      if (!msg.match_id) return

      switch (msg.type) {
        case 'match_update':
          if (msg.data && 'id' in msg.data) {
            setMatch(msg.data as Match)
          } else {
            updateMatch(msg.match_id, msg.data)
          }
          break

        case 'goal':
          updateMatch(msg.match_id, msg.data)
          options.onGoal?.(msg.match_id, msg.data)
          break

        case 'state_change':
          updateMatch(msg.match_id, msg.data)
          if (msg.data.state) {
            options.onStateChange?.(msg.match_id, msg.data.state)
          }
          break

        case 'heartbeat':
          break
      }
    },
    [setMatch, updateMatch, options]
  )

  const connect = useCallback(() => {
    if (!enabled) return

    connRef.current?.close()

    const conn = connectGlobalSSE(
      (msg) => {
        setConnected(true)
        retryCount.current = 0
        handleMessage(msg)
      },
      () => {
        setConnected(false)
        const delay = Math.min(30_000, 2_000 * Math.pow(2, retryCount.current))
        retryCount.current += 1
        retryRef.current = setTimeout(connect, delay)
      }
    )

    connRef.current = conn
  }, [enabled, handleMessage])

  useEffect(() => {
    connect()
    return () => {
      connRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [connect])

  return { connected }
}
