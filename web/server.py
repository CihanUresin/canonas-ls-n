#!/usr/bin/env python3
"""Sunucum: oturum açma destekli küçük web sunucusu (yalnızca standart kütüphane).

Çalıştırma:           python3 server.py
Kullanıcı ekleme:     python3 server.py adduser ornek@eposta.com
Kullanıcı silme:      python3 server.py deluser ornek@eposta.com
Kullanıcıları listele: python3 server.py users
Yükleme izni verme:   python3 server.py izinver ornek@eposta.com
Yükleme iznini alma:  python3 server.py izinal ornek@eposta.com

Her kullanıcının kendi galerisi vardır ve yalnızca sahibi içine yükleyebilir, silebilir, taşıyabilir.
Galeriler herkes tarafından görülebilir; kullanıcı adıyla aranır. Yeni hesaplar yükleme izniyle açılır,
izinal ile bir hesabın yüklemesi kapatılabilir.
Dosyalar doğrudan medya/<kullanıcı no>/ klasörüne (ya da içindeki bir alt klasöre) kopyalanarak da eklenebilir.
"""
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

BASE = Path(__file__).resolve().parent
PUBLIC = BASE / "public"
DB_PATH = BASE / "data" / "app.db"

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
# HTTPS arkasında çalışırken COOKIE_SECURE=1 verin.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"
# Herkese açık kayıt istemiyorsanız ALLOW_REGISTER=0 verin.
ALLOW_REGISTER = os.environ.get("ALLOW_REGISTER", "1") == "1"

COOKIE_NAME = "sid"
SESSION_TTL = 60 * 60 * 24            # "Beni hatırla" işaretli değilse 1 gün
REMEMBER_TTL = 60 * 60 * 24 * 30      # işaretliyse 30 gün
MAX_BODY = 4096
MIN_PASSWORD = 6
MAX_PASSWORD = 256
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_RE = re.compile(r"^[\w.\-]{3,20}$")
SEARCH_LIMIT = 20

# Deneme sınırı: IP başına WINDOW saniyede en fazla MAX_ATTEMPTS başarısız/istek.
MAX_ATTEMPTS = 10
WINDOW = 15 * 60

# Sayfalar: yol -> (dosya, erişim)
#   "guest": yalnızca giriş yapmamışlar, "user": yalnızca giriş yapmışlar, "public": herkes
PAGES = {
    "/": ("home.html", "public"),
    "/index.html": ("index.html", "guest"),
    "/register.html": ("register.html", "guest"),
    "/home.html": ("home.html", "public"),
    "/galeri.html": ("galeri.html", "public"),
    "/endpoints.html": ("endpoints.html", "public"),
}

