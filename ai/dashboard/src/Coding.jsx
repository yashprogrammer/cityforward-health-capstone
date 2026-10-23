import { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorNote, useAction } from './parts.jsx'

export default function Coding({ patient, setWhy, reload }) {
  const [list, setList] = useState([])
  const { busy, error, run } = useAction()
  const refresh = () => run(async () => { setList(await api(`/patients/${patient}/coding`)); reload() })
  useEffect(() => { refresh() }, [])
  const create = () => run(async () => { await api(`/patients/${patient}/coding`, { method: 'POST' }); await refresh() })
  return (
    <section>
      <div className="row"><h2>ICD-10-CM coding worksheet</h2><button className="primary" disabled={busy} onClick={create}>{busy ? 'Working…' : 'Suggest codes for this stay'}</button></div>
      <p className="muted">Suggestions come with the exact chart words they are based on. A coder or the attending accepts, rejects or changes every line and picks the principal diagnosis.</p>
      <ErrorNote error={error} />
      {list.map((w) => <Worksheet key={w.id} w={w} onChange={refresh} setWhy={setWhy} />)}
    </section>
  )
}

function Worksheet({ w, onChange, setWhy }) {
  const { busy, error, run } = useAction()
  const [principal, setPrincipal] = useState('')
  const decide = (item, body) => run(async () => { await api(`/coding/${w.id}/items/${item}`, { method: 'POST', body }); await onChange() })
  const final = w.status === 'FINAL'
  return (
    <article className="card">
      <div className="row"><h3>{w.id}</h3><span className={`status s-${w.status}`}>{w.status}{w.mode === 'chart-only' ? ' · no AI' : ''}</span></div>
      <ErrorNote error={error} />
      {w.queries.length > 0 && <div className="queries"><b>Documentation queries for the clinician</b><ul>{w.queries.map((q, i) => <li key={i}>{q}</li>)}</ul></div>}
      <table>
        <thead><tr><th>Code</th><th>Description</th><th>Basis</th><th>Decision</th></tr></thead>
        <tbody>{w.items.map((i) => (
          <tr key={i.id} className={i.flags.length ? 'flagged' : ''}>
            <td><code>{i.final_code || i.code}</code>{i.decision === 'modified' && <small className="block">was {i.code}</small>}</td>
            <td>{i.description}{i.flags.map((f, k) => <span key={k} className="tag warn">{f}</span>)}</td>
            <td><button className="link" onClick={() => setWhy({ title: `${i.code} ${i.description}`, rule: i.rationale, sources: [...(i.quote ? [{ ref: i.evidence[0]?.ref || '', label: 'Quoted words', text: `“${i.quote}”` }] : []), ...i.evidence.map((e) => ({ ref: e.ref, text: e.text }))] })}>
              {i.origin === 'model' ? 'AI-suggested' : 'In EHR'} · {i.status.replace(/_/g, ' ')}</button></td>
            <td>{final || i.decision ? <b>{i.decision}</b> : (
              <div className="actions">
                <button disabled={busy} onClick={() => decide(i.id, { action: 'accept' })}>Accept</button>
                <button disabled={busy} onClick={() => { const r = prompt('Reason for rejecting'); if (r) decide(i.id, { action: 'reject', reason: r }) }}>Reject</button>
                <button disabled={busy} onClick={() => { const c = prompt('Correct ICD-10-CM code'); const r = c && prompt('Reason for the change'); if (c && r) decide(i.id, { action: 'modify', code: c, reason: r }) }}>Change</button>
              </div>)}</td>
          </tr>))}</tbody>
      </table>
      {!final && w.open_items.length === 0 && (
        <div className="actions end"><label>Principal diagnosis <select value={principal} onChange={(e) => setPrincipal(e.target.value)}><option value="">choose…</option>
          {w.items.filter((i) => ['accepted', 'modified'].includes(i.decision)).map((i) => <option key={i.id} value={i.id}>{i.final_code || i.code}</option>)}</select></label>
          <button className="primary" disabled={busy || !principal} onClick={() => run(async () => { await api(`/coding/${w.id}/finalize`, { method: 'POST', body: { principal_item: principal } }); await onChange() })}>Finalize codes</button></div>)}
    </article>
  )
}
