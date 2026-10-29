"""Microsoft sign-in for Minecraft, done by Omo itself (no browser embedding).

The device-code flow: Omo shows "go to microsoft.com/link and enter ABCD-EFGH",
polls until the player has signed in, then trades the Microsoft token for an
Xbox Live token, an XSTS token and finally a Minecraft token and profile.

  sign_in(client_id, path, show)   the whole flow; `show` gets the code to display
  fresh(client_id, path)           the stored account, refreshed when close to expiry
  sign_out(path)

Only a client ID that Mojang has approved for Minecraft sign-in works at the
last step (login_with_xbox answers 403 otherwise). Tokens live in one file,
mode 0600, and are never printed: errors carry codes, not response bodies.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCOPE = "XboxLive.signin offline_access"
ENDPOINTS = {
    "devicecode": "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
    "token": "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "xbl": "https://user.auth.xboxlive.com/user/authenticate",
    "xsts": "https://xsts.auth.xboxlive.com/xsts/authorize",
    "minecraft": "https://api.minecraftservices.com/authentication/login_with_xbox",
    "profile": "https://api.minecraftservices.com/minecraft/profile",
}
if os.environ.get("OMO_AUTH_BASE"):             # a local stand-in for all of the above (tests)
    ENDPOINTS = {k: f"{os.environ['OMO_AUTH_BASE'].rstrip('/')}/{k}" for k in ENDPOINTS}

REFRESH_MARGIN = 600                            # refresh a Minecraft token with <10 min left
XSTS_ERRORS = {
    2148916233: "This Microsoft account has no Xbox profile yet. Sign in once at minecraft.net, then try again.",
    2148916235: "Xbox Live is not available in this account's country or region.",
    2148916236: "This account needs adult verification on the Xbox website first.",
    2148916237: "This account needs adult verification on the Xbox website first.",
    2148916238: "This is a child account: an adult has to add it to a Microsoft family first.",
}


class AuthError(Exception):
    """A reason the player can act on. `signed_out` means sign in again."""

    def __init__(self, message, signed_out=False):
        super().__init__(message)
        self.signed_out = signed_out


# --------------------------------------------------------------------------- http
def _call(name, form=None, body=None, bearer=None):
    """(status, parsed JSON or {}) for one endpoint. Never raises on HTTP errors."""
    headers = {"Accept": "application/json", "User-Agent": "omo-space/1.0"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(ENDPOINTS[name], data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
        exc.close()
    except OSError as exc:
        raise AuthError(f"could not reach {urllib.parse.urlsplit(ENDPOINTS[name]).netloc}: "
                        f"{getattr(exc, 'reason', exc)}") from None
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


# --------------------------------------------------------------------------- microsoft
def device_code(client_id):
    status, r = _call("devicecode", form={"client_id": client_id, "scope": SCOPE})
    if status != 200 or "device_code" not in r:
        raise AuthError(f"Microsoft did not start a sign-in ({r.get('error') or f'HTTP {status}'}). "
                        "Is the Omo client ID a valid Azure app for personal accounts?")
    return r


def poll(client_id, code, sleep=time.sleep):
    """Wait until the player has entered the code; the Microsoft tokens."""
    interval = int(code.get("interval", 5))
    end = time.monotonic() + int(code.get("expires_in", 900))
    while time.monotonic() < end:
        sleep(interval)
        status, r = _call("token", form={"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                         "client_id": client_id, "device_code": code["device_code"]})
        if status == 200 and "access_token" in r:
            return r
        err = r.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "authorization_declined":
            raise AuthError("Sign-in was cancelled on the Microsoft page.")
        if err in ("expired_token", "bad_verification_code"):
            break
        raise AuthError(f"Microsoft sign-in failed ({err or f'HTTP {status}'}).")
    raise AuthError("The sign-in code expired before it was used. Start again for a new code.")


def refresh_msa(client_id, refresh_token):
    status, r = _call("token", form={"grant_type": "refresh_token", "client_id": client_id,
                                     "refresh_token": refresh_token, "scope": SCOPE})
    if status == 200 and "access_token" in r:
        return r
    if r.get("error") in ("invalid_grant", "interaction_required", "invalid_client"):
        raise AuthError("Your Microsoft sign-in has expired. Sign in again.", signed_out=True)
    raise AuthError(f"Could not refresh the Microsoft sign-in ({r.get('error') or f'HTTP {status}'}).")


# --------------------------------------------------------------------------- xbox + minecraft
def minecraft_login(msa):
    """Microsoft tokens -> Minecraft token and profile (the stored account minus client ID)."""
    status, xbl = _call("xbl", body={
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                       "RpsTicket": f"d={msa['access_token']}"},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"})
    if status != 200 or "Token" not in xbl:
        raise AuthError(f"Xbox Live sign-in failed (HTTP {status}).")
    uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]
    status, xsts = _call("xsts", body={
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"})
    if status != 200 or "Token" not in xsts:
        raise AuthError(XSTS_ERRORS.get(xsts.get("XErr"), f"Xbox Live refused the sign-in (HTTP {status}, "
                                                           f"{xsts.get('XErr', 'no code')})."))
    status, mc = _call("minecraft", body={"identityToken": f"XBL3.0 x={uhs};{xsts['Token']}"})
    if status == 403:
        raise AuthError("Microsoft sign-in worked, but Mojang has not approved this app for Minecraft "
                        "sign-in yet (login_with_xbox: 403).")
    if status != 200 or "access_token" not in mc:
        raise AuthError(f"Minecraft sign-in failed (HTTP {status}).")
    status, profile = _call("profile", bearer=mc["access_token"])
    if status == 404:
        raise AuthError("This Microsoft account does not own Minecraft: Java Edition.")
    if status != 200 or "id" not in profile:
        raise AuthError(f"Could not read the Minecraft profile (HTTP {status}).")
    return {"msa": {"refresh_token": msa.get("refresh_token", "")},
            "minecraft": {"access_token": mc["access_token"],
                          "expires_at": int(time.time()) + int(mc.get("expires_in", 86400))},
            "profile": {"id": profile["id"], "name": profile["name"]}}


# --------------------------------------------------------------------------- storage
def load(path):
    try:
        account = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return account if isinstance(account, dict) and "profile" in account else None


def save(path, account):
    """Write atomically, created 0600 from the start (never briefly readable)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(account, fh)
    os.replace(tmp, path)


def sign_out(path):
    Path(path).unlink(missing_ok=True)


def sign_in(client_id, path, show, sleep=time.sleep):
    code = device_code(client_id)
    show(code)
    account = {"client_id": client_id, **minecraft_login(poll(client_id, code, sleep))}
    save(path, account)
    return account


def fresh(client_id, path):
    """The stored account with a Minecraft token good for a while, else AuthError."""
    account = load(path)
    if not account or account.get("client_id") != client_id:
        raise AuthError("Not signed in.", signed_out=True)
    if account["minecraft"]["expires_at"] - time.time() > REFRESH_MARGIN:
        return account
    msa = refresh_msa(client_id, account["msa"]["refresh_token"])
    msa.setdefault("refresh_token", account["msa"]["refresh_token"])     # not always rotated
    account = {"client_id": client_id, **minecraft_login(msa)}
    save(path, account)
    return account
