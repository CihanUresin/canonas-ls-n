async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || 'Bir hata oluştu.')
  return data
}

const post = (path, body) => request(path, { method: 'POST', body: JSON.stringify(body) })

export const login = (creds) => post('/login', creds)
export const register = (creds) => post('/register', creds)
export const logout = () => post('/logout')
export const me = () => request('/me')
