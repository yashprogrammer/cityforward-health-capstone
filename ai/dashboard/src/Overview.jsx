import { useEffect, useState } from 'react'
import { api } from './api.js'
import { Chip, ErrorNote, useAction } from './parts.jsx'

const AGENT_LABEL = { summary: 'Stay summary', coding: 'Coding worksheet', 'prior-auth': 'Prior authorization' }
const STATUS_TEXT = { ok: 'ready', degraded: 'ready (no-AI mode)', failed: 'failed', skipped: 'not for your role', 'nothing-to-do': 'nothing to do' }

function Packet({ patient }) {
  const [r, setR] = useState(null)
  const { busy, error, run } = useAction()
  useEffect(() => { api(`/patients/${patient}/readiness`).then((x) => x.generated_at && setR(x)).catch(() => {}) }, [patient])
  const prepare = () => run(async () => setR(await api(`/patients/${patient}/readiness`, { method: 'POST' })))
  return (
    <section className="card" aria-labelledby="packet-h">
      <div className="row"><h2 id="packet-h">Discharge packet</h2><button className="primary" onClick={prepare} disabled={busy}>{busy ? 'Preparing…' : r ? 'Refresh' : 'Prepare discharge packet'}</button></div>
      <ErrorNote error={error} />
      {!r && !busy && <p className="muted">Three specialist assistants prepare the paperwork in parallel (summary, coding, prior authorization). They prepare; people decide.</p>}
      {r && <>
        <ul className="agents">{r.agents.map((a) => <li key={a.agent} className={`ag ${a.status}`}><b>{AGENT_LABEL[a.agent] || a.agent}</b> <span>{STATUS_TEXT[a.status] || a.status}</span>{a.detail && <small>{a.detail}</small>}{a.error && <small className="err">{a.error}</small>}</li>)}</ul>
        {r.blockers.length > 0 ? <><h4>Waiting on a person</h4><ul>{r.blockers.map((b, i) => <li key={i}>{b}</li>)}</ul></> : <p className="muted">Nothing is waiting on a person.</p>}
      </>}
    </section>
  )
}

export default function Overview({ data, patient, setWhy }) {
  const [res, setRes] = useState(null)
  const { busy, error, run } = useAction()
  const summarize = () => run(async () => setRes(await api(`/patients/${patient}/summary`, { method: 'POST' })))
  const show = (id) => { const s = res.sources[id]; setWhy({ title: `Source ${id}`, sources: [{ ref: s.ref, label: s.kind, text: s.text }] }) }
  const sum = res?.summary
  const stmt = (f, i) => <li key={i}>{f.text} {f.sources.map((s) => <Chip key={s} id={s} onClick={() => show(s)} />)}</li>
  return (
    <>
      <section aria-labelledby="flags-h">
        <h2 id="flags-h">Needs attention <span className="muted">· rule-based, not predictions</span></h2>
        {data.flags.length === 0 && <p className="muted">No documentation gaps found by the rules.</p>}
        <div className="flags">
          {data.flags.map((f) => (
            <button key={f.id} className={`flag ${f.level}`} onClick={() => setWhy({ title: f.title, rule: f.rule, next: f.next_step, sources: f.evidence.map((e) => ({ ref: e.ref, text: e.text })) })}>
              <span className="dot" aria-hidden="true" />
              <span><b>{f.title}</b><small>{f.rule}</small></span>
              <span className="why-link">Why?</span>
            </button>))}
        </div>
      </section>

      <Packet patient={patient} />

      <section aria-labelledby="sum-h" className="card">
        <div className="row"><h2 id="sum-h">Hospital stay summary</h2>
          <button className="primary" onClick={summarize} disabled={busy}>{busy ? 'Reading the chart…' : res ? 'Regenerate' : 'Summarize this stay'}</button></div>
        <ErrorNote error={error} />
        {!res && !busy && <p className="muted">Generated on request from this patient’s chart only. Every statement below links to its source.</p>}
        {res && (
          <>
            <div className={`mode ${res.mode}`}>{res.mode === 'llm' ? <>AI summary by <b>{res.model}</b>. Every citation and number was checked against the chart.</> : <>Template summary (no AI used): {res.note}.</>}</div>
            <h3 className="headline">{sum.headline}</h3>
            <p><b>This stay.</b> {sum.course}</p>
            <p><b>Background.</b> {sum.history}</p>
            <h4>Key findings</h4><ul className="stmts">{sum.findings.map(stmt)}</ul>
            {sum.follow_up.length > 0 && <><h4>Open items for the clinician</h4><ul className="stmts">{sum.follow_up.map(stmt)}</ul></>}
          </>)}
      </section>
    </>
  )
}
