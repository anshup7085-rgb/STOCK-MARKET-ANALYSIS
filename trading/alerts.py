"""
Alerts — WhatsApp for what you should know, a phone call for what you must act on.

    urgent  (money at risk: an unprotected position, an exit that may not have
             gone out, a tripped breaker, shares missing at the broker)
              -> phone call; if the call is not answered -> WhatsApp
    warning -> WhatsApp
    summary -> WhatsApp, once per daily cycle (ALERT_DAILY_SUMMARY)

Calls ring only for LIVE urgents by default (ALERT_CALLS=live): a simulated
position cannot lose money, so paper urgents go to WhatsApp.

Delivery is through Twilio, which does both channels from one account. Three
things about it that decide whether an alert actually reaches you:

  * WhatsApp only lets a business message you free-form within 24 hours of your
    last message to it. Outside that window only a pre-approved template is
    delivered (Twilio error 63016 otherwise). Set TWILIO_WHATSAPP_CONTENT_SID to
    an approved template with one variable, e.g. "Trading alert: {{1}}", and
    alerts are sent through it. Without one, alerts arrive only while a
    session is open — fine for testing with the sandbox, not for production.
  * Calls to Indian numbers must come from a non-Indian number, and can be
    shown as spam. Save the Twilio number in your contacts as "TRADING ALERT".
  * "Not answered" is read by polling the call: no-answer, busy, failed or
    canceled, or answered by voicemail (machine detection), all fall through
    to WhatsApp. No public web server is needed.

Nothing here can stop trading. Every send is wrapped, failures are journaled
as ALERT_FAILED, and a call is escalated on a background thread so `watch`
keeps protecting positions while the phone rings.

Credentials come from the environment only and are never logged. Prefer a
Twilio API key (TWILIO_API_KEY + TWILIO_API_SECRET), which you can revoke on
its own, over the account auth token.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from market.config import IST

API = "https://api.twilio.com/2010-04-01"

# Final call states. "completed" means someone or something picked up.
_UNANSWERED = {"no-answer", "busy", "failed", "canceled"}
_FINAL = _UNANSWERED | {"completed"}


@dataclass
class AlertConfig:
    to: str | None                      # your number, E.164: +91XXXXXXXXXX
    account_sid: str | None
    auth_user: str | None               # API key SID, or the account SID
    auth_secret: str | None             # API key secret, or the auth token
    whatsapp_from: str | None           # whatsapp:+1415... (sandbox) or your approved sender
    messaging_service_sid: str | None   # needed to send templates
    content_sid: str | None             # approved template with one variable {{1}}
    voice_from: str | None              # a Twilio voice number (non-Indian, for Indian destinations)
    calls: str = "live"                 # live | always | never
    ring_seconds: int = 30
    whatsapp_after_answered_call: bool = False
    daily_summary: bool = True
    dedup_minutes: int = 30

    @classmethod
    def from_env(cls, env: dict | None = None) -> "AlertConfig":
        e = env if env is not None else os.environ
        api_key = e.get("TWILIO_API_KEY")
        return cls(
            to=e.get("ALERT_TO") or None,
            account_sid=e.get("TWILIO_ACCOUNT_SID") or None,
            auth_user=api_key or e.get("TWILIO_ACCOUNT_SID") or None,
            auth_secret=(e.get("TWILIO_API_SECRET") if api_key else e.get("TWILIO_AUTH_TOKEN")) or None,
            whatsapp_from=e.get("TWILIO_WHATSAPP_FROM") or None,
            messaging_service_sid=e.get("TWILIO_MESSAGING_SERVICE_SID") or None,
            content_sid=e.get("TWILIO_WHATSAPP_CONTENT_SID") or None,
            voice_from=e.get("TWILIO_VOICE_FROM") or None,
            calls=(e.get("ALERT_CALLS") or "live").strip().lower(),
            ring_seconds=int(e.get("ALERT_RING_SECONDS") or 30),
            whatsapp_after_answered_call=(e.get("ALERT_WHATSAPP_AFTER_ANSWERED_CALL") or "").lower()
            in ("1", "true", "yes"),
            daily_summary=(e.get("ALERT_DAILY_SUMMARY") or "1").lower() in ("1", "true", "yes"),
            dedup_minutes=int(e.get("ALERT_DEDUP_MINUTES") or 30),
        )

    @property
    def can_auth(self) -> bool:
        return bool(self.to and self.account_sid and self.auth_user and self.auth_secret)

    @property
    def whatsapp_ready(self) -> bool:
        return self.can_auth and bool(self.whatsapp_from or self.messaging_service_sid)

    @property
    def voice_ready(self) -> bool:
        return self.can_auth and bool(self.voice_from)

    def missing(self) -> list[str]:
        out = []
        if not self.to:
            out.append("ALERT_TO")
        if not self.account_sid:
            out.append("TWILIO_ACCOUNT_SID")
        if not (self.auth_user and self.auth_secret):
            out.append("TWILIO_API_KEY + TWILIO_API_SECRET (or TWILIO_AUTH_TOKEN)")
        if not (self.whatsapp_from or self.messaging_service_sid):
            out.append("TWILIO_WHATSAPP_FROM or TWILIO_MESSAGING_SERVICE_SID")
        if not self.voice_from:
            out.append("TWILIO_VOICE_FROM (for calls)")
        return out


class Transport:
    """Minimal HTTPS with basic auth. Swappable for a fake in tests."""

    def __init__(self, user: str, secret: str, timeout: float = 15.0):
        token = base64.b64encode(f"{user}:{secret}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}
        self.timeout = timeout

    def request(self, method: str, url: str, data: dict | None = None) -> dict:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method=method, headers=dict(self._auth))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            # Twilio's error JSON names the problem (e.g. 63016, 21608) and holds no secret.
            try:
                detail = json.loads(exc.read().decode())
                msg = f"{detail.get('code')}: {detail.get('message')}"
            except Exception:
                msg = str(exc)
            raise AlertError(f"Twilio {exc.code} — {msg}") from None


class AlertError(RuntimeError):
    pass


class TwilioClient:
    def __init__(self, cfg: AlertConfig, transport: Transport | None = None):
        self.cfg = cfg
        self.t = transport or Transport(cfg.auth_user, cfg.auth_secret)
        self.base = f"{API}/Accounts/{cfg.account_sid}"

    def send_whatsapp(self, text: str) -> str:
        to = self.cfg.to if self.cfg.to.startswith("whatsapp:") else f"whatsapp:{self.cfg.to}"
        data: dict = {"To": to}
        if self.cfg.messaging_service_sid:
            data["MessagingServiceSid"] = self.cfg.messaging_service_sid
        else:
            data["From"] = self.cfg.whatsapp_from
        if self.cfg.content_sid:
            # Business-initiated, outside any 24h window: only a template is delivered.
            data["ContentSid"] = self.cfg.content_sid
            data["ContentVariables"] = json.dumps({"1": text[:1000]})
        else:
            data["Body"] = text[:1500]
        r = self.t.request("POST", f"{self.base}/Messages.json", data)
        return str(r.get("sid") or "")

    def start_call(self, spoken: str) -> str:
        say = escape(spoken[:500])
        twiml = (
            '<Response><Say language="en-IN">Urgent trading alert. '
            f'{say}</Say><Pause length="1"/><Say language="en-IN">Repeating. {say}</Say>'
            '<Say language="en-IN">Check WhatsApp and the trading terminal now.</Say></Response>'
        )
        r = self.t.request("POST", f"{self.base}/Calls.json", {
            "To": self.cfg.to.replace("whatsapp:", ""),
            "From": self.cfg.voice_from,
            "Twiml": twiml,
            "Timeout": str(self.cfg.ring_seconds),
            "MachineDetection": "Enable",
        })
        return str(r.get("sid") or "")

    def call_outcome(self, sid: str, sleep=time.sleep, poll_s: float = 3.0, max_wait_s: float = 180.0) -> str:
        """answered | voicemail | no-answer | busy | failed | canceled | unknown"""
        waited = 0.0
        while waited <= max_wait_s:
            r = self.t.request("GET", f"{self.base}/Calls/{sid}.json")
            status = str(r.get("status") or "")
            answered_by = str(r.get("answered_by") or "")
            if status in _UNANSWERED:
                return status
            if answered_by.startswith("machine") or answered_by == "fax":
                return "voicemail"
            if status in ("in-progress", "completed") and answered_by == "human":
                return "answered"
            if status == "completed":
                return "answered" if answered_by in ("", "unknown", "human") else "voicemail"
            sleep(poll_s)
            waited += poll_s
        return "unknown"


class Alerter:
    def __init__(self, cfg: AlertConfig, mode: str, state_path: Path,
                 client: TwilioClient | None = None, journal=None,
                 sleep=time.sleep, background: bool = True, now=None):
        self.cfg = cfg
        self.mode = mode
        self.state_path = Path(state_path)
        self.client = client if client is not None else (TwilioClient(cfg) if cfg.can_auth else None)
        self.journal = journal
        self.sleep = sleep
        self.background = background
        self._now = now or (lambda: datetime.now(IST))
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._inflight: set[str] = set()      # urgent alerts whose call is still ringing

    # -- dedup ------------------------------------------------------------

    def _key(self, m: str) -> str:
        return hashlib.sha256(f"{self.mode}|{m}".encode()).hexdigest()[:16]

    def _seen(self) -> dict:
        try:
            seen = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}
        cutoff = self._now() - timedelta(minutes=self.cfg.dedup_minutes)
        return {k: v for k, v in seen.items() if datetime.fromisoformat(v) > cutoff}

    def _fresh(self, messages: list[str]) -> list[str]:
        """Messages not delivered within the dedup window. `watch` raises the
        same alert every minute while the condition lasts; the phone should
        ring once, not sixty times an hour."""
        with self._lock:
            seen = self._seen()
            busy = set(self._inflight)
        return [m for m in messages if self._key(m) not in seen and self._key(m) not in busy]

    def _remember(self, messages: list[str]) -> None:
        """Only DELIVERED alerts are remembered, so an outage does not silence one."""
        with self._lock:
            seen = self._seen()
            now = self._now().isoformat()
            seen.update({self._key(m): now for m in messages})
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps(seen))
            except OSError:
                pass

    # -- sending, never raising -------------------------------------------

    def _log(self, kind: str, **payload) -> None:
        if self.journal is not None:
            try:
                self.journal.append(kind, **payload)
            except Exception:
                pass

    def _whatsapp(self, text: str, why: str) -> bool:
        if not (self.client and self.cfg.whatsapp_ready):
            return False
        try:
            sid = self.client.send_whatsapp(text)
            self._log("ALERT_SENT", channel="whatsapp", why=why, sid=sid)
            return True
        except Exception as exc:
            self._log("ALERT_FAILED", channel="whatsapp", why=why, error=str(exc))
            return False

    def _calls_allowed(self) -> bool:
        if self.cfg.calls == "never" or not (self.client and self.cfg.voice_ready):
            return False
        return self.cfg.calls == "always" or self.mode == "live"

    def _escalate(self, urgent: list[str]) -> str:
        try:
            return self._escalate_inner(urgent)
        finally:
            with self._lock:
                self._inflight -= {self._key(m) for m in urgent}

    def _escalate_inner(self, urgent: list[str]) -> str:
        """Call; if not answered, WhatsApp. Returns the outcome for the journal."""
        head = f"[{self.mode.upper()}] URGENT — {len(urgent)} problem(s)"
        text = head + "\n" + "\n".join(f"• {m}" for m in urgent)
        if not self._calls_allowed():
            if self._whatsapp(text, "urgent (calls off for this mode)"):
                self._remember(urgent)
            return "whatsapp"
        try:
            sid = self.client.start_call(f"{len(urgent)} problems. First: {urgent[0]}")
            outcome = self.client.call_outcome(sid, sleep=self.sleep)
            self._log("ALERT_SENT", channel="call", sid=sid, outcome=outcome)
        except Exception as exc:
            self._log("ALERT_FAILED", channel="call", error=str(exc))
            outcome = "failed"
        delivered = outcome == "answered"
        if outcome != "answered" or self.cfg.whatsapp_after_answered_call:
            delivered = self._whatsapp(text + ("" if outcome == "answered" else f"\n(call {outcome})"),
                                       f"urgent, call {outcome}") or delivered
        if delivered:
            self._remember(urgent)
        return outcome

    def send(self, text: str, why: str = "event") -> bool:
        """One informational WhatsApp (a paper trade opened or settled). Deduplicated, never raises."""
        if not self._fresh([text]):
            return False
        if self._whatsapp(text, why):
            self._remember([text])
            return True
        return False

    def notify(self, rep, kind: str = "cycle") -> None:
        """Dispatch a CycleReport. Returns at once; a call escalates in the background."""
        if self.client is None:
            return
        urgent = self._fresh(list(dict.fromkeys(rep.urgent)))
        warnings = self._fresh([a for a in dict.fromkeys(rep.alerts) if a not in rep.urgent])
        if urgent:
            with self._lock:
                self._inflight |= {self._key(m) for m in urgent}
            if self.background:
                th = threading.Thread(target=self._escalate, args=(urgent,), daemon=False)
                th.start()
                self.threads.append(th)
            else:
                self._escalate(urgent)
        if warnings and self._whatsapp(f"[{self.mode.upper()}] {len(warnings)} alert(s)\n"
                                       + "\n".join(f"• {w}" for w in warnings), "warnings"):
            self._remember(warnings)
        if kind == "cycle" and self.cfg.daily_summary:
            bits = [f"[{self.mode.upper()}] daily cycle, data as of {rep.asof}"]
            if rep.equity is not None:
                bits.append(f"equity Rs{rep.equity:,.0f}")
            bits += rep.submitted + [a for a in rep.actions if "CLOSED" in a or "bought" in a]
            bits.append(rep.decision)
            self._whatsapp("\n".join(bits), "summary")

    def wait(self) -> None:
        for th in self.threads:
            th.join()


def build_alerter(mode: str, state_dir: Path, journal=None) -> Alerter:
    cfg = AlertConfig.from_env()
    return Alerter(cfg, mode, Path(state_dir) / "alerts_state.json", journal=journal)
