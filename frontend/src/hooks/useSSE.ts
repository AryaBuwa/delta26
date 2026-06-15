'use client'

// hooks/useSSE.ts — Project Delta
// SSE hook: connects to backend stream, syncs to Zustand store
//
// Session 8 fix: removed `options` param from useMatchSSE entirely.
// The options object was recreated on every render, causing handleMessage
// to change → connect to change → useEffect to re-run → infinite reconnect loop.
// page.tsx calls useMatchSSE(matchId) with no options anyway, so this is clean.
// useGlobalSSE: stabilised onGoal/onStateChange callbacks with useRef.

import { useEffect, useRef, useCallback, useState } from 'react'
import { connectMatchSSE, connectGlobalSSE } from '@/lib/api'
import { useMatchStore } from '@/lib/store'
import type { SSEMessage, Match } from '@/types'

// ── SINGLE MATCH SSE ──────────────────────────────────────────────────────────
// No options param — was the root cause of the infinite reconnect loop.
// If you need onGoal/onStateChange callbacks in future, pass them as stable
// refs (useRef) rather than inline objects.

export function useMatchSSE(matchId: string) {
  const updateMatch = useMatchStore((s) => s.updateMatch)
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null)
  const connRef = useRef<{ close: () => void } | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount = useRef(0)
  // Stable ref to updateMatch — never changes, but avoids any closure staleness
  const updateMatchRef = useRef(updateMatch)
  useEffect(() => { updateMatchRef.current = updateMatch }, [updateMatch])

  const handleMessage = useCallback((msg: SSEMessage) => {
    if (msg.match_id !== matchId) return
    setLastUpdate(new Date())

    switch (msg.type) {
      case 'match_update':
      case 'prediction_update':
      case 'vote_update':
      case 'goal':
      case 'state_change':
      case 'penalty_update':
        updateMatchRef.current(matchId, msg.data)
        break
      case 'heartbeat':
        // connection alive — no state update needed
        break
      case 'error':
        console.warn('[SSE] Server error:', msg)
        break
    }
  }, [matchId]) // matchId is stable for a given page mount — safe dep

  const connect = useCallback(() => {
    if (!matchId) return
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
        // Exponential backoff: 2s, 4s, 8s, 16s, max 30s
        const delay = Math.min(30_000, 2_000 * Math.pow(2, retryCount.current))
        retryCount.current += 1
        retryRef.current = setTimeout(connect, delay)
      }
    )

    connRef.current = conn
  }, [matchId, handleMessage]) // handleMessage only changes when matchId changes

  useEffect(() => {
    connect()
    return () => {
      connRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [connect]) // connect only changes when matchId changes — no loop

  return { connected, lastUpdate }
}


// ── GLOBAL SSE (all matches) ──────────────────────────────────────────────────
// onGoal and onStateChange are optional callbacks.
// Stabilised with useRef so they don't trigger reconnects when parent re-renders.

interface GlobalSSEOptions {
  enabled?: boolean
  onGoal?: (matchId: string, data: Partial<Match>) => void
  onStateChange?: (matchId: string, newState: string) => void
}

export function useGlobalSSE(options: GlobalSSEOptions = {}) {
  const { enabled = true } = options

  const setMatch = useMatchStore((s) => s.setMatch)
  const updateMatch = useMatchStore((s) => s.updateMatch)
  const [connected, setConnected] = useState(false)

  const connRef = useRef<{ close: () => void } | null>(null)
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const retryCount = useRef(0)

  // Stabilise callbacks — update the ref when the function changes,
  // but never use the callback directly as a useCallback/useEffect dep
  const onGoalRef = useRef(options.onGoal)
  const onStateChangeRef = useRef(options.onStateChange)
  useEffect(() => { onGoalRef.current = options.onGoal }, [options.onGoal])
  useEffect(() => { onStateChangeRef.current = options.onStateChange }, [options.onStateChange])

  const setMatchRef = useRef(setMatch)
  const updateMatchRef = useRef(updateMatch)
  useEffect(() => { setMatchRef.current = setMatch }, [setMatch])
  useEffect(() => { updateMatchRef.current = updateMatch }, [updateMatch])

  // handleMessage has NO external deps that could cause reconnect loops
  const handleMessage = useCallback((msg: SSEMessage) => {
    if (!msg.match_id) return

    switch (msg.type) {
      case 'match_update':
        if (msg.data && 'id' in msg.data) {
          setMatchRef.current(msg.data as Match)
        } else {
          updateMatchRef.current(msg.match_id, msg.data)
        }
        break

      case 'goal':
        updateMatchRef.current(msg.match_id, msg.data)
        onGoalRef.current?.(msg.match_id, msg.data)
        break

      case 'state_change':
        updateMatchRef.current(msg.match_id, msg.data)
        if (msg.data.state) {
          onStateChangeRef.current?.(msg.match_id, msg.data.state)
        }
        break

      case 'heartbeat':
        break
    }
  }, []) // zero deps — all state accessed via stable refs

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
  }, [enabled, handleMessage]) // handleMessage is stable (zero deps above)

  useEffect(() => {
    connect()
    return () => {
      connRef.current?.close()
      if (retryRef.current) clearTimeout(retryRef.current)
    }
  }, [connect])

  return { connected }
}
