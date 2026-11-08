import { useCallback, useEffect, useState } from 'react'

export const api = async (path, opts) => {
  const r = await fetch(path, opts)
  if (r.status === 401 && path.startsWith('/int/')) window.dispatchEvent(new Event('meditrack-session-expired'))
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((e) => e.msg).join('; ') : `ERROR ${r.status}`)
  }
  return r.json()
}
export const post = (path, body) => {
  const csrf = (document.cookie.match(/(?:^|; )mt_csrf=([^;]*)/) || [])[1]
  return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) }, body: body ? JSON.stringify(body) : undefined })
}

// MediTrack stores dates as 'YYYY-MM-DD HH:MM' text; US screens show MM/DD/YYYY
export const fd = (s) => (s ? s.replace(/^(\d{4})-(\d{2})-(\d{2})/, '$2/$3/$1') : '')

export function useLoad(fn, deps) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const reload = useCallback(() => {
    fn().then(setData).catch((e) => setErr(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  useEffect(reload, [reload])
  return [data, err, reload]
}

