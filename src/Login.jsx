import { useState } from 'react'
import { login } from './api.js'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export default function Login({ onSuccess }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [errors, setErrors] = useState({})
  const [serverError, setServerError] = useState('')
  const [loading, setLoading] = useState(false)

  function validate() {
    const e = {}
    if (!EMAIL_RE.test(email)) e.email = 'Geçerli bir e-posta gir.'
    if (password.length < 6) e.password = 'Şifre en az 6 karakter olmalı.'
    return e
  }

  async function handleSubmit(ev) {
    ev.preventDefault()
    setServerError('')
    const e = validate()
    setErrors(e)
    if (Object.keys(e).length) return

    setLoading(true)
    try {
      onSuccess(await login({ email, password }))
    } catch (err) {
      setServerError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="page">
      <form className="card" onSubmit={handleSubmit} noValidate>
        <h1>Giriş Yap</h1>
        <p className="muted">Hesabına erişmek için bilgilerini gir.</p>

        {serverError && <div className="alert" role="alert">{serverError}</div>}

        <label htmlFor="email">E-posta</label>
        <input
          id="email"
          type="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          aria-invalid={!!errors.email}
        />
        {errors.email && <span className="error">{errors.email}</span>}

        <label htmlFor="password">Şifre</label>
        <div className="pw">
          <input
            id="password"
            type={showPw ? 'text' : 'password'}
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={!!errors.password}
          />
          <button type="button" className="link" onClick={() => setShowPw((s) => !s)}>
            {showPw ? 'Gizle' : 'Göster'}
          </button>
        </div>
        {errors.password && <span className="error">{errors.password}</span>}

        <button className="btn" type="submit" disabled={loading}>
          {loading ? 'Giriş yapılıyor…' : 'Giriş Yap'}
        </button>

        <p className="hint">Demo: demo@example.com / 123456</p>
      </form>
    </main>
  )
}
