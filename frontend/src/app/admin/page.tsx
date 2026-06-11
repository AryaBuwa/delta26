'use client'
// app/admin/page.tsx — Password-protected admin dashboard
import { useState, useEffect, useCallback } from 'react'
import { motion } from 'framer-motion'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, PieChart, Pie, Cell
} from 'recharts'
import type { SystemStatus, ModelStatus, ResearchStats, PostDraft } from '@/types'
import {
  getSystemStatus, getModelStatus, getResearchStats,
  getPostDrafts, triggerRetrain, triggerSheetSync
} from '@/lib/api'
import { useAdminAuth } from '@/lib/store'
import { Spinner } from '@/components/ui'
import toast from 'react-hot-toast'

// ── LOGIN SCREEN ──────────────────────────────────────────────────────────────
function LoginScreen({ onAuth }: { onAuth: (p: string) => void }) {
  const [pw, setPw] = useState('')
  const [error, setError] = useState('')

  async function handleSubmit() {
    if (!pw.trim()) return
    try {
      await getSystemStatus(pw)
      onAuth(pw)
    } catch {
      setError('Wrong password')
    }
  }

  return (
    <div className="page flex flex-col items-center justify-center" style={{ paddingTop: 120 }}>
      <div className="card" style={{ width: '100%', maxWidth: 360, padding: 32 }}>
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 20 }}>
          Admin Access
        </p>
        <input
          type="password"
          value={pw}
          onChange={e => setPw(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSubmit()}
          placeholder="Password"
          style={{
            width: '100%',
            background: 'var(--bg-surface)',
            border: '1px solid var(--line-soft)',
            borderRadius: 3,
            padding: '10px 14px',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
            fontSize: 13,
            outline: 'none',
            marginBottom: error ? 8 : 16,
          }}
        />
        {error && <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--accent-red)', marginBottom: 12 }}>{error}</p>}
        <button
          onClick={handleSubmit}
          style={{
            width: '100%',
            background: 'var(--accent)',
            color: 'var(--bg-base)',
            border: 'none',
            borderRadius: 3,
            padding: '10px',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            fontWeight: 500,
            letterSpacing: '0.1em',
            cursor: 'pointer',
          }}
        >
          Enter
        </button>
      </div>
    </div>
  )
}

// ── SECTION WRAPPER ───────────────────────────────────────────────────────────
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h2 style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.16em', textTransform: 'uppercase', color: 'var(--text-dim)', marginBottom: 12 }}>
        {title}
      </h2>
      {children}
    </div>
  )
}

// ── SOURCE HEALTH GRID ────────────────────────────────────────────────────────
function SourceGrid({ sources }: { sources: SystemStatus['sources'] }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 4 }}>
      {sources.map(s => (
        <div key={s.name} className="card" style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{
            width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
            background: s.status === 'ok' ? 'var(--accent-green)' : s.status === 'blocked' ? 'var(--accent-red)' : 'var(--accent-orange)',
          }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {s.name}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── STATUS PANEL ──────────────────────────────────────────────────────────────
function StatusPanel({ status }: { status: SystemStatus }) {
  const metrics = [
    { label: 'SSE Connections', value: String(status.sse_connections) },
    { label: 'CPU', value: `${status.render_cpu_pct}%`, warn: status.render_cpu_pct > 80 },
    { label: 'Memory', value: `${status.render_memory_pct}%`, warn: status.render_memory_pct > 80 },
    { label: 'Hours Used', value: `${status.render_hours_used}h`, warn: status.render_hours_used > 700 },
    { label: 'Groq Key 1', value: `${status.groq_key1_pct}%`, warn: status.groq_key1_pct > 80 },
    { label: 'Groq Key 2', value: `${status.groq_key2_pct}%`, warn: status.groq_key2_pct > 80 },
  ]

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 4 }}>
      {metrics.map(({ label, value, warn }) => (
        <div key={label} className="card" style={{ padding: '10px 14px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4 }}>
            {label}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: warn ? 'var(--accent-red)' : 'var(--text-primary)', fontWeight: 500 }}>
            {value}
          </div>
        </div>
      ))}
    </div>
  )
}

