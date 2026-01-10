import os
import hmac
import hashlib
import base64
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Header, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

# optional: python-dotenv が入っているならローカルで勝手に .env を読める
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# optional: Postgres (asyncpg)
try:
    import asyncpg
except Exception:
    asyncpg = None  # type: ignore


app = FastAPI()

LINE_PROFILE_API = "https://api.line.me/v2/bot/profile/{}"

# =========================================================
# メモリ保存（DBが無いときのフォールバック）
# =========================================================
SEEN_USERS: List[Dict[str, Any]] = []

# =========================================================
# Admin 認証（HTTP Basic）
# =========================================================
security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    """
    /admin 配下を守る簡易認証。
    環境変数:
      ADMIN_USERNAME
      ADMIN_PASSWORD
    """
    admin_user = os.getenv("ADMIN_USERNAME", "")
    admin_pass = os.getenv("ADMIN_PASSWORD", "")
    if not admin_user or not admin_pass:
        # 認証情報が未設定なら危険なので、明示的に拒否
        raise HTTPException(
            status_code=500,
            detail="ADMIN_USERNAME / ADMIN_PASSWORD are not set",
        )

    is_user_ok = secrets.compare_digest(credentials.username, admin_user)
    is_pass_ok = secrets.compare_digest(credentials.password, admin_pass)

    if not (is_user_ok and is_pass_ok):
        # Basic認証のダイアログを出すためにWWW-Authenticateを付ける
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


# =========================================================
# LINE署名検証
# =========================================================
def verify_line_signature(body: bytes, x_line_signature: Optional[str]) -> None:
    """
    LINEの署名検証。
    LINE_CHANNEL_SECRET が未設定ならローカル動作のため検証をスキップ。
    """
    secret = os.getenv("LINE_CHANNEL_SECRET", "")
    if not secret:
        return

    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing x-line-signature")

    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")

    if not hmac.compare_digest(expected, x_line_signature):
        raise HTTPException(status_code=400, detail="Invalid signature")


# =========================================================
# LINEプロフィール取得
# =========================================================
async def fetch_line_profile(user_id: str) -> Dict[str, Any]:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return {}

    headers = {"Authorization": f"Bearer {token}"}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                LINE_PROFILE_API.format(user_id),
                headers=headers,
                timeout=7,
            )
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}


# =========================================================
# DB (Railway Postgres / asyncpg)
# =========================================================
def _db_url() -> str:
    return os.getenv("DATABASE_URL", "")


def _db_enabled() -> bool:
    return bool(_db_url()) and (asyncpg is not None)


async def _init_db(pool: "asyncpg.Pool") -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS seen_users (
        user_id        TEXT PRIMARY KEY,
        user_name      TEXT,
        picture_url    TEXT,
        status_message TEXT,
        last_event     TEXT,
        last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    """
    async with pool.acquire() as conn:
        await conn.execute(sql)


@app.on_event("startup")
async def on_startup() -> None:
    """
    DATABASE_URL がある場合だけDBを有効化。
    ない場合はメモリ保存で動作（ローカルUI確認用）。
    """
    if not _db_enabled():
        app.state.db_pool = None
        return

    pool = await asyncpg.create_pool(dsn=_db_url(), min_size=1, max_size=5)  # type: ignore
    await _init_db(pool)  # type: ignore
    app.state.db_pool = pool


@app.on_event("shutdown")
async def on_shutdown() -> None:
    pool = getattr(app.state, "db_pool", None)
    if pool is not None:
        await pool.close()


def _pool_or_none():
    return getattr(app.state, "db_pool", None)


# =========================================================
# 永続化レイヤ（DBがあればDB / なければメモリ）
# =========================================================
async def _upsert_user_db(user_id: str, event_type: str) -> None:
    pool = _pool_or_none()
    if pool is None:
        raise RuntimeError("DB pool is None")

    # 初回のみプロフィールを引く（API負荷軽減）
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM seen_users WHERE user_id=$1",
            user_id,
        )

    profile: Dict[str, Any] = {}
    if not exists:
        profile = await fetch_line_profile(user_id)

    now = datetime.now(timezone.utc)

    sql = """
    INSERT INTO seen_users (user_id, user_name, picture_url, status_message, last_event, last_seen_at)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (user_id) DO UPDATE SET
        last_event   = EXCLUDED.last_event,
        last_seen_at = EXCLUDED.last_seen_at
    ;
    """

    async with pool.acquire() as conn:
        await conn.execute(
            sql,
            user_id,
            profile.get("displayName") if profile else None,
            profile.get("pictureUrl") if profile else None,
            profile.get("statusMessage") if profile else None,
            event_type,
            now,
        )


async def _upsert_user_mem(user_id: str, event_type: str) -> None:
    for u in SEEN_USERS:
        if u["userId"] == user_id:
            u["event"] = event_type
            u["time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return

    profile = await fetch_line_profile(user_id)
    SEEN_USERS.append(
        {
            "userId": user_id,
            "userName": profile.get("displayName", "unknown"),
            "pictureUrl": profile.get("pictureUrl"),
            "statusMessage": profile.get("statusMessage"),
            "event": event_type,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )


async def record_user(user_id: str, event_type: str) -> None:
    if _pool_or_none() is not None:
        await _upsert_user_db(user_id, event_type)
    else:
        await _upsert_user_mem(user_id, event_type)


async def _list_users_db(limit: int = 300) -> List[Dict[str, Any]]:
    pool = _pool_or_none()
    if pool is None:
        raise RuntimeError("DB pool is None")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT user_id, user_name, picture_url, status_message, last_event, last_seen_at
            FROM seen_users
            ORDER BY last_seen_at DESC
            LIMIT $1
            """,
            limit,
        )

    out: List[Dict[str, Any]] = []
    for r in rows:
        t = r["last_seen_at"]
        time_str = t.strftime("%Y-%m-%d %H:%M:%S") if t else ""
        out.append(
            {
                "userId": r["user_id"],
                "userName": r["user_name"] or "",
                "pictureUrl": r["picture_url"],
                "statusMessage": r["status_message"],
                "event": r["last_event"] or "",
                "time": time_str,
            }
        )
    return out


