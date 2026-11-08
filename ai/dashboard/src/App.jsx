import { useEffect, useState } from 'react'
import { startLaunch, finishLaunch } from './smart.js'
import { api, setToken } from './api.js'
import { Banner, Why, About } from './parts.jsx'
import Overview from './Overview.jsx'
import Timeline from './Timeline.jsx'
import Ask from './Ask.jsx'
import PriorAuth from './PriorAuth.jsx'
import Coding from './Coding.jsx'

const TABS = [['overview', 'Overview'], ['timeline', 'Timeline'], ['ask', 'Ask the chart'], ['pa', 'Prior authorization'], ['coding', 'Coding']]

export default function App() {
  const [phase, setPhase] = useState('start')     // start | signing-in | ready | no-access | error
  const [error, setError] = useState('')
  const [session, setSession] = useState(null)
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('overview')
  const [why, setWhy] = useState(null)            // what the "Why" panel shows
  const [about, setAbout] = useState(false)

  useEffect(() => {
    const expired = () => { setToken(null); setSession(null); setData(null); setWhy(null); setPhase('expired') }
    window.addEventListener('copilot-session-expired', expired)
    return () => window.removeEventListener('copilot-session-expired', expired)
  }, [])

  useEffect(() => {
    const q = new URLSearchParams(location.search)
    const path = location.pathname
    ;(async () => {
      try {
        if (path.endsWith('/launch') && q.get('iss') && q.get('launch')) { setPhase('signing-in'); await startLaunch(q.get('iss'), q.get('launch')); return }
        if (path.endsWith('/auth/callback')) {
          if (q.get('error')) throw new Error(q.get('error_description') || q.get('error'))
          setPhase('signing-in')
          const s = await finishLaunch(q.get('code'), q.get('state'))
          setToken(s.token); setSession(s)
          history.replaceState({}, '', '/ai/')
          try { await load(s.patient) } catch (e) {
            if (e.status === 403 && /outside your assignment/.test(e.message)) { setPhase('no-access'); return }   // signed in, but this chart is on another unit
            throw e
          }
          setPhase('ready'); return
        }
        setPhase('start')
      } catch (e) { setError(e.message); setPhase('error') }
    })()
  }, [])

  async function load(patient) { setData(await api(`/patients/${patient}/dashboard`)) }
  const reload = () => load(session.patient)

  if (phase === 'start') return <Centered title="Open from the patient chart">This app is launched from inside the EHR so it knows which patient you are looking at. In MediTrack open a patient and choose <b>AI Clinical Insights</b>.</Centered>
  if (phase === 'signing-in') return <Centered title="Signing you in…">Connecting to the EHR securely (SMART on FHIR).</Centered>
  if (phase === 'expired') return <Centered title="Session expired">Return to MediTrack, sign in if needed, and reopen AI Clinical Insights from the patient chart.</Centered>
  if (phase === 'error') return <Centered title="Could not open the patient" bad>{error}</Centered>
  if (phase === 'no-access') return <NoAccess session={session} onGranted={async () => { await reload(); setPhase('ready') }} />

  const ctx = { session, data, reload, setWhy, patient: session.patient }
  return (
    <div className="app">
      <Banner data={data} session={session} onAbout={() => setAbout(true)} />
      <div className="body">
        <main>
          <nav className="tabs" role="tablist" aria-label="Sections">
            {TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}
              {id === 'overview' && data.flags.filter(f => f.level === 'attention').length > 0 && <span className="count">{data.flags.filter(f => f.level === 'attention').length}</span>}
              {id === 'pa' && data.prior_auth.length > 0 && <span className="count soft">{data.prior_auth.length}</span>}</button>)}
          </nav>
          {tab === 'overview' && <Overview {...ctx} />}
          {tab === 'timeline' && <Timeline {...ctx} />}
          {tab === 'ask' && <Ask {...ctx} />}
          {tab === 'pa' && <PriorAuth {...ctx} />}
          {tab === 'coding' && <Coding {...ctx} />}
        </main>
        <Why why={why} iss={session.iss} onClose={() => setWhy(null)} />
      </div>
      {about && <About onClose={() => setAbout(false)} />}
    </div>
  )
}

function Centered({ title, children, bad }) {
  return <div className="centered"><div className={`card ${bad ? 'bad' : ''}`}><h1>{title}</h1><p>{children}</p></div></div>
}

// The chart is on another unit. Clinicians with a real need (a transfer, a 3 a.m. consult) get time-boxed access with a documented reason:
// granted at once, logged loudly, reviewed by the privacy officer (HIPAA emergency access). Roles that may not break the glass are told so.
function NoAccess({ session, onGranted }) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ok = reason.trim().length >= 15
  async function request(e) {
    e.preventDefault(); setBusy(true); setErr('')
    try { await api(`/patients/${session.patient}/break-glass`, { method: 'POST', body: { reason: reason.trim() } }); await onGranted() }
    catch (x) { setErr(x.status === 403 ? `${x.message}. Ask a clinician assigned to this unit, or open a patient on your own unit.` : x.message); setBusy(false) }
  }
  return (
    <div className="centered"><form className="card" onSubmit={request}>
      <h1>This patient is on another unit</h1>
      <p>You are signed in{session.user ? <> as <b>{session.user}</b>{session.role ? <> ({session.role})</> : null}</> : null}, and your access covers your own unit. Nothing about this patient is shown until access is granted.</p>
      <p>If you need this chart for care right now (a transfer, an overnight consult), you can request <b>emergency access</b>. It is granted immediately for 4 hours, recorded in the audit log, and reviewed by the privacy officer.</p>
      <label htmlFor="reason" className="block">Clinical reason (at least 15 characters)</label>
      <textarea id="reason" rows={3} style={{ width: '100%', font: 'inherit', padding: '.5rem' }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Patient transferred to my unit overnight; need the discharge plan to continue care" />
      {err && <p role="alert" style={{ color: 'var(--bad)' }}>{err}</p>}
      <div className="actions end"><button className="primary" disabled={!ok || busy}>{busy ? 'Requesting…' : 'Request emergency access'}</button></div>
      <p className="muted small">Not an emergency? Open a patient on your own unit, or ask the clinician assigned to this unit.</p>
    </form></div>
  )
}