# Sunucunun bütün uç noktaları: (yöntem, yol, erişim, girdi, açıklama).
# /endpoints.html bu listeyi /api/endpoints üzerinden gösterir; yeni bir yol eklerken buraya da yazın.
#   erişim: "herkes", "misafir" (giriş yapmamışlar), "giriş" (oturum gerekli), "yükleme" (yükleme izni gerekli)
ENDPOINTS = [
    ("GET", "/", "herkes", "", "Ana sayfa."),
    ("GET", "/home.html", "herkes", "", "Ana sayfa."),
    ("GET", "/index.html", "misafir", "", "Giriş sayfası; giriş yapmışsa ana sayfaya yönlendirir."),
    ("GET", "/register.html", "misafir", "", "Kayıt sayfası; kayıt kapalıysa girişe yönlendirir."),
    ("GET", "/galeri.html", "herkes", "?u=kullanıcı adı", "Galeri sayfası."),
    ("GET", "/kombinler.html", "herkes", "", "Eski adres; /galeri.html sayfasına yönlendirir."),
    ("GET", "/endpoints.html", "herkes", "", "Bu sayfa: uç noktaların listesi."),
    ("GET", "/gorsel/<ad>", "herkes", "", "Sayfaların kullandığı görseller (public/gorsel/)."),
    ("GET", "/medya/<kullanıcı no>/[<klasör>/]<dosya>", "herkes", "Range başlığı (isteğe bağlı)",
     "Galerideki resim ya da video."),
    ("GET", "/api/endpoints", "herkes", "", "Bu listenin JSON hali."),
    ("GET", "/api/me", "giriş", "", "Giriş yapmış kullanıcının e-postası ve kullanıcı adı."),
    ("GET", "/api/medya", "herkes", "?u=kullanıcı adı",
     "Kullanıcının galerisindeki dosyalar ve klasörler; u yoksa giriş yapanın kendi galerisi."),
    ("GET", "/api/kullanicilar", "herkes", "?q=arama",
     "Adında sorgu geçen kullanıcılar; sorgu boşsa en yeni hesaplar."),
    ("POST", "/api/login", "herkes", "JSON: username, email, password, remember", "Oturum açar."),
    ("POST", "/api/register", "herkes", "JSON: username, email, password", "Hesap açar ve oturum başlatır."),
    ("POST", "/api/logout", "herkes", "", "Oturumu kapatır."),
    ("POST", "/api/yukle", "yükleme", "?name=dosya adı&klasor=klasör; gövde: ham dosya",
     "Kendi galerine resim ya da video yükler."),
    ("POST", "/api/medya/sil", "yükleme", "JSON: file, folder", "Galerinden bir dosyayı siler."),
    ("POST", "/api/medya/tasi", "yükleme", "JSON: file, folder, to", "Dosyayı başka bir klasöre taşır."),
    ("POST", "/api/klasor", "yükleme", "JSON: name", "Galerinde klasör açar."),
    ("POST", "/api/klasor/sil", "yükleme", "JSON: name", "Boş bir klasörü siler."),
]

# Sayfaların kullandığı görseller (arka plan vb.): public/gorsel/ içindeki dosyalar /gorsel/ad ile sunulur.
ASSETS = PUBLIC / "gorsel"
ASSET_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".svg": "image/svg+xml"}

# Galeriler: her kullanıcının dosyaları medya/<kullanıcı no>/ altında durur ve giriş yapmadan
# herkes tarafından görülebilir. İçindeki alt klasörler (tek seviye) galeride ayrı klasörler olarak görünür.
MEDIA = BASE / "medya"
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".avif": "image/avif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}
# Dosya başına yükleme sınırı (MB).
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1024 * 1024
# Yükleme sonrası diskte en az bu kadar boş yer kalmalı.
MIN_FREE = 1024 * 1024 * 1024
CHUNK = 256 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


