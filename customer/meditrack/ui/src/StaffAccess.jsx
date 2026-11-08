import { useEffect, useRef, useState } from 'react'
import { api, post, useLoad } from './lib.js'

const ROLE = { ATTENDING: 'Attending', RESIDENT: 'Resident', NURSE: 'Nurse', HIM_CODER: 'HIM coder', PA_COORD: 'Prior-auth coordinator', IT_ADMIN: 'IT administrator', COMPLIANCE: 'Compliance' }
const short = (id) => (id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id)
const when = (t) => (t ? new Date(t).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '—')

// one place for "run an action, show its error next to the buttons that caused it"
function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (fn) => {
    setBusy(true); setError('')
    try { await fn(); return true } catch (e) { setError(e.message); return false } finally { setBusy(false) }
  }
  return [run, busy, error, setError]
}

const Err = ({ error }) => (error ? <div className="err" role="alert" style={{ margin: '6px 0' }}>{error}</div> : null)

function ProfileForm({ meta, initial, submit, onSubmit, onCancel, busy, error }) {
  const [f, setF] = useState(initial)
  const set = (k, v) => setF((o) => ({ ...o, [k]: v }))
  const needsUnit = meta.unit_roles.includes(f.role)
  const clinician = meta.clinician_roles.includes(f.role)
  const pick = (role) => setF((o) => ({ ...o, role, unit: meta.unit_roles.includes(role) ? (o.unit === 'ALL' ? meta.units[0] : o.unit) : 'ALL', dr_id: meta.clinician_roles.includes(role) ? o.dr_id : '' }))
  return (
    <form className="formrow" onSubmit={(e) => { e.preventDefault(); onSubmit({ ...f, dr_id: f.dr_id || null }) }}>
      {'user_id' in f && <label>Staff ID <input type="text" size={16} value={f.user_id} onChange={(e) => set('user_id', e.target.value)} placeholder="nurse.smith" /></label>}
      <label>Name <input type="text" size={26} value={f.name} onChange={(e) => set('name', e.target.value)} /></label>
      <label>Role <select value={f.role} onChange={(e) => pick(e.target.value)}>{meta.roles.map((r) => <option key={r} value={r}>{ROLE[r] || r}</option>)}</select></label>
      <label>Ward <select value={needsUnit ? f.unit : 'ALL'} disabled={!needsUnit} onChange={(e) => set('unit', e.target.value)}>
        {needsUnit ? meta.units.map((u) => <option key={u}>{u}</option>) : <option>ALL</option>}</select></label>
      {clinician && <label>Practitioner record <select value={f.dr_id || ''} onChange={(e) => set('dr_id', e.target.value)}>
        <option value="">none</option>{meta.practitioners.map((p) => <option key={p.DR_ID} value={p.DR_ID}>{p.DR_NM}</option>)}</select></label>}
      <button className="primary" disabled={busy || f.name.trim().length < 3}>{busy ? 'Saving…' : submit}</button>
      <button type="button" onClick={onCancel}>Cancel</button>
      <Err error={error} />
    </form>
  )
}

function Links({ s, meta, reload }) {
  const [run, busy, error, setError] = useAction()
  const [adding, setAdding] = useState(false)
  const [oid, setOid] = useState('')
  const link = async () => { if (await run(() => post(`/int/admin/staff/${s.USR_ID}/links`, { subject: oid }))) { setAdding(false); setOid(''); reload() } }
  const change = (action, i) => run(async () => { await post(`/int/admin/links/${action}`, { issuer: i.ISSUER, subject: i.SUBJECT, user_id: s.USR_ID }); reload() })
  return (
    <div>
      {s.IDENTITIES.map((i) => (
        <div key={i.ISSUER + i.SUBJECT} style={{ whiteSpace: 'nowrap' }}>
          <code title={`${i.SUBJECT}\nlinked ${when(i.CREATED_AT)}${i.LINKED_BY ? ' by ' + i.LINKED_BY : ''}`} style={{ opacity: i.ACTIVE ? 1 : .5 }}>{i.ISSUER === 'local' ? 'local password' : short(i.SUBJECT)}{i.ACTIVE ? '' : ' (off)'}</code>{' '}
          <button disabled={busy} onClick={() => change(i.ACTIVE ? 'disable' : 'enable', i)}>{i.ACTIVE ? 'Disable' : 'Enable'}</button>{' '}
          <button disabled={busy} onClick={() => change('remove', i)}>Remove</button>
        </div>
      ))}
      {!s.IDENTITIES.length && <span className="hint">No sign-in linked</span>}
      {meta.entra_enabled && (adding
        ? <form className="formrow" onSubmit={(e) => { e.preventDefault(); link() }}>
            <input type="text" size={38} autoFocus value={oid} onChange={(e) => setOid(e.target.value)} placeholder="Entra object ID (8-4-4-4-12)" />
            <button className="primary" disabled={busy || !oid.trim()}>Link</button><button type="button" onClick={() => { setAdding(false); setError('') }}>Cancel</button>
          </form>
        : <button disabled={busy} onClick={() => setAdding(true)} style={{ marginTop: 4 }}>+ Link sign-in</button>)}
      <Err error={error} />
    </div>
  )
}

