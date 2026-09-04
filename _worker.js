/**
 * Algohns V12 — redirect Worker.
 *
 * The V12 platform is a Python / Streamlit application and cannot run inside a
 * Cloudflare Worker (Workers execute JS/WASM in a V8 isolate; Streamlit needs a
 * long-lived Python server). This Worker keeps the `algohns.workers.dev` domain
 * and forwards every request to the live Streamlit app configured in `APP_URL`
 * (see wrangler.toml [vars], or set it as a secret with `wrangler secret put`).
 *
 * When APP_URL is not set yet it serves a branded "coming online" landing page.
 * The legacy V11 application is preserved at legacy/_worker_v11.js.
 */
export default {
  async fetch(request, env) {
    const target = (env && env.APP_URL) ? String(env.APP_URL).trim() : "";

    // Lightweight health endpoint that never redirects.
    const url = new URL(request.url);
    if (url.pathname === "/__redirect_health") {
      return new Response(JSON.stringify({ ok: true, target: target || null }), {
        headers: { "content-type": "application/json; charset=utf-8" },
      });
    }

    if (!target) {
      return new Response(landingHTML(), {
        status: 200,
        headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" },
      });
    }

    // Preserve the query string; send everything to the app root (Streamlit is
    // a single-page app and does not use path-based routing for deep links).
    let location;
    try {
      const dest = new URL(target);
      location = dest.origin + (dest.pathname && dest.pathname !== "/" ? dest.pathname : "/") + url.search;
    } catch (_e) {
      location = target;
    }
    return Response.redirect(location, 302);
  },
};

function landingHTML() {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Algohns V12 — Quant Asset Manager OS</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; display:grid; place-items:center; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
         background: radial-gradient(1200px 600px at 20% -10%, #10233a 0%, #070B13 55%); color:#F8FAFC; }
  .card { max-width:640px; padding:40px; text-align:center; }
  .mark { width:96px; height:96px; margin:0 auto 20px; }
  h1 { font-size:1.7rem; margin:0 0 6px; letter-spacing:.3px; }
  .badge { display:inline-block; padding:3px 12px; border-radius:999px; font-size:.72rem; font-weight:700;
           text-transform:uppercase; background:linear-gradient(90deg,#E2B86B,#38BDF8); color:#070B13; margin-bottom:18px; }
  p { color:#94a3b8; line-height:1.6; }
  code { background:rgba(56,189,248,.12); border:1px solid rgba(56,189,248,.25); padding:2px 8px; border-radius:8px; color:#38BDF8; }
</style>
</head>
<body>
  <div class="card">
    <svg class="mark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="Algohns mark">
      <defs>
        <linearGradient id="g" x1="16" y1="12" x2="116" y2="118" gradientUnits="userSpaceOnUse">
          <stop stop-color="#E2B86B"/><stop offset="0.45" stop-color="#38BDF8"/><stop offset="1" stop-color="#0F172A"/>
        </linearGradient>
        <linearGradient id="s" x1="32" y1="24" x2="96" y2="104"><stop stop-color="#fff"/><stop offset="1" stop-color="#D9A84F"/></linearGradient>
      </defs>
      <rect x="8" y="8" width="112" height="112" rx="28" fill="#070B13" stroke="url(#g)" stroke-width="5"/>
      <path d="M78 24c-15 5-28 18-39 39l-9 18 19-9c5 19 23 27 43 22-13-6-20-16-19-30 10-3 18-8 25-16-9 1-17-1-24-7 5-5 8-11 4-17Z" fill="url(#s)"/>
      <path d="M53 78h22l9 18H70l-4-8H51l-4 8H33l27-56h14l-21 38Zm4-11h8l-3-8-5 8Z" fill="#F8FAFC" opacity=".95"/>
    </svg>
    <div class="badge">Algohns V12 · Python</div>
    <h1>Quant Asset Manager OS</h1>
    <p>The platform is being connected to its host. Set <code>APP_URL</code> in the Cloudflare
    Worker to your live Streamlit app and redeploy to point this domain at the dashboard.</p>
  </div>
</body>
</html>`;
}