async def _list_users_mem() -> List[Dict[str, Any]]:
    # time desc
    def key(u: Dict[str, Any]) -> str:
        return u.get("time", "")

    return list(sorted(SEEN_USERS, key=key, reverse=True))


async def list_users(limit: int = 300) -> List[Dict[str, Any]]:
    if _pool_or_none() is not None:
        return await _list_users_db(limit=limit)
    return await _list_users_mem()


# =========================================================
# UI helpers
# =========================================================
def _chip(text: str, kind: str = "neutral") -> str:
    # kind: neutral / ok / warn / danger / info
    cls = f"chip chip-{kind}"
    return f"<span class='{cls}'>{text}</span>"


def _layout(title: str, body_html: str, top_right: str = "") -> str:
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    :root{{
      --bg: #0b1020;
      --card: rgba(255,255,255,0.06);
      --card2: rgba(255,255,255,0.08);
      --text: rgba(255,255,255,0.92);
      --muted: rgba(255,255,255,0.70);
      --line: rgba(255,255,255,0.12);
      --accent: #5eead4;
      --accent2:#60a5fa;
      --danger:#fb7185;
      --shadow: 0 18px 60px rgba(0,0,0,0.45);
      --radius: 18px;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Hiragino Sans", "Noto Sans JP", "Helvetica Neue", Arial, "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol", sans-serif;
    }}
    *{{box-sizing:border-box}}
    body{{
      margin:0;
      min-height:100vh;
      font-family:var(--sans);
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 20% 10%, rgba(96,165,250,0.20), transparent 55%),
        radial-gradient(1000px 550px at 80% 0%, rgba(94,234,212,0.16), transparent 55%),
        radial-gradient(1100px 650px at 60% 90%, rgba(251,113,133,0.12), transparent 55%),
        linear-gradient(180deg, #070a14, #0b1020);
    }}
    a{{color:inherit}}
    .wrap{{max-width:1100px; margin:0 auto; padding:28px 18px 46px}}
    .topbar{{display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:18px}}
    .brand{{display:flex; align-items:center; gap:12px}}
    .logo{{
      width:42px; height:42px; border-radius:14px;
      background: linear-gradient(135deg, rgba(94,234,212,0.95), rgba(96,165,250,0.95));
      box-shadow: var(--shadow);
      display:grid; place-items:center; color:#06101a; font-weight:800;
    }}
    .brand h1{{font-size:16px; margin:0; letter-spacing:0.2px}}
    .brand p{{margin:2px 0 0; color:var(--muted); font-size:12px}}
    .card{{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow:hidden;
    }}
    .card-h{{padding:18px 18px 12px; border-bottom:1px solid var(--line); background:linear-gradient(180deg, rgba(255,255,255,0.06), transparent)}}
    .card-b{{padding:18px}}
    .grid{{display:grid; grid-template-columns: 1fr; gap:14px}}
    @media (min-width: 900px){{ .grid{{grid-template-columns: 1.2fr 0.8fr}} }}
    .pill{{
      display:inline-flex; align-items:center; gap:8px;
      padding:8px 12px;
      border-radius:999px;
      background: rgba(255,255,255,0.06);
      border:1px solid var(--line);
      color: var(--muted);
      font-size:12px;
      white-space:nowrap;
    }}
    .btn{{
      display:inline-flex; align-items:center; gap:8px;
      padding:10px 12px;
      border-radius:12px;
      border:1px solid var(--line);
      background: rgba(255,255,255,0.06);
      color: var(--text);
      text-decoration:none;
      font-size:13px;
      cursor:pointer;
    }}
    .btn:hover{{background: rgba(255,255,255,0.10)}}
    .btn-primary{{border-color: rgba(94,234,212,0.35); background: rgba(94,234,212,0.10)}}
    .btn-danger{{border-color: rgba(251,113,133,0.35); background: rgba(251,113,133,0.10)}}
    .mono{{font-family:var(--mono)}}
    .muted{{color:var(--muted)}}
    .chips{{display:flex; flex-wrap:wrap; gap:8px}}
    .chip{{display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; border:1px solid var(--line); background: rgba(255,255,255,0.06); font-size:12px; color:var(--muted)}}
    .chip-ok{{border-color: rgba(94,234,212,0.35); color: rgba(94,234,212,0.95); background: rgba(94,234,212,0.08)}}
    .chip-info{{border-color: rgba(96,165,250,0.35); color: rgba(147,197,253,0.95); background: rgba(96,165,250,0.08)}}
    .chip-warn{{border-color: rgba(251,191,36,0.35); color: rgba(251,191,36,0.95); background: rgba(251,191,36,0.08)}}
    .chip-danger{{border-color: rgba(251,113,133,0.35); color: rgba(251,113,133,0.95); background: rgba(251,113,133,0.08)}}

    .tablewrap{{overflow:auto}}
    table{{width:100%; border-collapse:separate; border-spacing:0; min-width:860px}}
    thead th{{
      text-align:left; font-size:12px; color:var(--muted); font-weight:600;
      padding:12px 14px;
      position:sticky; top:0; background: rgba(11,16,32,0.92);
      backdrop-filter: blur(8px);
      border-bottom:1px solid var(--line);
    }}
    tbody td{{padding:12px 14px; border-bottom:1px solid rgba(255,255,255,0.06); vertical-align:middle; font-size:13px}}
    tbody tr:hover{{background: rgba(255,255,255,0.04)}}
    .avatar{{width:34px; height:34px; border-radius:50%; object-fit:cover; border:1px solid rgba(255,255,255,0.15)}}
    .name{{display:flex; flex-direction:column; gap:2px}}
    .name strong{{font-size:13px}}
    .name span{{font-size:12px; color:var(--muted)}}
    .row-actions{{display:flex; gap:8px; justify-content:flex-end}}
    .kpi{{display:grid; grid-template-columns: repeat(2, 1fr); gap:12px}}
    @media (min-width: 600px){{ .kpi{{grid-template-columns: repeat(4, 1fr)}} }}
    .kpi .k{{background: rgba(255,255,255,0.04); border:1px solid var(--line); border-radius:14px; padding:12px}}
    .k .v{{font-size:18px; font-weight:800}}
    .k .l{{font-size:12px; color:var(--muted); margin-top:4px}}
    .controls{{display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:space-between}}
    .search{{
      flex:1;
      min-width:220px;
      display:flex; align-items:center; gap:8px;
      padding:10px 12px;
      border-radius:14px;
      border:1px solid var(--line);
      background: rgba(255,255,255,0.05);
    }}
    .search input{{
      border:none; outline:none; background:transparent; color:var(--text);
      width:100%;
      font-size:13px;
    }}
    .footer{{margin-top:16px; color:var(--muted); font-size:12px}}
    code{{font-family:var(--mono); color:rgba(94,234,212,0.95)}}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div class="brand">
        <div class="logo">LT</div>
        <div>
          <h1>{title}</h1>
          <p>LINE Task Server / Admin Dashboard</p>
        </div>
      </div>
      <div style="display:flex; gap:10px; align-items:center;">
        {top_right}
      </div>
    </div>

    {body_html}

    <div class="footer">
      Webhook: <code>/line/webhook</code> ・ Health: <code>/health</code>
    </div>
  </div>

  <script>
    function copyText(text) {{
      navigator.clipboard.writeText(text).then(() => {{
        toast("Copied");
      }});
    }}
    function toast(msg) {{
      let el = document.getElementById("toast");
      if (!el) {{
        el = document.createElement("div");
        el.id="toast";
        el.style.position="fixed";
        el.style.left="50%";
        el.style.bottom="20px";
        el.style.transform="translateX(-50%)";
        el.style.padding="10px 14px";
        el.style.border="1px solid rgba(255,255,255,0.15)";
        el.style.background="rgba(11,16,32,0.92)";
        el.style.backdropFilter="blur(10px)";
        el.style.borderRadius="12px";
        el.style.color="rgba(255,255,255,0.92)";
        el.style.fontSize="13px";
        el.style.zIndex="9999";
        document.body.appendChild(el);
      }}
      el.textContent = msg;
      el.style.opacity="1";
      clearTimeout(window.__toastTimer);
      window.__toastTimer = setTimeout(()=>{{ el.style.opacity="0"; }}, 900);
    }}

    function setupSearch() {{
      const input = document.getElementById("q");
      const rows = Array.from(document.querySelectorAll("tbody tr[data-row='1']"));
      if (!input) return;

      input.addEventListener("input", () => {{
        const q = input.value.toLowerCase().trim();
        let shown = 0;
        for (const r of rows) {{
          const hay = (r.getAttribute("data-hay") || "").toLowerCase();
          const ok = !q || hay.includes(q);
          r.style.display = ok ? "" : "none";
          if (ok) shown++;
        }}
        const c = document.getElementById("shownCount");
        if (c) c.textContent = shown.toString();
      }});
    }}

    window.addEventListener("DOMContentLoaded", setupSearch);
  </script>
