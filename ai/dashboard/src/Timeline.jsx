import { useState } from 'react'

const KINDS = { encounter: ['Visits', '🏥'], procedure: ['Procedures', '🩺'], lab: ['Results', '🧪'], medication: ['Medications', '💊'], note: ['Notes', '📝'], document: ['Documents', '📄'] }

export default function Timeline({ data, setWhy }) {
  const [on, setOn] = useState(new Set(['encounter', 'procedure', 'lab', 'note']))
  const toggle = (k) => setOn((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })
  const items = data.timeline.filter((e) => on.has(e.kind))
  const byDay = items.reduce((m, e) => { const d = e.when.slice(0, 10); (m[d] ||= []).push(e); return m }, {})
  const days = Object.keys(byDay).sort().reverse()
  return (
    <section>
      <div className="filters" role="group" aria-label="Show">
        {Object.entries(KINDS).map(([k, [label]]) => <button key={k} className={`filter ${on.has(k) ? 'on' : ''}`} aria-pressed={on.has(k)} onClick={() => toggle(k)}>{label}</button>)}
      </div>
      {days.length === 0 && <p className="muted">Nothing to show with these filters.</p>}
      <ol className="timeline">
        {days.map((d) => (
          <li key={d}>
            <h3>{new Date(d + 'T12:00:00').toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}</h3>
            <ul>
              {byDay[d].map((e, i) => (
                <li key={i}>
                  <button className={`ev ${e.kind} ${e.flag === 'pending' ? 'pending' : e.flag ? 'abn' : ''}`}
                    onClick={() => setWhy({ title: e.title, sources: [{ ref: e.ref, label: `${KINDS[e.kind][0]} · ${e.when.replace('T', ' ').slice(0, 16)}`, text: [e.title, e.detail].filter(Boolean).join(' — ') }], footer: 'Shown as recorded in the EHR.' })}>
                    <span className="ico" aria-hidden="true">{KINDS[e.kind][1]}</span>
                    <span className="t"><b>{e.title}</b>{e.detail && <small>{e.detail}</small>}</span>
                    {e.flag === 'pending' && <span className="tag warn">pending</span>}
                    {(e.flag === 'H' || e.flag === 'L') && <span className="tag bad">{e.flag === 'H' ? 'high' : 'low'}</span>}
                  </button>
                </li>))}
            </ul>
          </li>))}
      </ol>
    </section>
  )
}