# ---------- veritabanı ----------

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(mode=0o700, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL,
                pw_hash BLOB NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
            """
        )
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
        if "can_upload" not in columns:
            # Sütun eklenmeden önce var olan hesaplar yükleme izni alır; yeni kayıtlar izinsiz başlar.
            conn.execute("ALTER TABLE users ADD COLUMN can_upload INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE users SET can_upload = 1")
        if "username" not in columns:
            # Eski hesapların kullanıcı adı boş kalır; ilk girişte yazılan ad hesaba işlenir.
            conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
            conn.execute("ALTER TABLE users ADD COLUMN username_key TEXT")
            migrate_media(conn)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_key ON users(username_key)")
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    os.chmod(DB_PATH, 0o600)


def migrate_media(conn):
    """Kullanıcıya özel galerilerden önceki ortak galeriyi sunucu sahibinin galerisine taşır."""
    owner = conn.execute("SELECT id FROM users ORDER BY can_upload DESC, id LIMIT 1").fetchone()
    if owner is None or not MEDIA.is_dir() or not any(MEDIA.iterdir()):
        return
    old = MEDIA.with_name(MEDIA.name + ".eski")
    os.rename(MEDIA, old)
    MEDIA.mkdir()
    os.rename(old, user_root(owner["id"]))


# ---------- parola ve oturum ----------

def hash_password(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, **SCRYPT)


DUMMY_SALT = secrets.token_bytes(16)


def create_user(email, username, password, can_upload=True):
    salt = secrets.token_bytes(16)
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO users (email, username, username_key, salt, pw_hash, created_at, can_upload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email, username, username.casefold(), salt, hash_password(password, salt),
             int(time.time()), int(can_upload)),
        )
        return cur.lastrowid


def duplicate_message(error):
    """Çakışan alanın (e-posta ya da kullanıcı adı) iletisini döner."""
    if "email" in str(error):
        return "Bu e-posta ile bir hesap zaten var."
    return "Bu kullanıcı adı alınmış."


def claim_username(user_id, username):
    """Kullanıcı adı olmayan eski hesaba ad verir; ad başkasındaysa IntegrityError yükselir."""
    with db() as conn:
        conn.execute("UPDATE users SET username = ?, username_key = ? WHERE id = ? AND username_key IS NULL",
                     (username, username.casefold(), user_id))


def find_user(username):
    with db() as conn:
        return conn.execute("SELECT id, username FROM users WHERE username_key = ?",
                            (username.casefold(),)).fetchone()


def search_users(query):
    """Adında sorgu geçen kullanıcıların adlarını döner; sorgu boşsa en yeni hesaplar listelenir."""
    pattern = "%" + re.sub(r"([\\%_])", r"\\\1", query.casefold()) + "%"
    with db() as conn:
        rows = conn.execute(
            """SELECT username FROM users WHERE username_key LIKE ? ESCAPE '\\'
               ORDER BY """ + ("username_key" if query else "id DESC") + " LIMIT ?",
            (pattern, SEARCH_LIMIT)).fetchall()
    return [row["username"] for row in rows]


def check_login(email, password):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row is None:
        # Kullanıcı yokken de aynı süre harcansın (hesap varlığı sızmasın).
        hash_password(password, DUMMY_SALT)
        return None
    if hmac.compare_digest(hash_password(password, row["salt"]), row["pw_hash"]):
        return row
    return None


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id, ttl):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash(token), user_id, int(time.time()) + ttl),
        )
    return token


def session_user(token):
    if not token:
        return None
    with db() as conn:
        return conn.execute(
            """SELECT users.id, users.email, users.username, users.can_upload FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE token_hash = ? AND expires_at > ?""",
            (token_hash(token), int(time.time())),
        ).fetchone()


def delete_session(token):
    if token:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


# ---------- galeri ----------

def folder_name(name):
    """Klasör adını güvenli hale getirir; kullanılabilir bir ad kalmazsa boş döner."""
    return re.sub(r"[^\w\- ]+", "_", name).strip(" _")[:50]


def user_root(user_id):
    """Kullanıcının galerisinin durduğu dizin."""
    return MEDIA / str(int(user_id))


def media_dir(root, folder):
    """Galerideki klasörün dizinini döner ("" = klasörsüz); geçersizse ya da yoksa None."""
    if not isinstance(folder, str):
        return None
    if folder == "":
        return root
    path = root / folder
    return path if folder == folder_name(folder) and path.is_dir() else None


def media_file(root, name, folder=""):
    """Ad, klasörün doğrudan içindeki bir resim/video ise yolunu, değilse None döner."""
    directory = media_dir(root, folder)
    if directory is None or not isinstance(name, str):
        return None
    file = directory / name
    if (not name or "/" in name or "\\" in name or name.startswith(".")
            or file.suffix.lower() not in MEDIA_TYPES or not file.is_file()):
        return None
    return file


def list_folders(root):
    if not root.is_dir():
        return []
    return sorted((d.name for d in root.iterdir() if d.is_dir() and media_dir(root, d.name)),
                  key=str.casefold)


def list_media(root):
    """Galerinin bütün klasörlerindeki resim ve videoları en yeni başta olacak şekilde listeler."""
    if not root.is_dir():
        return []
    files = [(folder, f) for folder in [""] + list_folders(root)
             for f in media_dir(root, folder).iterdir() if media_file(root, f.name, folder)]
    files.sort(key=lambda item: item[1].stat().st_mtime, reverse=True)
    return [{"name": f.stem, "file": f.name, "folder": folder,
             "url": f"/medya/{root.name}/" + (quote(folder) + "/" if folder else "") + quote(f.name),
             "type": MEDIA_TYPES[f.suffix.lower()].split("/")[0]} for folder, f in files]


def upload_name(name, directory):
    """Dizine konacak dosya için güvenli ve boşta bir ad üretir; tür desteklenmiyorsa None döner."""
    stem, suffix = os.path.splitext(name.replace("\\", "/").rsplit("/", 1)[-1])
    suffix = suffix.lower()
    if suffix not in MEDIA_TYPES:
        return None
    stem = re.sub(r"[^\w\- ]+", "_", stem).strip(" _")[:50] or "dosya"
    candidate, n = stem + suffix, 2
    while (directory / candidate).exists():
        candidate, n = f"{stem}-{n}{suffix}", n + 1
    return candidate


# ---------- deneme sınırı ----------

_attempts = {}
_attempts_lock = threading.Lock()


def rate_limited(ip):
    now = time.time()
    with _attempts_lock:
        recent = [t for t in _attempts.get(ip, []) if now - t < WINDOW]
        _attempts[ip] = recent
        return len(recent) >= MAX_ATTEMPTS


def record_attempt(ip):
    with _attempts_lock:
        _attempts.setdefault(ip, []).append(time.time())


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "Sunucum"
    sys_version = ""

    # -- yardımcılar --

    def token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except cookies.CookieError:
            return None
        return jar[COOKIE_NAME].value if COOKIE_NAME in jar else None

    def cookie_header(self, value, max_age):
        parts = [f"{COOKIE_NAME}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        if COOKIE_SECURE:
            parts.append("Secure")
        return "; ".join(parts)

    def send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=(),
             cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, status, payload, headers=()):
        self.send(status, json.dumps(payload, ensure_ascii=False).encode(), headers=headers)

    def error(self, status, message):
        self.json(status, {"ok": False, "error": message})

    def redirect(self, location):
        self.send(302, headers=[("Location", location)])

    def read_json(self):
        """JSON gövdesini okur; hata durumunda yanıtı gönderip None döner."""
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            self.error(415, "İçerik türü application/json olmalı.")
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.error(411, "Content-Length gerekli.")
            return None
        if length > MAX_BODY:
            self.error(413, "İstek çok büyük.")
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            self.error(400, "Geçersiz JSON.")
            return None
        if not isinstance(data, dict):
            self.error(400, "Geçersiz JSON.")
            return None
        return data

    def query(self):
        return parse_qs(urlsplit(self.path).query)

    def credentials(self, data):
        """Kullanıcı adı, e-posta ve parolayı doğrular; hata durumunda yanıtı gönderip None döner."""
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(email, str) or not isinstance(password, str):
            self.error(400, "Kullanıcı adı, e-posta ve şifre gerekli.")
            return None
        username = username.strip()
        if not USERNAME_RE.match(username):
            self.error(400, "Kullanıcı adı 3-20 karakter olmalı; harf, rakam ve _ . - içerebilir.")
            return None
        email = email.strip().lower()
        if len(email) > 254 or not EMAIL_RE.match(email):
            self.error(400, "Geçerli bir e-posta girin.")
            return None
        if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            self.error(400, f"Şifre en az {MIN_PASSWORD} karakter olmalı.")
            return None
        return username, email, password

    def start_session(self, user_id, email, username, remember, status=200):
        ttl = REMEMBER_TTL if remember else SESSION_TTL
        token = create_session(user_id, ttl)
        self.json(
            status,
            {"ok": True, "user": {"email": email, "username": username}},
            headers=[("Set-Cookie", self.cookie_header(token, ttl if remember else None))],
        )

    # -- GET --

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/me":
            user = session_user(self.token())
            if user is None:
                return self.error(401, "Giriş yapılmamış.")
            return self.json(200, {"ok": True,
                                   "user": {"email": user["email"], "username": user["username"]}})

        if path == "/api/endpoints":
            keys = ("method", "path", "access", "input", "description")
            return self.json(200, {"ok": True, "endpoints": [dict(zip(keys, e)) for e in ENDPOINTS]})
        if path == "/api/medya":
            return self.api_media()
        if path == "/api/kullanicilar":
            query = self.query().get("q", [""])[0].strip()[:20]
            return self.json(200, {"ok": True, "users": search_users(query)})
        if path.startswith("/medya/"):
            return self.serve_media(path)
        if path.startswith("/gorsel/"):
            return self.serve_asset(unquote(path[len("/gorsel/"):]))
        if path == "/kombinler.html":
            return self.redirect("/galeri.html")

        page = PAGES.get(path)
        if page is None:
            return self.send(404, "Sayfa bulunamadı.".encode(), "text/plain; charset=utf-8")
        filename, access = page
        user = session_user(self.token())
        if access == "user" and user is None:
            return self.redirect("/index.html")
        if access == "guest" and user is not None:
            return self.redirect("/home.html")
        if filename == "register.html" and not ALLOW_REGISTER:
            return self.redirect("/index.html")
        self.send(200, (PUBLIC / filename).read_bytes(), "text/html; charset=utf-8")

    do_HEAD = do_GET

    def serve_asset(self, name):
        file = ASSETS / name
        if ("/" in name or "\\" in name or name.startswith(".")
                or file.suffix.lower() not in ASSET_TYPES or not file.is_file()):
            return self.send(404, "Dosya bulunamadı.".encode(), "text/plain; charset=utf-8")
        self.send(200, file.read_bytes(), ASSET_TYPES[file.suffix.lower()], cache="public, max-age=86400")

    def api_media(self):
        """?u=ad ile o kullanıcının, yoksa giriş yapmış kullanıcının kendi galerisini listeler."""
        user = session_user(self.token())
        name = self.query().get("u", [""])[0].strip()
        owner = find_user(name) if name else user
        if name and owner is None:
            return self.error(404, "Böyle bir kullanıcı yok.")
        root = owner and user_root(owner["id"])
        self.json(200, {
            "ok": True,
            "owner": owner and owner["username"],
            # Galeriyi yalnızca sahibi değiştirebilir.
            "mine": bool(user and owner and user["id"] == owner["id"]),
            "items": list_media(root) if owner else [],
            "folders": list_folders(root) if owner else [],
            "user": user and {"email": user["email"], "username": user["username"],
                              "can_upload": bool(user["can_upload"])},
            "max_upload": MAX_UPLOAD,
        })

    def serve_media(self, path):
        # /medya/kullanıcı no/dosya ya da /medya/kullanıcı no/klasör/dosya
        owner, _, rest = unquote(path[len("/medya/"):]).partition("/")
        folder, _, name = rest.rpartition("/")
        file = media_file(user_root(owner), name, folder) if owner.isascii() and owner.isdigit() else None
        if file is None:
            return self.send(404, "Dosya bulunamadı.".encode(), "text/plain; charset=utf-8")
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        # Videoda ileri/geri sarma için Range desteği.
        match = RANGE_RE.match(self.headers.get("Range", ""))
        if match and (match[1] or match[2]):
            if match[1]:
                start = int(match[1])
                if match[2]:
                    end = min(int(match[2]), size - 1)
            else:
                start = max(size - int(match[2]), 0)
            if start > end:
                return self.send(416, headers=[("Content-Range", f"bytes */{size}")])
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", MEDIA_TYPES[file.suffix.lower()])
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(file, "rb") as src:
                src.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = src.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass  # izleyici videoyu kapattı ya da başka yere sardı

    # -- POST --

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        routes = {
            "/api/login": self.api_login,
            "/api/register": self.api_register,
            "/api/logout": self.api_logout,
            "/api/yukle": self.api_upload,
            "/api/medya/sil": self.api_delete_media,
            "/api/medya/tasi": self.api_move_media,
            "/api/klasor": self.api_create_folder,
            "/api/klasor/sil": self.api_delete_folder,
        }
        route = routes.get(path)
        if route is None:
            return self.error(404, "Bulunamadı.")
        route()

    def api_login(self):
        ip = self.client_address[0]
        if rate_limited(ip):
            return self.error(429, "Çok fazla deneme. Lütfen daha sonra tekrar deneyin.")
        data = self.read_json()
        if data is None:
            return
        creds = self.credentials(data)
        if creds is None:
            return
        username, email, password = creds
        user = check_login(email, password)
        if user is not None and user["username_key"] is None:
            # Kullanıcı adı eklenmeden önce açılmış hesap: ilk girişte yazılan ad hesabın adı olur.
            try:
                claim_username(user["id"], username)
            except sqlite3.IntegrityError:
                return self.error(409, "Bu kullanıcı adı alınmış. Hesabınız için başka bir ad yazın.")
        elif user is None or user["username_key"] != username.casefold():
            record_attempt(ip)
            return self.error(401, "Kullanıcı adı, e-posta veya şifre hatalı.")
        else:
            username = user["username"]
        self.start_session(user["id"], user["email"], username, bool(data.get("remember")))

    def api_register(self):
        if not ALLOW_REGISTER:
            return self.error(403, "Kayıt kapalı.")
        ip = self.client_address[0]
        if rate_limited(ip):
            return self.error(429, "Çok fazla deneme. Lütfen daha sonra tekrar deneyin.")
        data = self.read_json()
        if data is None:
            return
        creds = self.credentials(data)
        if creds is None:
            return
        record_attempt(ip)
        username, email, password = creds
        try:
            user_id = create_user(email, username, password)
        except sqlite3.IntegrityError as error:
            return self.error(409, duplicate_message(error))
        self.start_session(user_id, email, username, False, status=201)

    def api_logout(self):
        delete_session(self.token())
        self.json(200, {"ok": True}, headers=[("Set-Cookie", self.cookie_header("", 0))])

    def uploader(self):
        """Yükleme izni olan kullanıcının kendi galeri dizinini döner; yoksa yanıtı gönderip None döner.

        Değiştiren bütün işlemler bu dizinle sınırlıdır; başkasının galerisine dokunulamaz."""
        user = session_user(self.token())
        if user is None:
            self.error(401, "Giriş yapılmamış.")
            return None
        if not user["can_upload"]:
            self.error(403, "Bu hesabın yükleme izni yok.")
            return None
        return user_root(user["id"])

    def api_upload(self):
        """Dosya, istek gövdesi olarak ham gönderilir; adı ?name=, klasörü ?klasor= ile verilir."""
        # Hata yanıtlarında gövde okunmadan bırakılabilir.
        self.close_connection = True
        root = self.uploader()
        if root is None:
            return
        root.mkdir(parents=True, exist_ok=True)
        query = self.query()
        directory = media_dir(root, query.get("klasor", [""])[0])
        if directory is None:
            return self.error(404, "Klasör bulunamadı.")
        name = upload_name(query.get("name", [""])[0], directory)
        if name is None:
            return self.error(415, "Desteklenmeyen dosya türü.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.error(411, "Content-Length gerekli.")
        if length <= 0:
            return self.error(400, "Dosya boş.")
        if length > MAX_UPLOAD:
            return self.error(413, f"Dosya çok büyük (en fazla {MAX_UPLOAD // 1024 // 1024} MB).")
        if shutil.disk_usage(root).free - length < MIN_FREE:
            return self.error(507, "Sunucuda yeterli boş yer yok.")
        # Nokta ile başlayan dosyalar galeride listelenmez; yükleme bitince asıl adına taşınır.
        tmp = root / f".yukleniyor-{secrets.token_hex(8)}"
        try:
            with open(tmp, "wb") as out:
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        break
                    out.write(chunk)
                    left -= len(chunk)
            if left:
                return  # bağlantı yarıda koptu
            os.replace(tmp, directory / name)
        except OSError:
            return self.error(500, "Dosya kaydedilemedi.")
        finally:
            tmp.unlink(missing_ok=True)
        self.json(201, {"ok": True, "file": name})

    def api_delete_media(self):
        root = self.uploader()
        if root is None:
            return
        data = self.read_json()
        if data is None:
            return
        file = media_file(root, data.get("file"), data.get("folder", ""))
        if file is None:
            return self.error(404, "Dosya bulunamadı.")
        file.unlink()
        self.json(200, {"ok": True})

    def api_move_media(self):
        root = self.uploader()
        if root is None:
            return
        data = self.read_json()
        if data is None:
            return
        file = media_file(root, data.get("file"), data.get("folder", ""))
        if file is None:
            return self.error(404, "Dosya bulunamadı.")
        directory = media_dir(root, data.get("to"))
        if directory is None:
            return self.error(404, "Klasör bulunamadı.")
        if directory != file.parent:
            os.rename(file, directory / upload_name(file.name, directory))
        self.json(200, {"ok": True})

    def api_create_folder(self):
        root = self.uploader()
        if root is None:
            return
        data = self.read_json()
        if data is None:
            return
        name = data.get("name")
        name = folder_name(name) if isinstance(name, str) else ""
        if not name:
            return self.error(400, "Geçerli bir klasör adı girin.")
        root.mkdir(parents=True, exist_ok=True)
        try:
            (root / name).mkdir()
        except FileExistsError:
            return self.error(409, "Bu adda bir klasör zaten var.")
        self.json(201, {"ok": True, "folder": name})

    def api_delete_folder(self):
        """Yalnızca boş klasörler silinir."""
        root = self.uploader()
        if root is None:
            return
        data = self.read_json()
        if data is None:
            return
        directory = media_dir(root, data.get("name"))
        if directory is None or directory == root:
            return self.error(404, "Klasör bulunamadı.")
        try:
            directory.rmdir()
        except OSError:
            return self.error(409, "Klasör boş değil. Önce içindekileri silin ya da taşıyın.")
        self.json(200, {"ok": True})


# ---------- komut satırı ----------

def cli(args):
    init_db()
    if args[0] == "users":
        with db() as conn:
            for row in conn.execute("SELECT email, username, can_upload FROM users ORDER BY email"):
                print(f'{row["email"]}  {row["username"] or "(kullanıcı adı yok)"}'
                      + ("" if row["can_upload"] else "  (yükleme izni yok)"))
        return 0
    if len(args) != 2 or args[0] not in ("adduser", "deluser", "izinver", "izinal"):
        print(__doc__)
        return 2
    email = args[1].strip().lower()
    if args[0] in ("izinver", "izinal"):
        with db() as conn:
            changed = conn.execute("UPDATE users SET can_upload = ? WHERE email = ?",
                                   (int(args[0] == "izinver"), email)).rowcount
        print("Güncellendi." if changed else "Kullanıcı bulunamadı.")
        return 0 if changed else 1
    if args[0] == "deluser":
        with db() as conn:
            deleted = conn.execute("DELETE FROM users WHERE email = ?", (email,)).rowcount
        print("Silindi." if deleted else "Kullanıcı bulunamadı.")
        return 0 if deleted else 1
    if not EMAIL_RE.match(email):
        print("Geçerli bir e-posta girin.")
        return 1
    username = input("Kullanıcı adı: ").strip()
    if not USERNAME_RE.match(username):
        print("Kullanıcı adı 3-20 karakter olmalı; harf, rakam ve _ . - içerebilir.")
        return 1
    password = getpass.getpass("Şifre: ")
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        print(f"Şifre en az {MIN_PASSWORD} karakter olmalı.")
        return 1
    try:
        create_user(email, username, password)
    except sqlite3.IntegrityError as error:
        print(duplicate_message(error))
        return 1
    print("Kullanıcı eklendi.")
    return 0


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"http://{HOST}:{PORT} dinleniyor", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
