import { useState } from 'react'
import { api } from './api.js'
import { ErrorNote, useAction } from './parts.jsx'

const EXAMPLES = ['Is every pending result accounted for?', 'What medications is the patient on and since when?', 'What did the latest progress note say about the plan?', 'Were there any procedures this stay and what was found?']

export default function Ask({ patient, setWhy }) {
  const [q, setQ] = useState('')
  const [log, setLog] = useState([])
  const { busy, error, run } = useAction()
  const send = (text) => run(async () => {
    const r = await api(`/patients/${patient}/ask`, { method: 'POST', body: { question: text } })
    setLog((l) => [{ q: text, r }, ...l]); setQ('')
  })
  return (
    <section>
      <form className="askbar" onSubmit={(e) => { e.preventDefault(); if (q.trim()) send(q.trim()) }}>
        <label htmlFor="q" className="sr">Question about this patient’s chart</label>
        <input id="q" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about this patient’s chart…" maxLength={1000} />
        <button className="primary" disabled={busy || !q.trim()}>{busy ? 'Searching…' : 'Ask'}</button>
      </form>
      <div className="examples">{EXAMPLES.map((x) => <button key={x} className="filter" onClick={() => send(x)}>{x}</button>)}</div>
      <ErrorNote error={error} />
      {log.map(({ q, r }, i) => (
        <article key={i} className="card qa">
          <h3>{q}</h3>
          <div className={`mode ${r.mode}`}>{r.mode === 'llm' ? <>AI answer · checked against the cited passages</> : <>No verified AI answer · {r.note || 'showing the most relevant passages'}</>}</div>
          <p className={r.insufficient_evidence ? 'muted' : ''}>{r.answer}</p>
          <ul className="cites">{r.citations.map((c) => (
            <li key={c.id}><button className="cite" onClick={() => setWhy({ title: 'Cited passage', sources: [{ ref: c.source_ref, label: `${c.id} · ${c.kind}${c.effective ? ' · ' + c.effective.slice(0, 10) : ''}`, text: c.text }] })}>
              <b>{c.id}</b> {c.text.slice(0, 140)}{c.text.length > 140 ? '…' : ''}</button></li>))}</ul>
        </article>))}
    </section>
  )
}
