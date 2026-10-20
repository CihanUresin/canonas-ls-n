import express from 'express'
import bcrypt from 'bcryptjs'
import jwt from 'jsonwebtoken'
import cookieParser from 'cookie-parser'
import rateLimit from 'express-rate-limit'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const PORT = process.env.PORT || 3001
const IS_PROD = process.env.NODE_ENV === 'production'
// Prod'da JWT_SECRET zorunlu; dev'de her başlatmada rastgele üretilir.
const JWT_SECRET = process.env.JWT_SECRET || (IS_PROD ? null : crypto.randomBytes(32).toString('hex'))
if (!JWT_SECRET) throw new Error('JWT_SECRET ortam değişkeni gerekli.')

const COOKIE = 'token'
const MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// --- Basit JSON dosya veritabanı ---
const DB_FILE = path.join(__dirname, 'data', 'users.json')
fs.mkdirSync(path.dirname(DB_FILE), { recursive: true })
let users = fs.existsSync(DB_FILE) ? JSON.parse(fs.readFileSync(DB_FILE, 'utf8')) : []
const save = () => {
  const tmp = DB_FILE + '.tmp'
  fs.writeFileSync(tmp, JSON.stringify(users, null, 2))
  fs.renameSync(tmp, DB_FILE)
}

// Kullanıcı yokken de bcrypt süresi harcamak için (zamanlama saldırısı).
const DUMMY_HASH = bcrypt.hashSync('dummy-password', 12)

const app = express()
app.disable('x-powered-by')
app.use(express.json({ limit: '10kb' }))
app.use(cookieParser())

const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  limit: 20,
  standardHeaders: true,
  legacyHeaders: false,
  message: { error: 'Çok fazla deneme. Lütfen daha sonra tekrar dene.' },
})

function setSession(res, user) {
  const token = jwt.sign({ sub: user.id }, JWT_SECRET, { expiresIn: '7d' })
  res.cookie(COOKIE, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: IS_PROD,
    maxAge: MAX_AGE_MS,
  })
}

const publicUser = (u) => ({ id: u.id, email: u.email })

function requireAuth(req, res, next) {
  try {
    const { sub } = jwt.verify(req.cookies[COOKIE], JWT_SECRET)
    const user = users.find((u) => u.id === sub)
    if (!user) throw new Error()
    req.user = user
    next()
  } catch {
    res.status(401).json({ error: 'Oturum geçersiz.' })
  }
}

function readCredentials(body) {
  const email = typeof body?.email === 'string' ? body.email.trim().toLowerCase() : ''
  const password = typeof body?.password === 'string' ? body.password : ''
  return { email, password }
}

app.post('/api/register', authLimiter, async (req, res) => {
  const { email, password } = readCredentials(req.body)
  if (!EMAIL_RE.test(email)) return res.status(400).json({ error: 'Geçerli bir e-posta gir.' })
  if (password.length < 6 || password.length > 72)
    return res.status(400).json({ error: 'Şifre 6-72 karakter olmalı.' })
  if (users.some((u) => u.email === email))
    return res.status(409).json({ error: 'Bu e-posta zaten kayıtlı.' })

  const user = {
    id: crypto.randomUUID(),
    email,
    passwordHash: await bcrypt.hash(password, 12),
    createdAt: new Date().toISOString(),
  }
  users.push(user)
  save()
  setSession(res, user)
  res.status(201).json(publicUser(user))
})

app.post('/api/login', authLimiter, async (req, res) => {
  const { email, password } = readCredentials(req.body)
  const user = users.find((u) => u.email === email)
  const ok = await bcrypt.compare(password, user ? user.passwordHash : DUMMY_HASH)
  if (!user || !ok) return res.status(401).json({ error: 'E-posta veya şifre hatalı.' })
  setSession(res, user)
  res.json(publicUser(user))
})

app.post('/api/logout', (req, res) => {
  res.clearCookie(COOKIE, { httpOnly: true, sameSite: 'lax', secure: IS_PROD })
  res.status(204).end()
})

app.get('/api/me', requireAuth, (req, res) => res.json(publicUser(req.user)))

app.use('/api', (req, res) => res.status(404).json({ error: 'Bulunamadı.' }))

// Prod'da build edilmiş frontend'i de sun.
const DIST = path.join(__dirname, '..', 'dist')
if (fs.existsSync(DIST)) {
  app.use(express.static(DIST))
  app.use((req, res) => res.sendFile(path.join(DIST, 'index.html')))
}

app.listen(PORT, () => console.log(`API http://localhost:${PORT}`))