// ── MODEL PANEL ───────────────────────────────────────────────────────────────
function ModelPanel({ model, password }: { model: ModelStatus; password: string }) {
  const [retraining, setRetraining] = useState(false)

  async function handleRetrain() {
    setRetraining(true)
    try {
      const res = await triggerRetrain(password)
      toast.success(res.message)
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : 'Retrain failed')
    } finally {
      setRetraining(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 4 }}>
        {[
          { label: 'Version', value: `v${model.current_version}` },
          { label: 'Accuracy', value: `${(model.current_accuracy * 100).toFixed(1)}%` },
          { label: 'Trained on', value: `${model.training_match_count} matches` },
        ].map(({ label, value }) => (
          <div key={label} className="card" style={{ padding: '10px 14px' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, color: 'var(--text-primary)', fontWeight: 500 }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Accuracy curve */}
      {model.accuracy_history.length > 1 && (
        <div className="card" style={{ padding: 16 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 12 }}>
            Accuracy Over Tournament
          </p>
          <ResponsiveContainer width="100%" height={140}>
            <LineChart data={model.accuracy_history}>
              <XAxis dataKey="version" tick={{ fontSize: 9, fontFamily: 'var(--font-mono)', fill: 'var(--text-dim)' }} />
              <YAxis domain={[0.4, 1]} tick={{ fontSize: 9, fontFamily: 'var(--font-mono)', fill: 'var(--text-dim)' }} />
              <Tooltip
                contentStyle={{ background: 'var(--bg-overlay)', border: '1px solid var(--line-soft)', borderRadius: 3, fontSize: 10, fontFamily: 'var(--font-mono)' }}
                itemStyle={{ color: 'var(--text-muted)' }}
              />
              <Line type="monotone" dataKey="accuracy" stroke="var(--accent)" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Retrain button */}
      <button
        onClick={handleRetrain}
        disabled={retraining}
        style={{
          background: retraining ? 'var(--bg-surface)' : 'var(--accent)',
          color: retraining ? 'var(--text-dim)' : 'var(--bg-base)',
          border: 'none',
          borderRadius: 3,
          padding: '10px 20px',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          letterSpacing: '0.1em',
          cursor: retraining ? 'not-allowed' : 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: 'fit-content',
        }}
      >
        {retraining && <Spinner />}
        {retraining ? 'Retraining…' : 'Retrain Model'}
      </button>
    </div>
  )
}

// ── RESEARCH PANEL ────────────────────────────────────────────────────────────
function ResearchPanel({ stats, password }: { stats: ResearchStats; password: string }) {
  const COLOURS = ['var(--accent)', 'var(--accent-blue)', 'var(--accent-red)']

  async function handleSync() {
    try {
      await triggerSheetSync(password)
      toast.success('Sheets sync triggered')
    } catch {
      toast.error('Sync failed')
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Totals */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: 4 }}>
        {[
          { label: 'Total votes', value: stats.total_votes.toLocaleString() },
          { label: 'Verified', value: stats.verified_votes.toLocaleString(), accent: true },
          { label: 'Today', value: stats.votes_today.toLocaleString() },
          { label: 'Excluded', value: stats.excluded_votes.toLocaleString(), warn: true },
        ].map(({ label, value, accent, warn }) => (
          <div key={label} className="card" style={{ padding: '10px 14px' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 500, color: accent ? 'var(--accent-green)' : warn ? 'var(--accent-red)' : 'var(--text-primary)' }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Trust distribution */}
      {stats.trust_distribution.length > 0 && (
        <div className="card" style={{ padding: 16 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginBottom: 12, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Trust Distribution
          </p>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <PieChart width={100} height={100}>
              <Pie data={stats.trust_distribution} dataKey="count" cx={50} cy={50} innerRadius={28} outerRadius={44}>
                {stats.trust_distribution.map((_, i) => (
                  <Cell key={i} fill={COLOURS[i % COLOURS.length]} />
                ))}
              </Pie>
            </PieChart>
            <div className="flex flex-col gap-2">
              {stats.trust_distribution.map((b, i) => (
                <div key={b.label} className="flex items-center gap-2">
                  <div style={{ width: 8, height: 8, borderRadius: 1, background: COLOURS[i % COLOURS.length], flexShrink: 0 }} />
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-muted)' }}>
                    {b.label} — {b.pct.toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Votes per match bar chart */}
      {stats.votes_per_match.length > 0 && (
        <div className="card" style={{ padding: 16 }}>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginBottom: 12, letterSpacing: '0.1em', textTransform: 'uppercase' }}>
            Votes Per Match
          </p>
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={stats.votes_per_match}>
              <Bar dataKey="total" fill="var(--accent)" opacity={0.7} radius={[1, 1, 0, 0]} />
              <Bar dataKey="verified" fill="var(--accent-green)" opacity={0.9} radius={[1, 1, 0, 0]} />
              <Tooltip
                contentStyle={{ background: 'var(--bg-overlay)', border: '1px solid var(--line-soft)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Sheets sync */}
      <div className="flex items-center justify-between">
        <div>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: stats.sheets_sync_ok ? 'var(--accent-green)' : 'var(--accent-red)' }}>
            {stats.sheets_sync_ok ? '✓' : '✗'} Sheets
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', marginLeft: 8 }}>
            Last sync: {new Date(stats.sheets_last_sync).toLocaleTimeString()}
          </span>
        </div>
        <button
          onClick={handleSync}
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--line-soft)',
            color: 'var(--text-muted)',
            borderRadius: 3,
            padding: '6px 14px',
            fontFamily: 'var(--font-mono)',
            fontSize: 9,
            letterSpacing: '0.1em',
            cursor: 'pointer',
          }}
        >
          Sync Now
        </button>
      </div>
    </div>
  )
}

// ── POST DRAFTS ───────────────────────────────────────────────────────────────
function DraftsPanel({ drafts }: { drafts: PostDraft[] }) {
  const [copied, setCopied] = useState<string | null>(null)

  function copy(content: string, id: string) {
    navigator.clipboard.writeText(content)
    setCopied(id)
    setTimeout(() => setCopied(null), 2000)
    toast.success('Copied to clipboard', { duration: 1500 })
  }

  return (
    <div className="flex flex-col gap-4">
      {drafts.map((d) => (
        <div key={`${d.platform}-${d.match_id}`} className="card" style={{ padding: 16 }}>
          <div className="flex items-center justify-between mb-3">
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, letterSpacing: '0.14em', textTransform: 'uppercase', color: d.platform === 'linkedin' ? 'var(--accent-blue)' : 'var(--text-dim)' }}>
              {d.platform}
            </span>
            <button
              onClick={() => copy(d.content, `${d.platform}-${d.match_id}`)}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--line-soft)',
                color: copied === `${d.platform}-${d.match_id}` ? 'var(--accent-green)' : 'var(--text-dim)',
                borderRadius: 3,
                padding: '4px 10px',
                fontFamily: 'var(--font-mono)',
                fontSize: 9,
                cursor: 'pointer',
                letterSpacing: '0.1em',
              }}
            >
              {copied === `${d.platform}-${d.match_id}` ? 'Copied ✓' : 'Copy'}
            </button>
          </div>
          <pre style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.7, whiteSpace: 'pre-wrap', fontFamily: 'var(--font-sans)' }}>
            {d.content}
          </pre>
        </div>
      ))}
      {drafts.length === 0 && (
        <p style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--text-dim)' }}>
          No drafts yet. Generated after each match.
        </p>
      )}
    </div>
  )
}

