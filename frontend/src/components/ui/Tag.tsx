// components/ui/Tag.tsx
import { cn } from '@/lib/utils'
import type { MatchState } from '@/types'

interface TagProps {
  children: React.ReactNode
  variant?: 'live' | 'ht' | 'ft' | 'scheduled' | 'penalties' | 'ai' | 'void'
  className?: string
}

export function Tag({ children, variant = 'ft', className }: TagProps) {
  return (
    <span className={cn('tag', `tag-${variant}`, className)}>
      {children}
    </span>
  )
}

export function StateTag({ state }: { state: MatchState }) {
  const isLive = ['LIVE', 'LIVE_2H', 'ET_1H', 'ET_2H'].includes(state)
  const isHT   = ['HT', 'ET_HT'].includes(state)
  const isPen  = state === 'PENALTIES'
  const isSched = state === 'SCHEDULED'

  const variant = isLive ? 'live' : isHT ? 'ht' : isPen ? 'penalties' : isSched ? 'scheduled' : 'ft'

  const labels: Record<MatchState, string> = {
    SCHEDULED: 'Upcoming',
    LIVE:      'Live',
    HT:        'Half Time',
    LIVE_2H:   'Live',
    FT:        'Full Time',
    ET_1H:     'Extra Time',
    ET_HT:     'ET Half Time',
    ET_2H:     'Extra Time',
    PENALTIES: 'Penalties',
    FINISHED:  'Final',
    VOID:      'Void',
  }

  return (
    <span className={cn('tag', `tag-${variant}`, 'flex items-center gap-1.5')}>
      {isLive && <span className="live-dot" />}
      {labels[state]}
    </span>
  )
}