function StaffTab({ meta, reload }) {
  const [text, setText] = useState('')
  const [role, setRole] = useState('')
  const [inactive, setInactive] = useState(false)
  const [editing, setEditing] = useState(null)
  const [adding, setAdding] = useState(false)
  const [confirming, setConfirming] = useState(null)
  const [run, busy, error] = useAction()
  const [rowRun, rowBusy, rowError] = useAction()
  const q = text.trim().toLowerCase()
  const shown = meta.staff.filter((s) => (inactive || s.IS_ACTV) && (!role || s.ROLE_CD === role) && (!q || `${s.USR_ID} ${s.USR_NM}`.toLowerCase().includes(q)))
  const setActive = async (s, active) => { if (await rowRun(() => post(`/int/admin/staff/${s.USR_ID}/active`, { active }))) { setConfirming(null); reload() } }
  const fresh = { user_id: '', name: '', role: 'NURSE', unit: meta.units[0], dr_id: '' }
  return (
    <>
      <div className="formrow" style={{ marginBottom: 8 }}>
        <input type="text" size={24} placeholder="Search name or staff ID" value={text} onChange={(e) => setText(e.target.value)} />
        <select value={role} onChange={(e) => setRole(e.target.value)}><option value="">All roles</option>{meta.roles.map((r) => <option key={r} value={r}>{ROLE[r] || r}</option>)}</select>
        <label><input type="checkbox" checked={inactive} onChange={(e) => setInactive(e.target.checked)} /> Show deactivated</label>
        <button className="primary" onClick={() => setAdding(!adding)}>{adding ? 'Close' : 'Add staff account'}</button>
        <span className="hint">{shown.length} of {meta.staff.length} accounts</span>
      </div>
      {adding && <fieldset><legend>New staff account</legend>
        <ProfileForm meta={meta} initial={fresh} submit="Create account" busy={busy} error={error} onCancel={() => setAdding(false)}
          onSubmit={async (f) => { if (await run(() => post('/int/admin/staff', { ...f, user_id: f.user_id.trim() }))) { setAdding(false); reload() } }} />
      </fieldset>}
      <Err error={rowError} />
      <table className="grid">
        <thead><tr><th>Staff ID</th><th>Name</th><th>Role</th><th>Ward</th><th>Sign-in links</th><th>Last sign-in</th><th>Status</th><th></th></tr></thead>
        <tbody>
          {shown.map((s) => (
            editing === s.USR_ID
              ? <tr key={s.USR_ID}><td colSpan={8}><fieldset><legend>Edit {s.USR_ID}</legend>
                  <ProfileForm meta={meta} initial={{ name: s.USR_NM, role: s.ROLE_CD, unit: s.UNIT_CD, dr_id: s.DR_ID || '' }} submit="Save changes" busy={rowBusy} error={rowError} onCancel={() => setEditing(null)}
                    onSubmit={async (f) => { if (await rowRun(() => post(`/int/admin/staff/${s.USR_ID}/update`, f))) { setEditing(null); reload() } }} />
                </fieldset></td></tr>
              : <tr key={s.USR_ID} style={{ opacity: s.IS_ACTV ? 1 : .6 }}>
                  <td><b>{s.USR_ID}</b>{s.USR_ID === meta.me && <span className="hint"> (you)</span>}</td><td>{s.USR_NM}</td><td>{ROLE[s.ROLE_CD] || s.ROLE_CD}</td><td>{s.UNIT_CD}</td>
                  <td className="wrap"><Links s={s} meta={meta} reload={reload} /></td>
                  <td>{when(s.LAST_LOGIN)}{s.OPEN_SESSIONS > 0 && <div className="hint">{s.OPEN_SESSIONS} open</div>}</td>
                  <td><span className={`sts ${s.IS_ACTV ? 'ADM' : 'DSCH'}`}>{s.IS_ACTV ? 'ACTIVE' : 'DEACTIVATED'}</span></td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button disabled={rowBusy} onClick={() => setEditing(s.USR_ID)}>Edit</button>{' '}
                    {confirming === s.USR_ID
                      ? <><button className="primary" disabled={rowBusy} onClick={() => setActive(s, false)}>Confirm deactivate</button> <button onClick={() => setConfirming(null)}>Keep</button></>
                      : s.IS_ACTV ? <button disabled={rowBusy} onClick={() => setConfirming(s.USR_ID)}>Deactivate</button>
                        : <button disabled={rowBusy} onClick={() => setActive(s, true)}>Reactivate</button>}
                  </td>
                </tr>
          ))}
          {!shown.length && <tr><td colSpan={8} className="hint">No staff accounts match.</td></tr>}
        </tbody>
      </table>
      <p className="hint">Deactivating an account ends its open sessions at once. Role and ward changes take effect on the person's next click.
        {!meta.entra_enabled && ' Microsoft sign-in is not enabled on this system, so sign-in links cannot be added here.'}</p>
    </>
  )
}

