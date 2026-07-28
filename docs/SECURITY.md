# Authentication and public deployment

How beWithMe authenticates, what was wrong, what changed, and what you must set
before putting the shell on a public address.

Companion to `ARCHITECTURE.md` section 6 (the trust model) and `docs/DEPLOY.md`
(shipping to Alibaba Cloud).

---

## 1. The problem this fixes

The original model verified that a user id **existed**, not that the caller
**was** that user:

| step | request | why it worked |
|---|---|---|
| 1 | `GET /api/users` | Public. Returned every user's UUID. |
| 2 | any request with `X-User-Id: <that uuid>` | Auth = "does this UUID exist". It does. |

No secret appeared anywhere in that chain, and step 1 handed out the values to
assert. `ARCHITECTURE.md` section 6 already scoped this model to "single-machine dev
and small private deploys" — it was correct for that, and only that.

A second, quieter issue: the shell forwarded the client's own `X-User-Id`
upstream. Sidecars trust that header unconditionally (invariant 9), so any
request reaching a sidecar carried a client-controlled identity.

---

## 2. What changed

### Always, in both modes

These are structural and are not behind a flag:

- **The proxy strips client-supplied `X-User-Id` and `Authorization`** and
  injects only the id it derived itself (`services/shell/main.py`). A client can
  no longer hand a sidecar an identity.
- **CORS origins are configurable** via `BEWITHME_CORS_ORIGINS`. The previous
  hardcoded `localhost:3000/3002` is the fallback, so local dev is unchanged.
- **Credential-free paths are rate-limited** per IP.
- **Baseline security response headers** (`nosniff`, `DENY` framing,
  `no-referrer`, same-site CORP).

### Mode-dependent

`BEWITHME_AUTH_MODE` selects the identity rule:

| | `legacy` (default) | `strict` |
|---|---|---|
| identity from | `X-User-Id` header | signed `Authorization: Bearer` token |
| `X-User-Id` from client | trusted | **ignored entirely** |
| `GET /api/users` | public | authenticated |
| `POST /api/users` | public | authenticated |
| `POST /api/auth/session` | public | public (rate-limited) |
| forgeable? | **yes** | no |
| safe on a public address? | **no** | yes |

`legacy` reproduces the old behaviour byte for byte. Nothing breaks, no UX
changes, and existing deployments keep working untouched.

### The token

`infra/session_token.py` — HMAC-SHA256 over a JSON payload, base64url, three
dot-separated parts (`v1.<payload>.<signature>`).

Deliberately **not** JWT. No algorithm is negotiated, so the `alg: none` family
of JWT bugs cannot exist here. Signature comparison is constant-time, and every
verification failure returns `None` without saying why — a caller must not learn
whether it got the signature or the expiry wrong.

Tokens carry `uid`, `iat`, `exp` and default to a 30-day life. The DB existence
check still runs on every request, so **deleting a user immediately revokes
access** even while their token is unexpired.

---

## 3. Going public — the checklist

```bash
# 1. Generate a signing key
python -c "from infra.session_token import generate_secret_key as g; print(g())"

# 2. Generate an access key
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then set, in `.env` (or your secret manager — never in an image layer):

```bash
BEWITHME_AUTH_MODE=strict
BEWITHME_SECRET_KEY=<the signing key from step 1>
BEWITHME_ACCESS_KEY=<the access key from step 2>
BEWITHME_CORS_ORIGINS=https://your-frontend-domain
BEWITHME_DEBUG=0
```

And give the clients the same access key:

- web / desktop: `NEXT_PUBLIC_BEWITHME_ACCESS_KEY`
- mobile: `EXPO_PUBLIC_BEWITHME_ACCESS_KEY`

The shell **refuses to boot** in strict mode if the signing or access key is
missing, rather than serving in a state nobody can authenticate against.

Still required regardless of mode:

- Sidecars (`+1`..`+8`) must **not** be reachable from the internet. Only the
  shell is public. This is invariant 9 and no auth mode changes it.
- Terminate TLS in front of the shell. Tokens are bearer credentials.

---

## 4. The one thing this does not solve

`BEWITHME_ACCESS_KEY` is a **single shared secret for the deployment**, not a
per-user password. Anyone holding it can obtain a token for any user id they
know. That is a deliberate trade: it is what allows the pick-your-name screen
to keep working exactly as it does today, with no login form, no password, and
no UX change.

It is the right model for the intended deployment — a personal assistant, one
household, a private URL. It is **not** multi-tenant authentication.

If beWithMe ever serves mutually-untrusting users, the remaining work is a
per-user credential:

1. Add `access_key_hash` to the `users` table (a migration).
2. `POST /api/users` returns a per-user key once, at creation.
3. `POST /api/auth/session` verifies against that hash instead of the shared key.
4. The user picker shows only locally-known identities.

Steps 1-3 slot in behind the same `/api/auth/session` endpoint without touching
the shell or any client. Step 4 is the part that changes UX, which is why it is
not done here.

---

## 5. Tests

- `tests/unit/test_session_token.py` — forgery, tampering, expiry, key
  rotation, malformed input, header parsing.
- `tests/unit/test_shell_auth.py` — mode selection, public-path sets per mode,
  identity resolution, startup validation, and header sanitation.

The header-sanitation tests are the load-bearing ones: if the proxy ever
forwards a client's `X-User-Id` again, every sidecar becomes impersonatable in a
single request.
