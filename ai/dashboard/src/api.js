let token = null
export const setToken = (t) => { token = t }
const base = () => `${location.origin}/ai/api`

export async function api(path, { method = 'GET', body } = {}) {
  const r = await fetch(`${base()}${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : r.statusText); e.status = r.status; throw e }
  return data
}
