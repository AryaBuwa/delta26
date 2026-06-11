'use client'
// components/match/EventTicker.tsx
import { motion, AnimatePresence } from 'framer-motion'
import type { MatchEvent } from '@/types'
import { getEventIcon } from '@/lib/utils'

interface Props {
  events: MatchEvent[]
  maxItems?: number
}

export function EventTicker({ events, maxItems = 5 }: Props) {
  const shown = [...events].reverse().slice(0, maxItems)

  if (shown.length === 0) return null

  return (
    <div className="border-t px-5 py-3 flex flex-col gap-2" style={{ borderColor: 'var(--line-dim)' }}>
      <AnimatePresence initial={false}>
        {shown.map((ev, i) => (
          <motion.div
            key={`${ev.minute}-${ev.player}-${i}`}
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="flex items-center gap-2.5"
          >
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)', minWidth: 28 }}>
              {ev.minute}&apos;
            </span>
            <span style={{ fontSize: 13 }}>{getEventIcon(ev.type)}</span>
            <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{ev.player}</strong>
              {ev.context && <> — <span>{ev.context}</span></>}
            </span>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  )
}
