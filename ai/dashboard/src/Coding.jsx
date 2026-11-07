import { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorNote, InlineForm, useAction } from './parts.jsx'

const norm = (code) => (code || '').replace(/\./g, '').toUpperCase()

export default function Coding({ patient, setWhy, reload, session }) {
  const [list, setList] = useState([])
  const [selected, setSelected] = useState('')
  const { busy, error, run } = useAction()
  const canSuggest = ['ATTENDING', 'RESIDENT', 'HIM_CODER'].includes(session.role)
  const canDecide = ['ATTENDING', 'HIM_CODER'].includes(session.role)
  const refresh = async (selectId) => {
    const all = (await api(`/patients/${patient}/coding`)).filter((w) => !session.encounter || w.encounter_id === session.encounter)
    setList(all)
    setSelected((old) => selectId || (all.some((w) => w.id === old) ? old : all[0]?.id || ''))
    reload()
  }
  useEffect(() => { run(() => refresh()) }, [patient])
  const create = () => run(async () => {
    const w = await api(`/patients/${patient}/coding`, { method: 'POST' })
    await refresh(w.id)
  })
  const selectedIndex = list.findIndex((w) => w.id === selected)
  const current = list[selectedIndex]
  const previous = list[selectedIndex + 1]
  const previousCodes = new Set((previous?.items || []).map((i) => norm(i.code)))
  const currentCodes = new Set((current?.items || []).map((i) => norm(i.code)))
  const absent = previous?.items.filter((i) => !currentCodes.has(norm(i.code))) || []
  return (
    <section>
      <div className="row"><h2>ICD-10-CM coding worksheet</h2>{canSuggest && <button className="primary" disabled={busy} onClick={create}>{busy ? 'Working…' : list.length ? 'Generate new version' : 'Suggest codes for this stay'}</button>}</div>
      <p className="muted">Review suggestions against the chart. A coder or attending decides each line and selects the principal diagnosis.</p>
      {!canDecide && <p className="muted">Read-only: only an attending or HIM coder can decide and finalize codes.</p>}
      <ErrorNote error={error} />
      {list.length > 0 && <div className="card"><label>Worksheet version <select value={selected} onChange={(e) => setSelected(e.target.value)}>{list.map((w, i) => <option value={w.id} key={w.id}>Version {list.length - i}{i === 0 ? ' (latest)' : ''} · {w.status} · {w.id}</option>)}</select></label><p className="muted small">Selected: version {list.length - selectedIndex}{selectedIndex === 0 ? ' (latest)' : ' (earlier)'}. Each version retains its own decisions. New suggestions require review.</p></div>}
      {current && <Worksheet key={current.id} w={current} previousCodes={previousCodes} hasPrevious={!!previous} absent={absent} canDecide={canDecide} onChange={() => refresh(current.id)} setWhy={setWhy} />}
    </section>
  )
}

function Worksheet({ w, previousCodes, hasPrevious, absent, canDecide, onChange, setWhy }) {
  const { busy, error, run } = useAction()
  const [principal, setPrincipal] = useState('')
  const [asking, setAsking] = useState(null)   // { item, mode: 'reject' | 'modify' }
  const decide = (item, body) => run(async () => { await api(`/coding/${w.id}/items/${item}`, { method: 'POST', body }); await onChange(); return true })
  const final = w.status === 'FINAL'
  const remaining = w.open_items.length
  return (
    <article className="card">
      <div className="row"><h3>{w.id}</h3><span className={`status s-${w.status}`}>{w.status}{w.mode === 'chart-only' ? ' · no AI' : ''}</span></div>
      <ErrorNote error={error} />
      {w.queries.length > 0 && <div className="queries"><b>Documentation queries for the clinician</b><ul>{w.queries.map((q, i) => <li key={i}>{q}</li>)}</ul></div>}
      <table>
        <thead><tr><th>Code</th><th>Description</th><th>Basis</th><th>Decision</th></tr></thead>
        <tbody>{w.items.map((i) => (
          <tr key={i.id} className={i.flags.length ? 'flagged' : ''}>
            <td><code>{i.final_code || i.code}</code>{i.decision === 'modified' && <small className="block">was {i.code}</small>}{hasPrevious && <small className="block">{previousCodes.has(norm(i.code)) ? 'Previously suggested' : 'New suggestion'}</small>}</td>
            <td>{i.description}{i.flags.map((f, k) => <span key={k} className="tag warn">{f}</span>)}</td>
            <td><button className="link" onClick={() => setWhy({ title: `${i.code} ${i.description}`, rule: i.rationale, sources: [...(i.quote ? [{ ref: i.evidence[0]?.ref || '', label: 'Quoted words', text: `“${i.quote}”` }] : []), ...i.evidence.map((e) => ({ ref: e.ref, text: e.text }))] })}>{i.origin === 'model' ? 'AI-suggested' : 'In EHR'} · {i.status.replace(/_/g, ' ')}</button></td>
            <td>{final || i.decision || !canDecide ? <><b>{i.decision || 'Not reviewed'}</b>{i.reason && <small className="block">Reason: {i.reason}</small>}{i.decided_by && <small className="block">Reviewed by: {i.decided_by}</small>}</> : (
              asking?.item === i.id ? (
                <InlineForm busy={busy} error={error} submit={asking.mode === 'reject' ? 'Reject code' : 'Change code'} onCancel={() => setAsking(null)}
                  fields={asking.mode === 'reject' ? [{ name: 'reason', label: 'Reason for rejecting', min: 5 }] : [{ name: 'code', label: 'Correct ICD-10-CM code', placeholder: 'e.g. J02.0' }, { name: 'reason', label: 'Reason for the change', min: 5 }]}
                  onSubmit={async (v) => { if (await decide(i.id, asking.mode === 'reject' ? { action: 'reject', reason: v.reason } : { action: 'modify', code: v.code, reason: v.reason })) setAsking(null) }} />
              ) : (
              <div className="actions">
                <button disabled={busy} onClick={() => decide(i.id, { action: 'accept' })}>Accept</button>
                <button disabled={busy} onClick={() => setAsking({ item: i.id, mode: 'reject' })}>Reject</button>
                <button disabled={busy} onClick={() => setAsking({ item: i.id, mode: 'modify' })}>Change</button>
              </div>))}</td>
          </tr>))}</tbody>
      </table>
      {absent.length > 0 && <div className="queries"><b>Not suggested in this version — review the earlier version</b><ul>{absent.map((i) => <li key={i.id}>{i.code} · {i.description}{i.decision ? ` · earlier decision: ${i.decision}` : ''}</li>)}</ul><p>Not suggested does not mean the diagnosis is irrelevant.</p></div>}
      {!final && <div className="actions end"><label>Principal diagnosis <select value={principal} disabled={!canDecide || remaining > 0} onChange={(e) => setPrincipal(e.target.value)}><option value="">choose…</option>
        {w.items.filter((i) => ['accepted', 'modified'].includes(i.decision)).map((i) => <option key={i.id} value={i.id}>{i.final_code || i.code}</option>)}</select></label>
        <button className="primary" disabled={busy || !canDecide || remaining > 0 || !principal} onClick={() => run(async () => { await api(`/coding/${w.id}/finalize`, { method: 'POST', body: { principal_item: principal } }); await onChange() })}>Finalize codes</button>
        <p className="muted small">{!canDecide ? 'Only an attending or HIM coder can finalize.' : remaining ? `Review ${remaining} remaining code(s) before finalizing.` : !principal ? 'Choose a principal diagnosis to finalize.' : 'Ready to finalize.'}</p></div>}
    </article>
  )
}
