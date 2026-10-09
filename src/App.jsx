import { useState } from 'react'
import Login from './Login.jsx'

export default function App() {
  const [user, setUser] = useState(null)

  if (user) {
    return (
      <main className="page">
        <div className="card">
          <h1>Hoş geldin</h1>
          <p className="muted">{user.email} olarak giriş yaptın.</p>
          <button className="btn" onClick={() => setUser(null)}>Çıkış yap</button>
        </div>
      </main>
    )
  }

  return <Login onSuccess={setUser} />
}
