# canonas-ls-n

React + Vite login/kayıt sayfası ve Express backend.

## Çalıştırma

```bash
npm install
npm run dev   # frontend (5173) + API (3001), /api proxy'lenir
```

## Backend (`server/index.js`)

| Endpoint | Açıklama |
|---|---|
| `POST /api/register` | `{email, password}` ile kayıt, oturum açar |
| `POST /api/login` | Giriş |
| `POST /api/logout` | Çıkış (cookie siler) |
| `GET /api/me` | Oturumdaki kullanıcı |

- Şifreler bcrypt ile hash'lenir, oturum httpOnly cookie içinde JWT (7 gün).
- Giriş/kayıt için rate limit (15 dk'da 20 istek).
- Kullanıcılar `server/data/users.json` içinde tutulur (git'e girmez). Büyürse gerçek bir DB'ye geçir.

## Production

```bash
npm run build
NODE_ENV=production JWT_SECRET=uzun-rastgele-bir-deger npm start
```
