// Cloudflare Worker: reverse proxy for Garmin's auth / OAuth endpoints.
//
// Why: Railway (and many cloud) egress IPs are on Garmin's rate-limit
// blocklist. The SSO login (sso.garmin.com) and the OAuth1/OAuth2 exchange
// (connectapi.garmin.com) then return 429 by *source IP* — curl_cffi TLS
// impersonation doesn't help because the throttle is IP-based, not on the
// TLS fingerprint. Cloudflare's IP pool isn't on that blocklist, so we bounce
// those requests through this Worker.
//
// How: the bot sends the request to this Worker with the real Garmin host in
// an `X-Garmin-Host` header (or a `__ghost` query param on redirect follows).
// The Worker forwards path + query + body + headers to that host verbatim.
// For OAuth1-signed requests the signature is computed over the canonical
// Garmin URL and only the wire host changes (path/query stay byte-identical),
// so Garmin still validates the signature.
//
// Cookies: the Worker strips the `Domain=` attribute from Set-Cookie so the
// bot's HTTP client binds cookies to the Worker host and replays them here on
// the next hop (the widget SSO flow needs the embed cookies on /sso/signin).
//
// Bot env:
//   GARMIN_OAUTH_PROXY=https://<worker>.workers.dev
//   GARMIN_OAUTH_PROXY_SECRET=<random string>     # must equal PROXY_SECRET
//
// Worker secret (dashboard or `wrangler secret put PROXY_SECRET`):
//   PROXY_SECRET=<same random string>

const ALLOWED_HOSTS = new Set([
  "sso.garmin.com",
  "connectapi.garmin.com",
  "connect.garmin.com",
]);

const DEFAULT_HOST = "connectapi.garmin.com";

/**
 * Constant-time string compare. A plain `!==` leaks the secret's prefix through
 * response timing, one byte at a time.
 */
function secretMatches(provided, expected) {
  const a = new TextEncoder().encode(provided);
  const b = new TextEncoder().encode(expected);
  if (a.byteLength !== b.byteLength) return false;
  return crypto.subtle.timingSafeEqual(a, b);
}

export default {
  async fetch(request, env) {
    // The shared secret is REQUIRED, not optional. Treating it as optional meant a
    // missing binding silently turned this into an open proxy to Garmin's SSO and
    // API — a deploy typo, not an attack, was enough to expose it.
    if (!env.PROXY_SECRET) {
      console.error("PROXY_SECRET is not bound; refusing to proxy");
      return new Response("proxy misconfigured", { status: 500 });
    }
    if (!secretMatches(request.headers.get("X-Proxy-Auth") || "", env.PROXY_SECRET)) {
      return new Response("forbidden", { status: 403 });
    }

    const inUrl = new URL(request.url);

    // Target host: query param (redirect follow) wins over header.
    const ghost = inUrl.searchParams.get("__ghost");
    const targetHost = ghost || request.headers.get("X-Garmin-Host") || DEFAULT_HOST;
    if (!ALLOWED_HOSTS.has(targetHost)) {
      return new Response(`bad target host: ${targetHost}`, { status: 400 });
    }
    inUrl.searchParams.delete("__ghost");

    const targetUrl = `https://${targetHost}${inUrl.pathname}${inUrl.search}`;

    const fwdHeaders = new Headers(request.headers);
    fwdHeaders.delete("X-Garmin-Host");
    // Keep X-Proxy-Auth off the upstream request.
    fwdHeaders.delete("X-Proxy-Auth");
    fwdHeaders.set("Host", targetHost);

    const init = {
      method: request.method,
      headers: fwdHeaders,
      redirect: "manual", // hand 3xx back so the client follows through us
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
    };

    const resp = await fetch(targetUrl, init);

    const outHeaders = new Headers(resp.headers);

    // Rewrite redirects so the follow-up comes back through the Worker.
    const loc = outHeaders.get("Location");
    if (loc) {
      try {
        const locUrl = new URL(loc, targetUrl);
        if (ALLOWED_HOSTS.has(locUrl.hostname)) {
          locUrl.searchParams.set("__ghost", locUrl.hostname);
          outHeaders.set(
            "Location",
            `${inUrl.origin}${locUrl.pathname}${locUrl.search}`
          );
        }
      } catch (_) {
        // leave non-URL Location untouched
      }
    }

    // Strip Domain= from Set-Cookie so cookies bind to the Worker host.
    const setCookies =
      typeof resp.headers.getSetCookie === "function"
        ? resp.headers.getSetCookie()
        : [];
    if (setCookies.length) {
      outHeaders.delete("Set-Cookie");
      for (const c of setCookies) {
        outHeaders.append("Set-Cookie", c.replace(/;\s*Domain=[^;]+/i, ""));
      }
    }

    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers: outHeaders,
    });
  },
};