function RequestsTab({ meta, requests, reload }) {
  const [run, busy, error] = useAction()
  const [choice, setChoice] = useState({})
  const accounts = meta.staff.filter((s) => s.IS_ACTV)
  const key = (r) => r.ISSUER + r.SUBJECT
  return (
    <>
      <p className="hint">When someone signs in with Microsoft but has no staff account, the attempt is kept here. Choose the account they should use and assign it; they can then sign in again.</p>
      <Err error={error} />
      <table className="grid">
        <thead><tr><th>Name</th><th>Email</th><th>Entra object ID</th><th>Attempts</th><th>Last attempt</th><th>Status</th><th>Assign to staff account</th></tr></thead>
        <tbody>
          {requests?.map((r) => (
            <tr key={key(r)}>
              <td>{r.DISPLAY_NAME || '—'}</td><td>{r.EMAIL || '—'}</td><td><code title={r.SUBJECT}>{short(r.SUBJECT)}</code></td><td>{r.ATTEMPTS}</td><td>{when(r.LAST_SEEN)}</td>
              <td><span className={`sts ${r.STATUS === 'PENDING' ? 'DA' : 'DSCH'}`}>{r.STATUS}</span></td>
              <td style={{ whiteSpace: 'nowrap' }}>
                <select value={choice[key(r)] || ''} onChange={(e) => setChoice({ ...choice, [key(r)]: e.target.value })}>
                  <option value="">Choose account…</option>{accounts.map((s) => <option key={s.USR_ID} value={s.USR_ID}>{s.USR_ID} · {ROLE[s.ROLE_CD] || s.ROLE_CD}{s.UNIT_CD !== 'ALL' ? ` · ${s.UNIT_CD}` : ''}</option>)}
                </select>{' '}
                <button className="primary" disabled={busy || !choice[key(r)]} onClick={async () => { if (await run(() => post('/int/admin/access-requests/assign', { issuer: r.ISSUER, subject: r.SUBJECT, user_id: choice[key(r)] }))) reload() }}>Assign</button>{' '}
                {r.STATUS === 'PENDING' && <button disabled={busy} onClick={async () => { if (await run(() => post('/int/admin/access-requests/dismiss', { issuer: r.ISSUER, subject: r.SUBJECT }))) reload() }}>Dismiss</button>}
              </td>
            </tr>
          ))}
          {requests && !requests.length && <tr><td colSpan={7} className="hint">No open access requests.</td></tr>}
        </tbody>
      </table>
    </>
  )
}

