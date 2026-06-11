'use client'
// components/match/AIContext.tsx
interface Props {
  text: string
  compact?: boolean
}

export function AIContext({ text, compact }: Props) {
  if (!text) return null
  return (
    <div
      className="flex gap-2.5 items-start border-t px-5 py-3"
      style={{ borderColor: 'var(--line-dim)' }}
    >
      <span className="tag tag-ai flex-shrink-0 mt-0.5">AI GEN</span>
      <p style={{
        fontSize: 12,
        color: 'var(--text-muted)',
        lineHeight: 1.55,
        display: '-webkit-box',
        WebkitLineClamp: compact ? 2 : 4,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden',
      }}>
        {text}
      </p>
    </div>
  )
}
