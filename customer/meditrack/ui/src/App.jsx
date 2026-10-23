import { useCallback, useEffect, useRef, useState } from 'react'

const api = async (path, opts) => {
  const r = await fetch(path, opts)
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `ERROR ${r.status}`)
  return r.json()
}
const post = (path, body) =>
  api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })

// MediTrack stores dates as 'YYYY-MM-DD HH:MM' text; US screens show MM/DD/YYYY
const fd = (s) => (s ? s.replace(/^(\d{4})-(\d{2})-(\d{2})/, '$2/$3/$1') : '')
const pay = (m) => (m ? m.replace('_', ' ') : '')

function useHash() {
  const [hash, setHash] = useState(window.location.hash || '#/census')
  useEffect(() => {
    const on = () => setHash(window.location.hash || '#/census')
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return hash
}

function useLoad(fn, deps) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const reload = useCallback(() => {
    fn().then(setData).catch((e) => setErr(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(reload, [reload])
  return [data, err, reload]
}

const Sts = ({ s }) => <span className={`sts ${s}`}>{{ ADM: 'ADMITTED', DA: 'DISCH. ADVISED', DSCH: 'DISCHARGED', CLS: 'CLOSED' }[s] || s}</span>

// ------------------------------------------------------------------ Census
function Census() {
  const [data, err] = useLoad(() => api('/int/census'), [])
  return (
    <fieldset>
      <legend>IPD Census - Current In-Patients</legend>
      {err && <div className="err">{err}</div>}
      <table className="grid compact">
        <thead>
          <tr><th>Account</th><th>MRN</th><th>Patient Name</th><th>Age/Sex</th><th>Unit</th><th>Bed</th><th>Attending</th><th>Coverage</th><th>Payer</th><th>Admitted</th><th>Status</th></tr>
        </thead>
        <tbody>
          {data?.map((r) => (
            <tr key={r.IP_NO}>
              <td><a href={`#/ip/${r.IP_NO}`}>{r.IP_NO}</a></td>
              <td>{r.MRN}</td><td className="wrap">{r.PT_NM.toUpperCase()}</td><td>{r.PT_AGE}/{r.PT_SEX}</td>
              <td>{r.WRD_CD}</td><td>{r.BED_NO}</td><td className="wrap">{r.DR_NM}</td><td>{pay(r.PAY_MODE)}</td><td className="wrap">{r.PYR_NM || '-'}</td>
              <td>{fd(r.ADM_DT)}</td><td><Sts s={r.STS} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="hint">Total in-patients: {data?.length ?? '...'}. Click the account number to open the patient file.</p>
    </fieldset>
  )
}

// ------------------------------------------------------------------ Discharged
function Discharged() {
  const [data] = useLoad(() => api('/int/discharged'), [])
  return (
    <fieldset>
      <legend>Discharged Patients (last 60)</legend>
      <table className="grid compact">
        <thead><tr><th>Account</th><th>MRN</th><th>Patient</th><th>Unit</th><th>Attending</th><th>Coverage</th><th>Disch. Advised</th><th>Discharged</th><th>Disposition</th><th>DS on file</th></tr></thead>
        <tbody>
          {data?.map((r) => (
            <tr key={r.IP_NO}>
              <td><a href={`#/ip/${r.IP_NO}`}>{r.IP_NO}</a></td><td>{r.MRN}</td><td className="wrap">{r.PT_NM.toUpperCase()}</td><td>{r.WRD_CD}</td>
              <td className="wrap">{r.DR_NM}</td><td>{pay(r.PAY_MODE)}</td><td>{fd(r.DSCH_ADV_DT)}</td><td>{fd(r.DSCH_DT)}</td><td>{pay(r.DSCH_DISP)}</td><td>{r.HAS_SUMM ? 'Y' : 'N'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </fieldset>
  )
}

// ------------------------------------------------------------------ Patient file
const TABS = ['Diagnosis', 'Procedures', 'Lab Results', 'Pharmacy', 'Clinical Notes', 'Documents', 'Discharge Summary', 'Payer Auth', 'Visit History']

function Patient({ ip }) {
  const [d, err, reload] = useLoad(() => api(`/int/ip/${ip}`), [ip])
  const [tab, setTab] = useState('Diagnosis')
  const [msg, setMsg] = useState('')

  const act = async (path, confirmMsg, body) => {
    if (!window.confirm(confirmMsg)) return
    try { await post(path, body); setMsg('Saved successfully.'); reload() } catch (e) { setMsg(e.message) }
  }

  if (err) return <div className="err">{err}</div>
  if (!d) return <div>Loading patient file...</div>
  const a = d.adm
  const linkItems = d.links.filter((l) => l.DISP_MD === 'LINK')
  const customTabs = d.links.filter((l) => l.DISP_MD !== 'LINK')   // TAB and SMART apps render inside the patient file
  const openTab = (t) => { setTab(t); reload() }   // every tab click is a fresh server round-trip, like 2011
  const custom = customTabs.find((l) => l.LNK_LBL === tab)
  const discharge = () => {
    const disp = window.prompt('Discharge disposition (HOME, HOME_HEALTH, SNF, REHAB)', 'HOME')
    if (disp) act(`/int/ip/${ip}/discharge`, 'Confirm FINAL DISCHARGE (bill settled)?', { disposition: disp.toUpperCase() })
  }
  const inpatient = a.ENC_CLS === 'IMP'
  return (
    <>
      <fieldset>
        <legend>Patient File - {a.IP_NO} ({a.ENC_TYP || 'visit'})</legend>
        <div className="pt-strip">
          <span><b>Name:</b> {a.PT_NM.toUpperCase()}</span><span><b>MRN:</b> {a.MRN}</span>
          <span><b>DOB:</b> {fd(a.PT_DOB)}</span><span><b>Age/Sex:</b> {a.PT_AGE}/{a.PT_SEX}</span>
          <span><b>Phone:</b> {a.PT_PHN}</span><span><b>Address:</b> {a.PT_ADDR}, {a.PT_CITY} {a.PT_ST} {a.PT_ZIP}</span>
          <span><b>Admitted:</b> {fd(a.ADM_DT)}</span><span><b>Unit/Bed:</b> {a.WRD_CD} / {a.BED_NO || '-'}</span>
          <span><b>Attending:</b> {a.DR_NM}</span><span><b>Status:</b> <Sts s={a.STS} /></span>
          <span><b>Coverage:</b> {pay(a.PAY_MODE)}</span><span><b>Payer:</b> {a.PYR_NM || '-'}</span>
          <span><b>Member ID:</b> {a.MBR_ID || '-'}</span><span><b>Disch. Advised:</b> {fd(a.DSCH_ADV_DT) || '-'}</span>
        </div>
        <div><b>Reason for visit:</b> {a.ADM_RSN}</div>
        {inpatient && (
          <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="primary" disabled={a.STS !== 'ADM'} onClick={() => act(`/int/ip/${ip}/discharge-advise`, 'Mark patient as DISCHARGE ADVISED?')}>Discharge Advised</button>
            <button disabled={a.STS !== 'DA'} onClick={discharge}>Final Discharge</button>
            <a className="btn" href={`/int/ip/${ip}/summary-template`}>Download DS Template (.doc)</a>
            <button onClick={() => window.print()}>Print</button>
            {msg && <span className="err">{msg}</span>}
          </div>
        )}
        {linkItems.length > 0 && (
          <div className="links">
            <b>External Links:</b>{' '}
            {linkItems.map((l) => <a key={l.LNK_ID} href={l.HREF} target="_blank" rel="noreferrer" style={{ marginRight: 14 }}>{l.LNK_LBL} &#8599;</a>)}
          </div>
        )}
      </fieldset>

      <div className="tabs">
        {TABS.map((t) => <button key={t} className={t === tab ? 'cur' : ''} onClick={() => openTab(t)}>{t}</button>)}
        {customTabs.map((l) => <button key={l.LNK_ID} className={l.LNK_LBL === tab ? 'cur' : ''} onClick={() => openTab(l.LNK_LBL)}>{l.LNK_LBL}</button>)}
      </div>
      <div className="tabbody">
        {tab === 'Diagnosis' && <Grid rows={d.diags} cols={[['DIAG_TYP', 'Type'], ['ICD_CD', 'ICD-10-CM'], ['DIAG_DESC', 'Description']]} />}
        {tab === 'Procedures' && <Grid rows={d.procs.map((p) => ({ ...p, PROC_DT: fd(p.PROC_DT) }))} cols={[['PROC_DT', 'Date'], ['PROC_CD', 'Code'], ['PROC_DESC', 'Procedure'], ['SURGEON', 'Performed by']]} />}
        {tab === 'Lab Results' && <Labs labs={d.labs} />}
        {tab === 'Pharmacy' && <Grid rows={d.meds} cols={[['ISS_TYP', 'List'], ['DRUG_NM', 'Drug'], ['DOSE', 'Dose'], ['FREQ', 'Frequency'], ['ROUTE', 'Route'], ['MED_STS', 'Status']]} />}
        {tab === 'Clinical Notes' && <Grid rows={d.notes.map((n) => ({ ...n, NOTE_DT: fd(n.NOTE_DT) }))} cols={[['NOTE_DT', 'Date/Time'], ['NOTE_TYP', 'Type'], ['AUTH', 'Author'], ['NOTE_TXT', 'Note']]} />}
        {tab === 'Documents' && <Documents ip={ip} docs={d.docs} reload={reload} />}
        {tab === 'Discharge Summary' && <SummaryTab ip={ip} summary={d.summary} reload={reload} />}
        {custom && <EmbeddedTab key={custom.LNK_ID} title={custom.LNK_LBL} src={custom.HREF} />}
        {tab === 'Payer Auth' && <Grid rows={d.auths.map((x) => ({ ...x, SUBM_DT: fd(x.SUBM_DT), RESP_DT: fd(x.RESP_DT) }))} cols={[['SUBM_DT', 'Submitted'], ['AUTH_STS', 'Status'], ['QRY_RSN', 'Payer query'], ['RESP_DT', 'Responded']]} />}
        {tab === 'Visit History' && <VisitHistory visits={d.visits} />}
      </div>
    </>
  )
}

function VisitHistory({ visits }) {
  if (!visits?.length) return <div className="hint">No other visits on file.</div>
  return (
    <table className="grid">
      <thead><tr><th>Account</th><th>Date</th><th>Class</th><th>Visit type</th><th>Reason</th></tr></thead>
      <tbody>
        {visits.map((v) => (
          <tr key={v.IP_NO}>
            <td>{v.ENC_CLS === 'IMP' ? <a href={`#/ip/${v.IP_NO}`}>{v.IP_NO}</a> : <a href={`#/ip/${v.IP_NO}`}>{v.IP_NO}</a>}</td>
            <td>{fd(v.ADM_DT)}</td><td>{v.ENC_CLS}</td><td>{v.ENC_TYP}</td><td>{v.ADM_RSN}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Configured "Patient file tab" / SMART app links render the page inside the tab body.
// The embedded page may report its height (postMessage 'meditrack:tab-height') so the tab grows to fit.
function EmbeddedTab({ title, src }) {
  const ref = useRef(null)
  const [height, setHeight] = useState(600)
  useEffect(() => {
    const on = (e) => {
      if (e.source === ref.current?.contentWindow && e.data?.type === 'meditrack:tab-height') setHeight(Math.max(300, e.data.height + 4))
    }
    window.addEventListener('message', on)
    return () => window.removeEventListener('message', on)
  }, [])
  return <iframe ref={ref} title={title} src={src} style={{ width: '100%', height, border: 0, display: 'block' }} />
}

function Grid({ rows, cols }) {
  if (!rows?.length) return <div className="hint">No records found.</div>
  return (
    <table className="grid">
      <thead><tr>{cols.map(([, h]) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{cols.map(([k]) => <td key={k}>{r[k] ?? ''}</td>)}</tr>)}</tbody>
    </table>
  )
}

function Labs({ labs }) {
  if (!labs.length) return <div className="hint">No records found.</div>
  return (
    <table className="grid">
      <thead><tr><th>Date</th><th>Test</th><th>LOINC</th><th>Result</th><th>Unit</th><th>Ref. Range</th><th>Status</th></tr></thead>
      <tbody>
        {labs.map((l) => (
          <tr key={l.ID}>
            <td>{fd(l.RSLT_DT)}</td><td>{l.TST_NM}</td><td>{l.LOINC_CD}</td>
            <td className={l.FLG ? `flag-${l.FLG}` : ''}>{l.RSLT_VAL} {l.FLG && `(${l.FLG})`}</td>
            <td>{l.UOM}</td><td>{l.REF_RNG}</td>
            <td className={l.STS === 'PENDING' ? 'pending' : ''}>{l.STS}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const DOC_TYPES = ['DEVICE_LOG', 'CATH_RPT', 'OP_NOTE', 'CONSENT', 'LAB_RPT', 'ID_PROOF', 'INS_CARD', 'OTHER']

function Documents({ ip, docs, reload }) {
  const [typ, setTyp] = useState(DOC_TYPES[0])
  const [file, setFile] = useState(null)
  const [msg, setMsg] = useState('')
  const upload = async () => {
    if (!file) return setMsg('SELECT FILE FIRST')
    const fd_ = new FormData()
    fd_.append('doc_typ', typ)
    fd_.append('file', file)
    try { await api(`/int/ip/${ip}/docs`, { method: 'POST', body: fd_ }); setMsg('Uploaded.'); reload() } catch (e) { setMsg(e.message) }
  }
  return (
    <>
      <table className="grid">
        <thead><tr><th>Doc ID</th><th>Type</th><th>File</th><th>Uploaded</th><th>By</th></tr></thead>
        <tbody>
          {docs.map((d) => (
            <tr key={d.DOC_ID} className={d.UPL_BY === 'BATCH_IMPORT' ? 'hl' : ''}>
              <td>{d.DOC_ID}</td><td>{d.DOC_TYP}</td>
              <td><a href={`/int/docs/${d.DOC_ID}`} target="_blank" rel="noreferrer">{d.FILE_NM}</a></td>
              <td>{fd(d.UPL_DT)}</td><td>{d.UPL_BY}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <fieldset style={{ marginTop: 8 }}>
        <legend>Upload Document</legend>
        <div className="formrow">
          Type: <select value={typ} onChange={(e) => setTyp(e.target.value)}>{DOC_TYPES.map((t) => <option key={t}>{t}</option>)}</select>
          <input type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files[0])} />
          <button className="primary" onClick={upload}>Upload</button> <span className="err">{msg}</span>
        </div>
      </fieldset>
    </>
  )
}

function SummaryTab({ ip, summary, reload }) {
  const [text, setText] = useState('')
  const save = async () => { await post(`/int/ip/${ip}/manual-summary`, { text }); reload() }
  if (summary) {
    return (
      <>
        <div className="hint">Typed by {summary.CRT_BY} on {fd(summary.CRT_DT)}</div>
        <pre className="summ">{summary.SUMM_TXT}</pre>
      </>
    )
  }
  return (
    <>
      <div className="hint">
        No discharge summary on file. Download the DS template (Form HIM-DS-07), then copy details from the
        Diagnosis, Procedures, Lab Results, Pharmacy and Clinical Notes screens.
      </div>
      <textarea rows={16} value={text} onChange={(e) => setText(e.target.value)}
        placeholder={'Diagnosis:\nPresenting complaints:\nHospital course:\nProcedures:\nInvestigations:\nCondition at discharge:\nDischarge medications:\nFollow-up:'} />
      <div style={{ marginTop: 8 }}><button className="primary" onClick={save} disabled={!text.trim()}>Save Summary</button></div>
    </>
  )
}

// ------------------------------------------------------------------ Admin: external links
function ExtLinks() {
  const [data, , reload] = useLoad(() => api('/int/admin/extlinks'), [])
  const [label, setLabel] = useState('')
  const [url, setUrl] = useState('')
  const [mode, setMode] = useState('LINK')
  const add = async () => { await post('/int/admin/extlinks', { label, url, mode }); setLabel(''); setUrl(''); setMode('LINK'); reload() }
  const toggle = async (id) => { await post(`/int/admin/extlinks/${id}/toggle`); reload() }
  const modeName = { LINK: 'New window', TAB: 'Patient file tab', SMART: 'SMART on FHIR app (EHR launch)' }
  return (
    <fieldset>
      <legend>System Admin - External Links (Patient File)</legend>
      <p className="hint">Links configured here appear on every patient file, as a link, an embedded tab, or a SMART on FHIR app that MediTrack launches with patient context. Placeholders: {'{IP_NO}'}, {'{MRN}'}. SMART apps: enter the app's launch URL.</p>
      <table className="grid">
        <thead><tr><th>ID</th><th>Label</th><th>URL</th><th>Display</th><th>Active</th><th>Created By</th><th>Created</th><th></th></tr></thead>
        <tbody>
          {data?.map((l) => (
            <tr key={l.LNK_ID}>
              <td>{l.LNK_ID}</td><td>{l.LNK_LBL}</td><td>{l.LNK_URL}</td><td>{modeName[l.DISP_MD] || l.DISP_MD}</td><td>{l.IS_ACTV ? 'Y' : 'N'}</td>
              <td>{l.CRT_BY}</td><td>{fd(l.CRT_DT)}</td><td><button onClick={() => toggle(l.LNK_ID)}>{l.IS_ACTV ? 'Disable' : 'Enable'}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <fieldset style={{ marginTop: 8 }}>
        <legend>Add New Link</legend>
        <div className="formrow">
          Label: <input type="text" size={22} value={label} onChange={(e) => setLabel(e.target.value)} />
          URL: <input type="text" size={48} value={url} onChange={(e) => setUrl(e.target.value)} />
          Display: <select value={mode} onChange={(e) => setMode(e.target.value)}>{Object.entries(modeName).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <button className="primary" disabled={!label || !url} onClick={add}>Add</button>
        </div>
      </fieldset>
    </fieldset>
  )
}

// ------------------------------------------------------------------ MIS
function Mis() {
  const [d] = useLoad(() => api('/int/mis/discharge-tat'), [])
  if (!d) return <div>Generating report...</div>
  return (
    <fieldset>
      <legend>MIS Report - Discharge Turnaround (Discharge Advised to Patient Out)</legend>
      <div style={{ margin: '2px 0 4px' }}>
        {d.by_mode.map((m) => <span className="kpi" key={m.PAY_MODE}><b>{m.AVG_TAT_HRS} hrs</b>{pay(m.PAY_MODE)} ({m.N} pts)</span>)}
        <span className="kpi"><b>{d.auths.QUERY_PCT}%</b>payer authorizations with a query ({d.auths.QUERIES}/{d.auths.N})</span>
      </div>
      <Grid rows={d.reasons} cols={[['QRY_RSN', 'Payer query reason'], ['N', 'Count']]} />
    </fieldset>
  )
}

function Audit() {
  const [data] = useLoad(() => api('/int/audit'), [])
  return <fieldset><legend>Audit Trail (last 50)</legend><Grid rows={data?.map((r) => ({ ...r, TS: fd(r.TS) }))} cols={[['TS', 'Time'], ['USR', 'User'], ['ACTN', 'Action'], ['REF', 'Ref']]} /></fieldset>
}

// ------------------------------------------------------------------ Webhooks admin
function Webhooks() {
  const [subs, , reload] = useLoad(() => api('/int/admin/webhooks'), [])
  const [out, , reloadOut] = useLoad(() => api('/int/admin/webhooks/outbox'), [])
  const [f, setF] = useState({ name: '', url: '', secret: '', events: '*' })
  const add = async () => { await post('/int/admin/webhooks', f); setF({ name: '', url: '', secret: '', events: '*' }); reload() }
  const replay = async (id) => { await post(`/int/admin/webhooks/outbox/${id}/replay`); reloadOut() }
  return (
    <>
      <fieldset>
        <legend>System Admin - Event Webhooks</legend>
        <p className="hint">MediTrack posts a signed CloudEvent (IDs only, no patient data) to every active subscription. Events: encounter.discharge-advised, encounter.discharged, document.added.</p>
        <Grid rows={subs?.map((s) => ({ ...s, IS_ACTV: s.IS_ACTV ? 'Y' : 'N' }))} cols={[['WH_NM', 'Name'], ['WH_URL', 'URL'], ['EVENTS', 'Events'], ['IS_ACTV', 'Active'], ['SENT', 'Sent'], ['PENDING', 'Pending'], ['FAILED', 'Failed']]} />
        <fieldset style={{ marginTop: 8 }}>
          <legend>Add Subscription</legend>
          <div className="formrow">
            Name: <input type="text" size={16} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
            URL: <input type="text" size={38} value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />
            Secret: <input type="text" size={16} value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })} />
            Events: <input type="text" size={20} value={f.events} onChange={(e) => setF({ ...f, events: e.target.value })} />
            <button className="primary" disabled={!f.name || !f.url || !f.secret} onClick={add}>Add</button>
          </div>
        </fieldset>
      </fieldset>
      <fieldset>
        <legend>Delivery Log (last 100)</legend>
        <table className="grid compact">
          <thead><tr><th>ID</th><th>Event</th><th>Status</th><th>Attempts</th><th>Last error</th><th>Created</th><th></th></tr></thead>
          <tbody>
            {out?.map((o) => (
              <tr key={o.ID}><td>{o.ID}</td><td>{o.EVT_TYP.replace('org.cityforward.meditrack.', '')}</td><td className={o.STS === 'FAILED' ? 'err' : ''}>{o.STS}</td>
                <td>{o.ATTEMPTS}</td><td className="wrap">{o.LAST_ERR}</td><td>{fd(o.CRT_DT)}</td>
                <td>{(o.STS === 'FAILED' || o.STS === 'SENT') && <button onClick={() => replay(o.ID)}>Replay</button>}</td></tr>
            ))}
          </tbody>
        </table>
      </fieldset>
    </>
  )
}

// Demo only: MediTrack has no SSO, so the banner lets you choose who you are signed in as (stored in a cookie).
function ActAs() {
  const [users] = useLoad(() => api('/int/users'), [])
  const cur = (document.cookie.match(/(?:^|; )mt_user=([^;]*)/) || [])[1] || 'res.etran'
  const change = (e) => { document.cookie = `mt_user=${e.target.value}; path=/`; window.location.reload() }
  return (
    <span className="user">
      <select value={cur} onChange={change} title="Demo: act as" style={{ fontSize: 12, padding: '2px 4px' }}>
        {(users || [{ USR_ID: cur, USR_NM: cur }]).map((u) => <option key={u.USR_ID} value={u.USR_ID}>{u.USR_NM} ({u.ROLE_CD}{u.UNIT_CD !== 'ALL' ? ', ' + u.UNIT_CD : ''})</option>)}
      </select>
      {' '}&middot; {new Date().toLocaleDateString('en-US')}
    </span>
  )
}

// ------------------------------------------------------------------ Shell
export default function App() {
  const hash = useHash()
  const ipMatch = hash.match(/^#\/ip\/(.+)$/)
  const nav = [
    ['In-Patient (IPD)', [['#/census', 'IPD Census'], ['#/discharged', 'Discharged Patients']]],
    ['Reports', [['#/mis', 'MIS: Discharge TAT']]],
    ['System Admin', [['#/admin/links', 'External Links'], ['#/admin/webhooks', 'Event Webhooks'], ['#/audit', 'Audit Trail']]],
  ]
  return (
    <>
      <div className="banner">
        <span className="logo">MediTrack HMS<small>v3.4.0 &middot; CityForward Health, Columbus OH</small></span>
        <ActAs />
      </div>
      <div className="ticker">&#9888; Scheduled server maintenance: Sunday 02:00-04:00 AM. Please save your work. &middot; Lab results interface (HL7) delayed by 15 min on weekends. &middot; Contact IT ext. 2204 for password reset.</div>
      <div className="layout">
        <div className="nav">
          {nav.map(([g, items]) => (
            <div key={g}>
              <div className="grp">{g}</div>
              {items.map(([h, l]) => <a key={h} href={h} className={hash === h ? 'cur' : ''}>{l}</a>)}
            </div>
          ))}
        </div>
        <div className="main">
          {ipMatch ? <Patient ip={ipMatch[1]} key={ipMatch[1]} />
            : hash === '#/discharged' ? <Discharged />
            : hash === '#/admin/links' ? <ExtLinks />
            : hash === '#/admin/webhooks' ? <Webhooks />
            : hash === '#/mis' ? <Mis />
            : hash === '#/audit' ? <Audit />
            : <Census />}
        </div>
      </div>
      <div className="footer">&copy; 2011-2018 Medisoft Solutions Inc. &middot; MediTrack HMS v3.4.0 (Clarity UI) &middot; Best viewed in Google Chrome at 1366x768.</div>
    </>
  )
}