function SessionsTab() {
  const [rows, err, reload] = useLoad(() => api('/int/admin/sessions'), [])
  const [run, busy, error] = useAction()
  return (
    <>
      <p className="hint">Sessions end after 15 minutes without activity or 8 hours in total. Signing someone out takes effect on their next click.</p>
      <Err error={error || err} />
      <table className="grid">
        <thead><tr><th>Staff</th><th>Role</th><th>Signed in</th><th>Last active</th><th>Method</th><th></th></tr></thead>
        <tbody>
          {rows?.map((s) => (
            <tr key={s.ID}>
              <td><b>{s.USER_ID}</b> <span className="hint">{s.USR_NM}</span></td><td>{ROLE[s.ROLE_CD] || s.ROLE_CD}</td><td>{when(s.CREATED_AT)}</td><td>{when(s.LAST_SEEN)}</td>
              <td>{s.ISSUER === 'local' ? 'Password' : 'Microsoft'}</td>
              <td>{s.CURRENT ? <span className="hint">this session</span> : <button disabled={busy} onClick={async () => { if (await run(() => post('/int/admin/sessions/revoke', { id: s.ID }))) reload() }}>Sign out</button>}</td>
            </tr>
          ))}
          {rows && !rows.length && <tr><td colSpan={6} className="hint">No open sessions.</td></tr>}
        </tbody>
      </table>
      <button style={{ marginTop: 8 }} onClick={reload}>Refresh</button>
    </>
  )
}

export default function StaffAccess({ onChange }) {
  const [meta, err, reloadMeta] = useLoad(() => api('/int/admin/staff'), [])
  const [requests, , reloadRequests] = useLoad(() => api('/int/admin/access-requests'), [])
  const [tab, setTab] = useState('staff')
  const reload = () => { reloadMeta(); reloadRequests(); onChange?.() }
  useEffect(() => { const t = setInterval(() => { reloadRequests(); onChange?.() }, 30000); return () => clearInterval(t) }, [])
  const open = (name) => { setTab(name); if (name === 'requests') { reloadRequests(); onChange?.() } }
  const pending = requests?.filter((r) => r.STATUS === 'PENDING').length || 0
  return (
    <fieldset>
      <legend>System Admin - Staff &amp; Access</legend>
      <p className="hint">Microsoft Entra proves who someone is; this is where you decide which MediTrack staff accounts they may use. Every change is recorded in the Audit Trail.</p>
      {err && <div className="err" role="alert">{err}</div>}
      <div className="tabs" style={{ marginBottom: 10 }}>
        <button className={tab === 'staff' ? 'cur' : ''} onClick={() => open('staff')}>Staff accounts{meta ? ` (${meta.staff.filter((s) => s.IS_ACTV).length})` : ''}</button>
        <button className={tab === 'requests' ? 'cur' : ''} onClick={() => open('requests')}>Access requests{pending ? ` (${pending})` : ''}</button>
        <button className={tab === 'sessions' ? 'cur' : ''} onClick={() => open('sessions')}>Open sessions</button>
      </div>
      {!meta ? <div>Loading staff…</div>
        : tab === 'staff' ? <StaffTab meta={meta} reload={reload} />
        : tab === 'requests' ? <RequestsTab meta={meta} requests={requests} reload={reload} />
        : <SessionsTab />}
    </fieldset>
  )
}

// People linked to more than one account (for example a clinician who is also an administrator) switch here, without signing in again.
export function AccountMenu({ session }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const box = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const away = (e) => { if (!box.current?.contains(e.target)) setOpen(false) }
    const esc = (e) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', away); document.addEventListener('keydown', esc)
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc) }
  }, [open])
  const switchTo = async (user) => {
    setError('')
    try { await post('/auth/switch', { user }); window.location.hash = ''; window.location.reload() } catch (e) { setError(e.message) }
  }
  return (
    <span ref={box} style={{ position: 'relative' }}>
      <button onClick={() => setOpen(!open)} aria-expanded={open}>Switch account ▾</button>
      {open && <div style={{ position: 'absolute', right: 0, top: 28, zIndex: 5, background: '#fff', color: '#1f2933', border: '1px solid #d9dee5', borderRadius: 4, boxShadow: '0 4px 12px #0003', minWidth: 260, padding: 4, textAlign: 'left' }}>
        {session.accounts.map((a) => (
          <button key={a.user} disabled={a.user === session.user} onClick={() => switchTo(a.user)} style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: 2 }}>
            <b>{a.name}</b><br /><span className="hint">{ROLE[a.role] || a.role}{a.unit !== 'ALL' ? ` · ${a.unit}` : ''}{a.user === session.user ? ' · current' : ''}</span>
          </button>
        ))}
        {error && <div className="err" role="alert" style={{ padding: 4 }}>{error}</div>}
      </div>}
    </span>
  )
}
