const REPO = "0xBadSign/myndos-reels";

async function dispatch(env, workflow, command) {
  const r = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "myndos-reels-cron",
    },
    body: JSON.stringify({ ref: "main", inputs: { command } }),
  });
  console.log(workflow, command, r.status, r.status === 204 ? "" : await r.text());
}

// ---- TikTok OAuth for "Myndos Publisher": one developer app, one connected account per product ----
const TT_BASE = "https://myndos.studio/publisher/tiktok";
const TT_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/";
const ACCOUNT_RE = /^[a-z0-9-]{2,20}$/;

const html = (body, status = 200) =>
  new Response(`<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><body style="font:18px system-ui;max-width:30em;margin:3em auto;padding:0 1em">${body}`, {
    status,
    headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
  });
const errPage = (msg, status = 400) => html(`<p>Hata: ${msg}</p>`, status);

// Constant-time string compare (hash both sides so lengths do not leak).
async function safeEqual(a, b) {
  const enc = new TextEncoder();
  const [x, y] = await Promise.all([crypto.subtle.digest("SHA-256", enc.encode(a)), crypto.subtle.digest("SHA-256", enc.encode(b))]);
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let d = 0;
  for (let i = 0; i < u.length; i++) d |= u[i] ^ v[i];
  return d === 0;
}

// POST the token endpoint (form-encoded) and turn the answer into the record we keep in KV.
async function tokenRequest(env, params) {
  const r = await fetch(TT_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache" },
    body: new URLSearchParams({ client_key: env.TIKTOK_CLIENT_KEY, client_secret: env.TIKTOK_CLIENT_SECRET, ...params }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.access_token) {
    console.log("tiktok token error", r.status, j.error, j.error_description); // never the tokens
    return null;
  }
  const now = Math.floor(Date.now() / 1000);
  return {
    access_token: j.access_token,
    refresh_token: j.refresh_token,
    expires_at: now + (j.expires_in || 7200),
    refresh_expires_at: now + (j.refresh_expires_in || 180 * 86400),
    open_id: j.open_id,
    scope: j.scope,
  };
}

async function tiktok(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "");
  if (!env.TOKENS || !env.TIKTOK_CLIENT_KEY || !env.TIKTOK_CLIENT_SECRET) return errPage("Sunucu ayarları eksik.", 500);

  if (path === "/publisher/tiktok/login" && request.method === "GET") {
    const account = url.searchParams.get("account") || "";
    if (!ACCOUNT_RE.test(account)) return errPage("Geçersiz hesap adı.");
    const state = crypto.randomUUID().replaceAll("-", "");
    await env.TOKENS.put(`state:${state}`, account, { expirationTtl: 600 });
    const auth = new URL("https://www.tiktok.com/v2/auth/authorize/");
    auth.search = new URLSearchParams({
      client_key: env.TIKTOK_CLIENT_KEY,
      scope: "user.info.basic,video.upload,video.publish",
      response_type: "code",
      redirect_uri: `${TT_BASE}/callback`,
      state,
    }).toString();
    return Response.redirect(auth.toString(), 302);
  }

  if (path === "/publisher/tiktok/callback" && request.method === "GET") {
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state") || "";
    if (url.searchParams.get("error")) return errPage("TikTok izni reddedildi veya iptal edildi.");
    if (!code || !/^[a-f0-9]{32}$/.test(state)) return errPage("Eksik veya geçersiz istek.");
    const account = await env.TOKENS.get(`state:${state}`);
    if (!account) return errPage("Bağlantı süresi doldu, girişi baştan başlat.");
    await env.TOKENS.delete(`state:${state}`);
    const rec = await tokenRequest(env, { code, grant_type: "authorization_code", redirect_uri: `${TT_BASE}/callback` });
    if (!rec) return errPage("TikTok kodu jetona çeviremedi.", 502);
    // The login link is public, so once an account key is connected only the same TikTok account may reconnect it.
    const prev = JSON.parse((await env.TOKENS.get(`tiktok:${account}`)) || "null");
    if (prev && prev.open_id && prev.open_id !== rec.open_id) return errPage("Bu hesap anahtarı başka bir TikTok hesabına bağlı.", 403);
    await env.TOKENS.put(`tiktok:${account}`, JSON.stringify(rec));
    return html(`<p>TikTok hesabı bağlandı (${account}). Bu sekmeyi kapatabilirsin.</p>`);
  }

  if (path === "/publisher/tiktok/token" && request.method === "POST") {
    const bearer = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
    if (!env.PUBLISHER_KEY || !(await safeEqual(bearer, env.PUBLISHER_KEY))) return Response.json({ error: "unauthorized" }, { status: 401 });
    const { account } = await request.json().catch(() => ({}));
    if (!ACCOUNT_RE.test(account || "")) return Response.json({ error: "bad account" }, { status: 400 });
    const key = `tiktok:${account}`;
    const rec = await env.TOKENS.get(key, "json");
    if (!rec) return Response.json({ error: "account not connected" }, { status: 404 });
    const now = Math.floor(Date.now() / 1000);
    if (rec.expires_at - now < 600) {
      const fresh = await tokenRequest(env, { grant_type: "refresh_token", refresh_token: rec.refresh_token });
      if (!fresh) return Response.json({ error: "refresh failed, reconnect the account" }, { status: 502 });
      Object.assign(rec, fresh, { open_id: fresh.open_id || rec.open_id, scope: fresh.scope || rec.scope });
      await env.TOKENS.put(key, JSON.stringify(rec)); // the refresh token may rotate
    }
    return Response.json({ access_token: rec.access_token });
  }

  return new Response("Not found", { status: 404 });
}

export default {
  async fetch(request, env) {
    try {
      return await tiktok(request, env);
    } catch (e) {
      console.log("tiktok handler error", String(e));
      return errPage("Beklenmeyen hata.", 500);
    }
  },
  async scheduled(event, env) {
    if (!env.GH_TOKEN) return console.log("GH_TOKEN is not set");
    if (event.cron === "11 16 * * *") {
      await dispatch(env, "post.yml", "daily");
      await dispatch(env, "tiktok.yml", "daily");
    }
    else await dispatch(env, "autodm.yml", "run");
  },
};
