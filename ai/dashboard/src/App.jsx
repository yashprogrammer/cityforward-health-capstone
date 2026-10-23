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
  const [phase, setPhase] = useState('start')     // start | signing-in | ready | error
  const [error, setError] = useState('')
  const [session, setSession] = useState(null)
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('overview')
  const [why, setWhy] = useState(null)            // what the "Why" panel shows
  const [about, setAbout] = useState(false)

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
          await load(s.patient)
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
  if (phase === 'error') return <Centered title="Could not open the patient" bad>{error}</Centered>

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
