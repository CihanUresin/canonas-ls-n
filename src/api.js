// Gerçek bir backend bağlanana kadar kullanılan sahte giriş.
// Kendi API'nle değiştirmek için bu fonksiyonu fetch('/api/login', ...) ile güncelle.
export async function login({ email, password }) {
  await new Promise((r) => setTimeout(r, 600))
  if (email === 'demo@example.com' && password === '123456') {
    return { email }
  }
  throw new Error('E-posta veya şifre hatalı.')
}
