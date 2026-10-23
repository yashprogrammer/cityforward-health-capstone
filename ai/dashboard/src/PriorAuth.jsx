import { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorNote, useAction } from './parts.jsx'

const LABEL = { met: 'Documented', candidate: 'Found: please confirm', missing: 'Not documented', fails_policy: 'Does not meet policy', stale: 'Too old', needs_attestation: 'Needs clinician attestation',
                accepted: 'Confirmed', attested: 'Attested', clinician_provided: 'Provided by clinician' }
const GOOD = new Set(['met', 'accepted', 'attested', 'clinician_provided'])

export default function PriorAuth({ patient, setWhy, reload }) {
  const [sug, setSug] = useState(null)
  const [list, setList] = useState([])
  const { busy, error, run } = useAction()
  const refresh = () => run(async () => { setList(await api(`/patients/${patient}/prior-auth`)); setSug(await api(`/patients/${patient}/prior-auth/suggestions`)); reload() })
  useEffect(() => { refresh() }, [])
  const draft = (code) => run(async () => { await api(`/patients/${patient}/prior-auth`, { method: 'POST', body: { service_code: code } }); await refresh() })
  const open = new Set(list.filter((p) => !['CERTIFIED', 'DENIED', 'NOT_REQUIRED'].includes(p.status)).map((p) => p.service_code))
  return (
    <section>
      <ErrorNote error={error} />
      <h2>Suggested from the chart</h2>
      {sug === null ? <p className="muted">Checking the plan’s requirements…</p> : sug.length === 0 ? <p className="muted">Nothing in the notes suggests a service that needs prior authorization.</p> :
        <ul className="plain">{sug.map((s) => (
          <li key={s.service_code} className="card row">
            <div><b>{s.service_name}</b> <span className="muted">({s.service_code})</span><small className="block">{s.pa_required ? `${s.plan} requires prior authorization.` : `${s.plan || 'This plan'} does not require it.`} Mentioned in: {s.reason}</small></div>
            {s.pa_required && !open.has(s.service_code) && <button className="primary" disabled={busy} onClick={() => draft(s.service_code)}>{busy ? 'Drafting…' : 'Prepare draft'}</button>}
            {open.has(s.service_code) && <span className="tag">in progress</span>}
          </li>))}</ul>}
      <h2>Requests</h2>
      {list.length === 0 && <p className="muted">None yet. Drafts are also prepared automatically when discharge is advised.</p>}
      {list.map((p) => <Request key={p.id} pa={p} onChange={refresh} setWhy={setWhy} />)}
    </section>
  )
}

function Request({ pa, onChange, setWhy }) {
  const { busy, error, run } = useAction()
  const act = (path, body) => run(async () => { await api(path, { method: 'POST', body }); await onChange() })
  const review = (rid, body) => act(`/prior-auth/${pa.id}/requirements/${rid}`, body)
  return (
    <article className="card pa">
      <div className="row"><h3>{pa.service_name} <span className="muted">· {pa.id}</span></h3><span className={`status s-${pa.status}`}>{pa.status.replace('_', ' ')}</span></div>
      <ErrorNote error={error} />
      <ul className="reqs">
        {pa.requirements.map((r) => (
          <li key={r.id} className={GOOD.has(r.status) ? 'ok' : 'gap'}>
            <div className="req-head"><b>{LABEL[r.status] || r.status}</b><span>{r.text}</span></div>
            {r.statement && <p className="statement">{r.statement}</p>}
            {r.note && <p className="muted small">{r.note}</p>}
            {r.evidence.length > 0 && <button className="link" onClick={() => setWhy({ title: r.text, sources: r.evidence.map((e) => ({ ref: e.ref, label: e.date || e.ref, text: e.text })) })}>Show evidence</button>}
            {pa.status === 'DRAFT' || pa.status === 'PENDED' ? (
              <div className="actions">
                {r.status === 'candidate' && <button disabled={busy} onClick={() => review(r.id, { action: 'accept', statement: r.statement || prompt('What does the evidence show? (this goes to the payer)') || '' })}>Confirm</button>}
                {r.status === 'needs_attestation' && <button disabled={busy} onClick={() => review(r.id, { action: 'attest' })}>I attest</button>}
                {!GOOD.has(r.status) && r.kind !== 'attestation' && r.kind !== 'diagnosis' && <button disabled={busy} onClick={() => {
                  const st = prompt('Statement for the payer (at least 20 characters)'); if (!st) return
                  const v = r.kind === 'value' ? parseFloat(prompt('Measured value')) : undefined
                  review(r.id, { action: 'provide', statement: st, value: v, measured_on: r.kind === 'value' ? prompt('Date measured (YYYY-MM-DD)') : undefined })
                }}>Provide…</button>}
              </div>) : null}
          </li>))}
      </ul>
      {pa.payer_notes?.length > 0 && <div className="payer"><b>Payer response ({pa.payer_action}):</b> {pa.payer_notes.join(' · ')} {pa.preauth_ref && <b>Reference {pa.preauth_ref}</b>}</div>}
      <div className="actions end">
        {(pa.status === 'DRAFT') && <button className="primary" disabled={busy} onClick={() => { const gaps = pa.gaps.length; act(`/prior-auth/${pa.id}/approve`, gaps ? { submit_with_gaps_reason: prompt(`${gaps} requirement(s) open. Reason to submit anyway?`) || '' } : {}) }}>Approve for submission</button>}
        {pa.status === 'APPROVED' && <button className="primary" disabled={busy} onClick={() => act(`/prior-auth/${pa.id}/submit`)}>Submit to payer</button>}
        {pa.status === 'QUEUED' && <button disabled={busy} onClick={() => act(`/prior-auth/${pa.id}/refresh`)}>Check payer decision</button>}
      </div>
      <details><summary>History</summary><ul className="hist">{pa.history.map((h, i) => <li key={i}><time>{h.at.slice(5, 16).replace('T', ' ')}</time> {h.by}: {h.what}</li>)}</ul></details>
    </article>
  )
}
