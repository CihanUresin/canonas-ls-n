import { useEffect, useState } from 'react'
import Login from './Login.jsx'
import { logout, me } from './api.js'

export default function App() {
  const [user, setUser] = useState(null)
  const [checking, setChecking] = useState(true)

  useEffect(() => {
    me().then(setUser).catch(() => {}).finally(() => setChecking(false))
  }, [])

  async function handleLogout() {
    await logout().catch(() => {})
    setUser(null)
  }

  if (checking) return null

  if (user) {
    return (
      <main className="page">
        <div className="card">
          <h1>Hoş geldin</h1>
          <p className="muted">{user.email} olarak giriş yaptın.</p>
          <button className="btn" onClick={handleLogout}>Çıkış yap</button>
        </div>
      </main>
    )
  }

  return <Login onSuccess={setUser} />
}
