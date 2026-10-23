// SMART on FHIR "EHR launch" with PKCE, done in the browser (a public client: there is no secret to protect).
//   1. EHR opens   /ai/launch?iss=<FHIR base>&launch=<opaque id>
//   2. we read     <iss>/.well-known/smart-configuration, then send the user to the authorization endpoint (with the launch id and a PKCE challenge)
//   3. EHR returns /ai/auth/callback?code=...&state=...
//   4. we swap the code (+ the PKCE verifier) for an access token. The token response says which patient and encounter we were launched on.
// The access token lives in memory only. A reload means "open me from the EHR again", which is the safe default for a clinical tool.
const CLIENT_ID = 'cityforward-ai-dashboard'
const SCOPE = 'launch openid fhirUser patient/*.read patient/DocumentReference.write user/Practitioner.read'
const KEY = 'smart-pending'

const b64url = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const rand = (n = 32) => b64url(crypto.getRandomValues(new Uint8Array(n)))
export const redirectUri = () => `${location.origin}/ai/auth/callback`

export async function startLaunch(iss, launch) {
  const cfg = await (await fetch(`${iss}/.well-known/smart-configuration`)).json()
  const verifier = rand(48)
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)))
  const state = rand(16)
  sessionStorage.setItem(KEY, JSON.stringify({ verifier, state, iss, token_endpoint: cfg.token_endpoint }))
  const q = new URLSearchParams({ response_type: 'code', client_id: CLIENT_ID, redirect_uri: redirectUri(), scope: SCOPE, state, aud: iss, launch,
                                  code_challenge: challenge, code_challenge_method: 'S256' })
  location.assign(`${cfg.authorization_endpoint}?${q}`)
}

export async function finishLaunch(code, state) {
  const pending = JSON.parse(sessionStorage.getItem(KEY) || 'null')
  sessionStorage.removeItem(KEY)
  if (!pending || pending.state !== state) throw new Error('This sign-in did not start here. Open the app again from the patient chart.')
  const r = await fetch(pending.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code, redirect_uri: redirectUri(), client_id: CLIENT_ID, code_verifier: pending.verifier }) })
  const body = await r.json()
  if (!r.ok) throw new Error(body.error_description || body.error || 'token request failed')
  // the id_token carries who signed in; it is only used to greet the user (the API verifies the access token itself)
  let who = {}
  try { who = JSON.parse(atob(body.id_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))) } catch { /* optional */ }
  return { token: body.access_token, patient: body.patient, encounter: body.encounter, user: who.name || '', role: who.role || '', iss: pending.iss }
}
