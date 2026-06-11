'use client'
// hooks/useFingerprint.ts — Project Delta
// Browser fingerprint hash for bot prevention (no PII stored)

import { useState, useEffect } from 'react'

function hashString(str: string): string {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i)
    hash = (hash << 5) - hash + char
    hash = hash & hash
  }
  return Math.abs(hash).toString(36).padStart(8, '0')
}

function getSessionId(): string {
  if (typeof window === 'undefined') return 'ssr'
  const key = 'delta-sid'
  let sid = sessionStorage.getItem(key)
  if (!sid) {
    sid = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2)
    sessionStorage.setItem(key, sid)
  }
  return sid
}

async function buildFingerprint(): Promise<string> {
  const components: string[] = []

  // Screen
  components.push(`${window.screen.width}x${window.screen.height}x${window.screen.colorDepth}`)

  // Timezone
  components.push(Intl.DateTimeFormat().resolvedOptions().timeZone)

  // Language
  components.push(navigator.language)

  // Platform
  components.push(navigator.platform || 'unknown')

  // Hardware concurrency
  components.push(String(navigator.hardwareConcurrency ?? 0))

  // Canvas fingerprint (lightweight)
  try {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (ctx) {
      ctx.textBaseline = 'top'
      ctx.font = '14px Arial'
      ctx.fillText('Delta🌍', 2, 2)
      components.push(canvas.toDataURL().slice(-32))
    }
  } catch { components.push('no-canvas') }

  // Session
  components.push(getSessionId())

  return hashString(components.join('|'))
}

export function useFingerprint() {
  const [fingerprint, setFingerprint] = useState<string>('')
  const [sessionId, setSessionId] = useState<string>('')

  useEffect(() => {
    buildFingerprint().then(setFingerprint)
    setSessionId(getSessionId())
  }, [])

  return { fingerprint, sessionId }
}