// ── TABS ──────────────────────────────────────────────────────────────────────
const TABS = ['Live', 'Model', 'Research', 'Drafts'] as const
type Tab = typeof TABS[number]

// ── MAIN PAGE ─────────────────────────────────────────────────────────────────
export default function AdminPage() {
  const { adminPassword, setAdminPassword } = useAdminAuth()
  const [tab, setTab] = useState<Tab>('Live')
  const [loading, setLoading] = useState(false)

  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [model, setModel] = useState<ModelStatus | null>(null)
  const [research, setResearch] = useState<ResearchStats | null>(null)
  const [drafts, setDrafts] = useState<PostDraft[]>([])

  const load = useCallback(async (pw: string) => {
    setLoading(true)
    try {
      const [s, m, r, d] = await Promise.allSettled([
        getSystemStatus(pw),
        getModelStatus(pw),
        getResearchStats(pw),
        getPostDrafts(pw),
      ])
      if (s.status === 'fulfilled') setStatus(s.value)
      if (m.status === 'fulfilled') setModel(m.value)
      if (r.status === 'fulfilled') setResearch(r.value)
      if (d.status === 'fulfilled') setDrafts(d.value)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (adminPassword) load(adminPassword)
  }, [adminPassword, load])

  // Refresh live every 60s
  useEffect(() => {
    if (!adminPassword) return
    const t = setInterval(() => {
      getSystemStatus(adminPassword).then(setStatus).catch(() => {})
    }, 60_000)
    return () => clearInterval(t)
  }, [adminPassword])

  if (!adminPassword) {
    return <LoginScreen onAuth={(pw) => { setAdminPassword(pw); load(pw) }} />
  }

  return (
    <div className="page" style={{ paddingTop: 24, paddingBottom: 64 }}>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 style={{ fontFamily: 'var(--font-sans)', fontSize: 22, fontWeight: 800, letterSpacing: '-0.03em' }}>
            Admin
          </h1>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--text-dim)', letterSpacing: '0.1em', textTransform: 'uppercase', marginTop: 2 }}>
            DEΔTA · World Cup 2026
          </p>
        </div>
        <div className="flex items-center gap-3">
          {loading && <Spinner className="w-4 h-4 text-dim" />}
          <button
            onClick={() => load(adminPassword)}
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--line-soft)', color: 'var(--text-dim)', borderRadius: 3, padding: '6px 12px', fontFamily: 'var(--font-mono)', fontSize: 9, cursor: 'pointer', letterSpacing: '0.1em' }}
          >
            Refresh
          </button>
          <button
            onClick={() => setAdminPassword(null)}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)', fontSize: 9, cursor: 'pointer', letterSpacing: '0.1em' }}
          >
            Log out
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-6" style={{ borderBottom: '1px solid var(--line-dim)', paddingBottom: 0 }}>
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: 'none',
              border: 'none',
              borderBottom: tab === t ? '2px solid var(--accent)' : '2px solid transparent',
              padding: '8px 16px',
              fontFamily: 'var(--font-mono)',
              fontSize: 10,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: tab === t ? 'var(--text-primary)' : 'var(--text-dim)',
              cursor: 'pointer',
              marginBottom: -1,
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <motion.div key={tab} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.15 }}>
        {tab === 'Live' && status && (
          <div className="flex flex-col gap-6">
            <Section title="System">{<StatusPanel status={status} />}</Section>
            <Section title="Sources">{<SourceGrid sources={status.sources} />}</Section>
          </div>
        )}
        {tab === 'Model' && model && (
          <ModelPanel model={model} password={adminPassword} />
        )}
        {tab === 'Research' && research && (
          <ResearchPanel stats={research} password={adminPassword} />
        )}
        {tab === 'Drafts' && (
          <DraftsPanel drafts={drafts} />
        )}
      </motion.div>
    </div>
  )
}