</body>
</html>
"""


def _mode_chip() -> str:
    if _pool_or_none() is not None:
        return _chip("DB: Postgres", "ok")
    if asyncpg is None and _db_url():
        return _chip("DB: asyncpg missing", "danger")
    return _chip("DB: Memory (no DATABASE_URL)", "warn")


# =========================================================
# 画面
# =========================================================
@app.get("/", response_class=HTMLResponse)
def home():
    right = f"""
      <span class="pill">{_mode_chip()}</span>
      <a class="btn btn-primary" href="/admin/users">Open Admin</a>
      <a class="btn" href="/health">Health</a>
    """

    body = f"""
    <div class="grid">
      <div class="card">
        <div class="card-h">
          <div class="chips">
            {_chip("Running", "ok")}
            {_chip("FastAPI", "info")}
            {_mode_chip()}
          </div>
        </div>
        <div class="card-b">
          <h2 style="margin:0 0 10px; font-size:18px;">✅ LINE Task Server is running</h2>
          <p class="muted" style="margin:0 0 14px; line-height:1.6;">
            Webhookを受けて <span class="mono">userId</span> / <span class="mono">displayName</span> を保存します。
            管理画面で一覧表示・検索・コピーができます。
          </p>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <a class="btn btn-primary" href="/admin/users">Admin Users</a>
            <a class="btn" href="/line/webhook">Webhook GET</a>
            <a class="btn" href="/docs">OpenAPI Docs</a>
          </div>
          <div style="margin-top:16px;" class="pill">
            Webhook URL: <span class="mono">/line/webhook</span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-h">
          <strong style="font-size:13px;">Setup checklist</strong>
        </div>
        <div class="card-b">
          <ul style="margin:0; padding-left:18px; color:var(--muted); line-height:1.8;">
            <li>Railway Variables: <span class="mono">LINE_CHANNEL_SECRET</span>, <span class="mono">LINE_CHANNEL_ACCESS_TOKEN</span></li>
            <li>Railway Variables: <span class="mono">ADMIN_USERNAME</span>, <span class="mono">ADMIN_PASSWORD</span></li>
            <li>Railway Postgres: <span class="mono">DATABASE_URL</span>（永続化）</li>
            <li>Start command: <span class="mono">uvicorn app.main:app --host 0.0.0.0 --port $PORT</span></li>
          </ul>
        </div>
      </div>
    </div>
    """
    return _layout("LINE Task Server", body, top_right=right)


@app.get("/health")
def health():
    return {"status": "ok", "db": "postgres" if _pool_or_none() is not None else "memory"}


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(_: None = Depends(require_admin)):
    users = await list_users(limit=300)

    total = len(users)
    uniq = len({u.get("userId") for u in users if u.get("userId")})
    last_time = users[0].get("time") if users else ""
    mode = "postgres" if _pool_or_none() is not None else "memory"

    # event stats (rough)
    ev_count: Dict[str, int] = {}
    for u in users:
        ev = (u.get("event") or "unknown").strip() or "unknown"
        ev_count[ev] = ev_count.get(ev, 0) + 1
    top_events = sorted(ev_count.items(), key=lambda x: x[1], reverse=True)[:4]

    def safe(s: Any) -> str:
        return (str(s) if s is not None else "").replace("<", "&lt;").replace(">", "&gt;")

    rows = ""
    for u in users:
        uid = safe(u.get("userId", ""))
        name = safe(u.get("userName", ""))
        event = safe(u.get("event", ""))
        time_ = safe(u.get("time", ""))
        pic = u.get("pictureUrl")

        pic_html = f"<img class='avatar' src='{safe(pic)}'/>" if pic else "<div class='avatar' style='display:grid;place-items:center;background:rgba(255,255,255,0.06);'>?</div>"

        hay = f"{uid} {name} {event} {time_}".strip()
        rows += f"""
          <tr data-row="1" data-hay="{safe(hay)}">
            <td class="muted">{time_}</td>
            <td>{_chip(event or "unknown", "info")}</td>
            <td>{pic_html}</td>
            <td>
              <div class="name">
                <strong>{name or "unknown"}</strong>
                <span class="mono">{safe(u.get("statusMessage") or "")}</span>
              </div>
            </td>
            <td class="mono">{uid}</td>
            <td>
              <div class="row-actions">
                <button class="btn" onclick="copyText('{uid}')">Copy userId</button>
              </div>
            </td>
          </tr>
        """

    event_chips = ""
    for ev, cnt in top_events:
        event_chips += _chip(f"{ev}: {cnt}", "neutral")

    right = f"""
      <span class="pill">{_mode_chip()}</span>
      <a class="btn" href="/">Home</a>
      <a class="btn" href="/health">Health</a>
    """

    body = f"""
    <div class="card" style="margin-bottom:14px;">
      <div class="card-b">
        <div class="kpi">
          <div class="k"><div class="v">{total}</div><div class="l">rows loaded</div></div>
          <div class="k"><div class="v">{uniq}</div><div class="l">unique users</div></div>
          <div class="k"><div class="v"><span id="shownCount">{total}</span></div><div class="l">shown (filter)</div></div>
          <div class="k"><div class="v">{safe(last_time) if last_time else "-"}</div><div class="l">last seen</div></div>
        </div>

        <div style="margin-top:12px;" class="chips">
          {_chip(f"Mode: {mode}", "ok" if mode == "postgres" else "warn")}
          {event_chips}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="controls">
          <div class="search">
            <span class="muted">🔎</span>
            <input id="q" placeholder="Search userId / name / event..." />
          </div>
          <div style="display:flex; gap:10px; flex-wrap:wrap;">
            <a class="btn" href="/admin/users">Reload</a>
          </div>
        </div>
      </div>

      <div class="card-b tablewrap">
        <table>
          <thead>
            <tr>
              <th style="width:160px;">time</th>
              <th style="width:140px;">event</th>
              <th style="width:70px;">icon</th>
              <th>name / status</th>
              <th style="width:360px;">userId</th>
              <th style="width:150px; text-align:right;">actions</th>
            </tr>
          </thead>
          <tbody>
            {rows if rows else "<tr><td colspan='6' class='muted'>まだ0件（Webhookが来ると増えます）</td></tr>"}
          </tbody>
        </table>
      </div>
    </div>
    """
    return _layout("Admin / Users", body, top_right=right)


# =========================================================
# Webhook
# =========================================================
@app.post("/line/webhook")
async def line_webhook(
    request: Request,
    x_line_signature: Optional[str] = Header(default=None),
):
    body = await request.body()
    verify_line_signature(body, x_line_signature)

    data = await request.json()
    print("LINE webhook received:", data)

    events = data.get("events", [])
    for ev in events:
        event_type = ev.get("type") or "unknown"
        src = ev.get("source", {})
        user_id = src.get("userId")
        if user_id:
            await record_user(user_id, event_type)

    return JSONResponse({"ok": True, "received": len(events)})


@app.get("/line/webhook")
def line_webhook_get():
    return {"ok": True}
