"""
Glow Up – FastAPI Backend
Run: uvicorn main:app --reload
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, Literal
import sqlite3, uuid, os, shutil, hashlib
from datetime import datetime

app = FastAPI(title="Glow Up API ✨", version="2.0.0")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

DB_PATH = "glowup.db"

CATEGORIES = Literal["fit", "makeup", "food", "nails", "style", "other"]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            caption TEXT DEFAULT '',
            category TEXT DEFAULT 'other',
            image_url TEXT NOT NULL,
            share_token TEXT UNIQUE NOT NULL,
            pin_hash TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            rater_name TEXT DEFAULT 'Anonymous',
            score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
            created_at TEXT NOT NULL,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            emoji TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
        );
    """)
    conn.commit()
    conn.close()

init_db()

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode()).hexdigest()

def post_stats(post_id, conn):
    stats = conn.execute(
        "SELECT AVG(score) as avg, COUNT(*) as total FROM ratings WHERE post_id=?", (post_id,)
    ).fetchone()
    reactions = conn.execute(
        "SELECT emoji, COUNT(*) as count FROM reactions WHERE post_id=? GROUP BY emoji", (post_id,)
    ).fetchall()
    return {
        "avg_rating": round(stats["avg"], 1) if stats["avg"] else None,
        "total_ratings": stats["total"],
        "reactions": {r["emoji"]: r["count"] for r in reactions},
    }

def serialize_post(row, base_url):
    conn = get_db()
    stats = post_stats(row["id"], conn)
    conn.close()
    return {
        "id": row["id"],
        "username": row["username"],
        "caption": row["caption"],
        "category": row["category"],
        "image_url": f"{base_url}/uploads/{row['image_url']}",
        "share_token": row["share_token"],
        "created_at": row["created_at"],
        **stats,
    }


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"message": "Glow Up API ✨ v2"}

@app.post("/fits")
async def create_post(
    username: str = Form(...),
    caption: str = Form(""),
    category: str = Form("other"),
    pin: Optional[str] = Form(None),
    image: UploadFile = File(...),
    request_base: str = "http://localhost:8000",
):
    if not image.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image")

    post_id = str(uuid.uuid4())
    share_token = str(uuid.uuid4())[:8]
    ext = image.filename.rsplit(".", 1)[-1] if "." in image.filename else "jpg"
    filename = f"{post_id}.{ext}"

    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        shutil.copyfileobj(image.file, f)

    pin_hash = hash_pin(pin) if pin else None

    conn = get_db()
    conn.execute(
        "INSERT INTO posts VALUES (?,?,?,?,?,?,?,?)",
        (post_id, username, caption, category, filename, share_token, pin_hash, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()

    return {"id": post_id, "share_token": share_token, "share_url": f"/rate/{share_token}"}

@app.get("/fits")
def get_posts(request_base: str = "http://localhost:8000"):
    conn = get_db()
    rows = conn.execute("SELECT * FROM posts ORDER BY created_at DESC").fetchall()
    conn.close()
    return [serialize_post(r, request_base) for r in rows]

@app.get("/fits/{token}")
def get_post(token: str, request_base: str = "http://localhost:8000"):
    conn = get_db()
    row = conn.execute("SELECT * FROM posts WHERE share_token=? OR id=?", (token, token)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Post not found")
    return serialize_post(row, request_base)

@app.post("/fits/{token}/rate")
def rate_post(token: str, score: int, rater_name: str = "Anonymous"):
    if not 1 <= score <= 10:
        raise HTTPException(400, "Score must be 1-10")
    conn = get_db()
    post = conn.execute("SELECT id FROM posts WHERE share_token=? OR id=?", (token, token)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post not found")
    conn.execute(
        "INSERT INTO ratings (post_id, rater_name, score, created_at) VALUES (?,?,?,?)",
        (post["id"], rater_name, score, datetime.utcnow().isoformat()),
    )
    conn.commit()
    stats = post_stats(post["id"], conn)
    conn.close()
    return {"message": "Rated! ✨", **stats}

@app.post("/fits/{token}/react")
def react_post(token: str, emoji: str):
    conn = get_db()
    post = conn.execute("SELECT id FROM posts WHERE share_token=? OR id=?", (token, token)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post not found")
    conn.execute(
        "INSERT INTO reactions (post_id, emoji, created_at) VALUES (?,?,?)",
        (post["id"], emoji, datetime.utcnow().isoformat()),
    )
    conn.commit()
    stats = post_stats(post["id"], conn)
    conn.close()
    return {"message": "Reacted!", "reactions": stats["reactions"]}

@app.delete("/fits/{token}")
def delete_post(token: str, pin: Optional[str] = None):
    conn = get_db()
    post = conn.execute("SELECT id, image_url, pin_hash FROM posts WHERE share_token=? OR id=?", (token, token)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "Post not found")

    # Check PIN
    if post["pin_hash"]:
        if not pin or hash_pin(pin) != post["pin_hash"]:
            conn.close()
            raise HTTPException(403, "Invalid PIN")

    # Delete image file
    img_path = os.path.join(UPLOAD_DIR, post["image_url"])
    if os.path.exists(img_path):
        os.remove(img_path)

    conn.execute("DELETE FROM posts WHERE id=?", (post["id"],))
    conn.commit()
    conn.close()
    return {"message": "Post deleted ✨"}
