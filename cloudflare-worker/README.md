# Garmin OAuth proxy (Cloudflare Worker)

Railway (and most cloud) egress IPs are on Garmin's rate-limit blocklist: the
OAuth exchange against `connectapi.garmin.com` returns **429 by source IP**.
curl_cffi TLS impersonation doesn't help — the throttle is IP-based, not on the
TLS fingerprint. Cloudflare's IP pool isn't on that list, so this Worker
forwards the exchange requests on the bot's behalf.

The SSO widget login (`sso.garmin.com`) is **not** IP-blocked, so it runs
directly; only the `connectapi.garmin.com` exchange goes through the Worker.

---

## What is a Cloudflare Worker? What is wrangler?

- A **Cloudflare Worker** is a small serverless function that runs on
  Cloudflare's edge network (similar to AWS Lambda). It gets its own URL like
  `https://garmin-oauth-proxy.<your-account>.workers.dev`. The free plan
  (100k requests/day) is far more than this bot needs.
- **wrangler** is Cloudflare's official command-line tool for creating,
  testing, and deploying Workers. It logs in to your Cloudflare account,
  uploads the code, manages secrets, and tails logs. It's an npm package, so it
  needs Node.js.

You can deploy **either** with wrangler (Option A, recommended) **or** entirely
through the Cloudflare web dashboard with copy-paste (Option B, no CLI/Node
needed). Both produce the same result.

---

## Prerequisites

- A free Cloudflare account: <https://dash.cloudflare.com/sign-up>
- For Option A only: Node.js 18+ (`node --version` to check;
  install from <https://nodejs.org> or via your package manager).

---

## How it works (so the steps make sense)

The bot points the exchange URL at the Worker, keeps the path + query
byte-identical, and names the real Garmin host in an `X-Garmin-Host` header. The
Worker fetches `https://<X-Garmin-Host><path><query>`. Because the OAuth1
signature is computed over the canonical Garmin URL and only the wire host
changes, Garmin still validates the signature.

An optional shared secret (`PROXY_SECRET` on the Worker = `GARMIN_OAUTH_PROXY_SECRET`
on the bot) stops anyone else from using your Worker as an open proxy.

---

## Option A — Deploy with wrangler (recommended)

All commands are run from inside this `cloudflare-worker/` directory.

1. **Install wrangler** (one-time, global):

   ```bash
   npm install -g wrangler
   wrangler --version        # confirm it works
   ```

2. **Log in to Cloudflare** — opens a browser to authorize:

   ```bash
   wrangler login
   ```

3. **Set the shared secret** — pick any long random string (e.g. from a
   password manager). wrangler will prompt you to paste it:

   ```bash
   wrangler secret put PROXY_SECRET
   ```

   > Keep this string — you'll paste the *same* value into Railway as
   > `GARMIN_OAUTH_PROXY_SECRET` in the last section.

4. **Deploy:**

   ```bash
   wrangler deploy
   ```

   On success it prints your Worker URL, e.g.:

   ```
   https://garmin-oauth-proxy.<your-account>.workers.dev
   ```

   Copy that URL — it's your `GARMIN_OAUTH_PROXY`.

5. **(Optional) Watch live logs** while you test the bot:

   ```bash
   wrangler tail
   ```

Now jump to **"Configure the bot (Railway)"** below.

---

## Option B — Deploy via the dashboard (no CLI / no Node)

1. Go to <https://dash.cloudflare.com> → **Workers & Pages** → **Create** →
   **Create Worker**.
2. Give it a name (e.g. `garmin-oauth-proxy`) and click **Deploy** to create a
   placeholder.
3. Click **Edit code**. Delete the placeholder code, then paste the entire
   contents of [`garmin-oauth-proxy.js`](garmin-oauth-proxy.js). Click
   **Deploy**.
4. Go to the Worker's **Settings → Variables and Secrets**:
   - Add a **Secret** named `PROXY_SECRET` with a long random string as the
     value. Save. (Keep this string for the Railway step.)
5. Back on the Worker's overview page, copy its URL
   (`https://garmin-oauth-proxy.<your-account>.workers.dev`) — that's your
   `GARMIN_OAUTH_PROXY`.

---

## Configure the bot (Railway)

In your Railway project → **Variables**, add:

```
GARMIN_OAUTH_PROXY=https://garmin-oauth-proxy.<your-account>.workers.dev
GARMIN_OAUTH_PROXY_SECRET=<the same random string you set as PROXY_SECRET>
```

Then redeploy the bot (Railway redeploys automatically on a new env var or git
push). The bot will now route the OAuth exchange through the Worker.

- **Local development:** leave `GARMIN_OAUTH_PROXY` **unset** — the bot calls
  Garmin directly (correct for residential IPs, which aren't blocklisted).
- `GARMIN_OAUTH_PROXY_SECRET` is optional. If you set `PROXY_SECRET` on the
  Worker, you **must** set the matching value here, or the Worker returns 403.

---

## Verify it works

1. Tail the Worker logs (`wrangler tail`, or the dashboard's **Logs** tab).
2. In Telegram, run `/start` and log in with Garmin credentials.
3. You should see a request to `/oauth-service/...` hit the Worker and return
   `200`. The bot replies "Successfully logged in!".

### Troubleshooting

- **Worker returns 403** — `GARMIN_OAUTH_PROXY_SECRET` (bot) doesn't match
  `PROXY_SECRET` (Worker), or one of them is missing.
- **Still 429 in bot logs** — confirm `GARMIN_OAUTH_PROXY` is set on Railway and
  the bot was redeployed; check `wrangler tail` to confirm traffic reaches the
  Worker at all.
- **Worker returns 400 "bad target host"** — the bot sent an unexpected host;
  only `sso.garmin.com`, `connectapi.garmin.com`, `connect.garmin.com` are
  allowed (see `ALLOWED_HOSTS` in the Worker).
