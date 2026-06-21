"""Email Sender Agent — sends outreach drafts via Resend API.
Reads stage_outreach drafts, sends compliant emails, tracks daily quota.
Advances businesses: offer_generated → outreached (when sent).
Uses curl for Resend (urllib has 403 compatibility issues).
"""
import os, json, subprocess, time, urllib.request, urllib.error
from datetime import datetime, timezone

RESEND_DAILY_LIMIT = 100
FROM_FULL = "The Blue Whale <admin@thebluewhale.online>"
REPLY_TO = "sujitchan431@gmail.com"

# CAN-SPAM compliance
CAN_SPAM_FOOTER = (
    "\n--\n"
    "The Blue Whale\n"
    "Pune 411015, India\n\n"
    "Reply to this email with 'unsubscribe' to opt out"
)

COMPLIANCE_HEADERS = {
    "List-Unsubscribe": "<mailto:admin@thebluewhale.online?subject=unsubscribe>",
    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
}

LOGO_URL = "https://thebluewhale.online/logo.png"


def text_to_html(plain_text):
    """Wrap plain text in clean HTML template with logo."""
    escaped = plain_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    paragraphs = escaped.split("\n\n")
    html_paras = []
    for p in paragraphs:
        p = p.strip()
        if p:
            html_paras.append(p.replace("\n", "<br>"))
    body_html = "".join(f"<p style='margin:0 0 12px 0;'>{p}</p>" for p in html_paras)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;max-width:560px;margin:0 auto;padding:20px;color:#1a1a1a;">
<div style="text-align:center;margin-bottom:20px;">
  <img src="{LOGO_URL}" alt="The Blue Whale" width="64" height="64" style="border-radius:50%;border:2px solid #0084FF;">
</div>
{body_html}
</body>
</html>"""


def load_env():
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


ENV = load_env()
API_URL = ENV["SUPABASE_URL"]
API_KEY = ENV["SUPABASE_SERVICE_ROLE_KEY"]
RESEND_KEY = ENV.get("RESEND_API_KEY", "")  # TradeFlow Account 1 (NOT ACCT2)

HEADERS = {"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}"}
STATE_FILE = os.path.expanduser("~/.hermes/state/resend_daily_tf.json")


def get_daily_state():
    """Read daily send count. Resets on date change."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            if state.get("date") == today:
                return state
        except:
            pass
    return {"date": today, "sent": 0, "failed_429": 0, "last_send_at": None}


def save_daily_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def quota_remaining():
    state = get_daily_state()
    return max(0, RESEND_DAILY_LIMIT - state["sent"])


def send_via_resend(to_email, subject, body_text):
    """Send via Resend using curl. Returns (result_dict, error_string)."""
    full_text = body_text + CAN_SPAM_FOOTER
    payload = json.dumps({
        "from": FROM_FULL,
        "to": [to_email],
        "subject": subject,
        "text": full_text,
        "html": text_to_html(full_text),
        "reply_to": REPLY_TO,
        "headers": COMPLIANCE_HEADERS,
    })
    auth_header = f"Authorization: Bearer {RESEND_KEY}"
    result = subprocess.run([
        "curl", "-s", "-X", "POST", "https://api.resend.com/emails",
        "-H", auth_header,
        "-H", "Content-Type: application/json",
        "-d", payload,
    ], capture_output=True, text=True, timeout=30)
    try:
        resp = json.loads(result.stdout)
        if "id" in resp:
            return resp, None
        return None, result.stdout[:300]
    except:
        return None, result.stderr or result.stdout[:300]


def get_drafts(limit=15):
    """Get unsent drafts from stage_outreach."""
    url = f"{API_URL}/rest/v1/stage_outreach?select=*&status=eq.draft&order=created_at.asc&limit={limit}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  DB error: {e}")
        return []


def update_outreach(record_id, fields):
    """Update a stage_outreach record."""
    url = f"{API_URL}/rest/v1/stage_outreach?id=eq.{record_id}"
    h = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    req = urllib.request.Request(url, method="PATCH", headers=h, data=json.dumps(fields).encode())
    try:
        urllib.request.urlopen(req, timeout=10)
    except:
        pass


def update_business(biz_id, fields):
    """Update business status."""
    url = f"{API_URL}/rest/v1/businesses?id=eq.{biz_id}"
    h = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    req = urllib.request.Request(url, method="PATCH", headers=h, data=json.dumps(fields).encode())
    try:
        urllib.request.urlopen(req, timeout=10)
    except:
        pass


def run(batch_size=15):
    """Send draft emails via Resend. Returns stats dict."""
    state = get_daily_state()
    remaining = quota_remaining()

    print(f"📤 TradeFlow Email Sender — {datetime.now(timezone.utc).isoformat()}")
    print(f"   Resend quota: {state['sent']}/{RESEND_DAILY_LIMIT} sent today | {remaining} remaining")

    if remaining <= 0:
        print("🛑 DAILY QUOTA EXHAUSTED. Skipping.")
        return {"sent": 0, "failed": 0, "rate_limited": 0, "quota_exhausted": True}

    effective = min(batch_size, remaining)
    drafts = get_drafts(limit=effective)

    if not drafts:
        print("   No drafts to send.")
        return {"sent": 0, "failed": 0, "rate_limited": 0}

    print(f"   Processing {len(drafts)} drafts...\n")

    sent = 0
    failed = 0
    rate_limited = 0

    for draft in drafts:
        draft_id = draft["id"]
        biz_id = draft.get("business_id")
        email_to = (draft.get("email_to") or "").strip()
        subject = draft.get("email_subject", "Quick question")
        body = draft.get("email_body", "")

        if not email_to or "@" not in email_to:
            update_outreach(draft_id, {
                "status": "invalid",
                "notes": json.dumps({"error": "no_valid_email"})
            })
            failed += 1
            print(f"   ❌ Invalid email: #{biz_id}")
            continue

        name = subject[:40]
        print(f"   📧 {name:40s} → {email_to[:40]}")

        resp, error = send_via_resend(email_to, subject, body)

        if resp and "id" in resp:
            notes = draft.get("notes") or {}
            if isinstance(notes, str):
                try: notes = json.loads(notes)
                except: notes = {}
            notes["resend_id"] = resp["id"]
            notes["sent_via"] = "Resend"

            update_outreach(draft_id, {
                "status": "sent",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "notes": json.dumps(notes),
            })

            if biz_id:
                update_business(biz_id, {"status": "outreached"})

            sent += 1
            state["sent"] += 1
            state["last_send_at"] = datetime.now(timezone.utc).isoformat()
            print(f"      ✅ Sent: {resp['id'][:20]}...")

        elif error and "429" in error:
            rate_limited += 1
            state["failed_429"] += 1
            print(f"      🛑 RATE LIMITED (429) — stopping")
            break

        else:
            update_outreach(draft_id, {
                "status": "failed",
                "notes": json.dumps({"error": (error or "unknown")[:200]})
            })
            failed += 1
            print(f"      ❌ Failed: {(error or 'unknown')[:80]}")

    save_daily_state(state)

    print(f"\n   ✅ Sent: {sent} | ❌ Failed: {failed} | 🛑 Rate-limited: {rate_limited}")
    print(f"   📊 Daily total: {state['sent']}/{RESEND_DAILY_LIMIT} ({quota_remaining()} remaining)")

    return {"sent": sent, "failed": failed, "rate_limited": rate_limited}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=15)
    args = p.parse_args()
    run(batch_size=args.batch)
