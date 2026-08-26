"""Loopback guardrail —— the gatekeeper for the browser surface
(security patch 2026-08-12).

**Precedent (self-audit 2026-08-12)**: binding 127.0.0.1 only blocks
the LAN, it does not block **any web page inside the local
browser** —— loopback is fully reachable from local-machine JS; the
WS handshake is not bound by same-origin policy; a cross-origin POST
using text/plain is a "simple request" and does not trigger CORS
preflight. Threat model = "the engine is running, the user casually
browses some web page" —— that page can then:
  · `{"type":"chat"}` / `{"type":"cli_in"}` → inject arbitrary
    instructions into the running host CLI (an agent that already
    has shell privileges) —— remote prompt injection;
  · `{"type":"chains"}` reads out a gated task id, then
    `{"type":"approve"}` **acts as the approving hand itself** ——
    zero-interaction bypass of the final human gate (the whole
    design of keeping initiation power with the human rests on this
    one gate);
  · `{"type":"stop"}` → kills the engine and the host session in
    one frame;
  · POST /api/mcp with no token falls back to a fail-open identity
    → forge attribution / plant an intent;
  · POST /api/hook → forge permission-card copy to trick the user
    into pressing a key (UI spoofing).

Two gates, both built on headers that **the browser forces and the
page cannot spoof**:
  1. **Origin allowlist** —— only accept the origin of the engine's
     own observe page (loopback + the engine's HTTP port). Browsers
     always attach Origin on WS handshakes and cross-origin POSTs;
     JS cannot alter it.
  2. **Host validation** —— guards against DNS rebinding (an
     attacker's domain rebinds to 127.0.0.1, after which Origin
     reads as "same-origin" but the Host header is still the
     attacker's domain).

**No Origin = allow** —— this is the key to zero false positives:
browsers always send it; non-browser clients (the MCP bridge's
urllib / hookfwd / guard scripts) never do —— letting them through
does not weaken the browser surface at all.

**Boundary (user ruling 2026-08-12, threat model for an open-source
project)**: this project only promises to "**run safely in an
environment the user has approved**" —— the defense line is drawn
on the **external attack** side (a browser using the user's own
browser as a stepping stone to reach the local engine, which is
exactly what this module seals off). A malicious **process** on the
local machine is not in the threat model: it already has local code
execution privilege, so defending against it further is futile (if
it can read .mcp.json it can get the token, and it can freely
construct any header). **Therefore the caller token is not made
fail-closed** (the engine's `_mcp_call` treating "missing token" as
leniently equal to home is a deliberate leniency, not a hole
awaiting a fix); the token's job is caller **identity recognition**
(splitting attribution across multiple instances), not a security
boundary. Stop auditing here —— this is a ruling, not an oversight.

**GET-surface patch (release audit 2026-08-24)**: /trigger (the
deck action surface born 08-22) is pure GET —— a simple request
like `<img src>` **carries no Origin**, so the first gate is blind
to it; a malicious web page can blind-fire approve /
engine=shutdown / an intent trigger with a single img tag, and the
"acting as the approving hand itself" sealed on 08-12 comes back
around through the GET surface. Third gate = **Sec-Fetch-Site**:
modern browsers force this header on **every** request (including
img / script sub-resources and navigation) and the page cannot
alter it; non-browser clients never send it —— absence means allow,
the same zero-false-positive reasoning as Origin. Only attached to
the **action surface** (/trigger, /api/*): panel paths do not carry
it —— opening the panel via a localhost link clicked from another
web page (a web chat tool, etc.) is a legitimate route, and a
navigation request also carries cross-site, so gating it there would
cause false positives; the panel is a pure read-only document
surface with no action to blind-fire anyway. Residual surface = old
browsers that don't send the Sec-Fetch header (~pre-2020), accepted.
"""
from __future__ import annotations

from urllib.parse import urlsplit

# Loopback hostnames (urlsplit.hostname already strips brackets and
# lowercases)
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def host_ok(host: str | None) -> bool:
    """Host header must point at loopback —— the gate against DNS
    rebinding. Missing Host is allowed (HTTP/1.0 and some local
    clients don't send it)."""
    if not host:
        return True
    # Use urlsplit to parse netloc (handles IPv6 brackets and port
    # together)
    try:
        return urlsplit("//" + host.strip()).hostname in LOOPBACK_HOSTS
    except ValueError:
        return False


def origin_ok(origin: str | None, http_port: int) -> bool:
    """Origin absent = non-browser, allow; if present it must be the
    origin of the engine's own page: http + loopback host + the
    engine's HTTP port. `null` (sandbox iframe / file://) is always
    rejected —— that's not our page."""
    if not origin:
        return True
    o = origin.strip().lower()
    if o == "null":
        return False
    try:
        u = urlsplit(o)
        port = u.port
    except ValueError:
        return False
    if u.scheme != "http" or u.path or u.query or u.fragment:
        return False
    return u.hostname in LOOPBACK_HOSTS and port == int(http_port)


def sec_fetch_ok(site: str | None) -> bool:
    """Sec-Fetch-Site absent = non-browser (deck plugin / MCP bridge
    / script), allow; if present, only same-origin (our own page) is
    accepted. cross-site / same-site / **none** are all rejected ——
    this is the gate for the GET action surface against Origin-less
    simple requests (see the "GET-surface patch" section in the
    module docstring).

    `none` was accepted until the 2026-08-25 audit, reasoning that it
    means "address bar / bookmark / external app opened it directly".
    It does — and that is exactly the hole: the browser sends `none`
    for **any** navigation it initiated itself, so a link the user
    clicks in a chat client, a mail client or a PDF carries `none`
    all the way to http://127.0.0.1:9700/trigger?engine=shutdown (the
    port is the documented default), and it survives a cross-site
    redirect too. Nothing legitimate is lost: every non-browser
    client sends no Sec-Fetch-Site at all and is already admitted
    above; the panel's own fetches are same-origin. The only
    capability given up is typing a /trigger URL into the address
    bar."""
    if not site:
        return True
    return site.strip().lower() == "same-origin"
