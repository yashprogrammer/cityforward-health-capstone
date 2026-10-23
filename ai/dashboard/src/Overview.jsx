import { useState } from 'react'
import { api } from './api.js'
import { Chip, ErrorNote, useAction } from './parts.jsx'

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
