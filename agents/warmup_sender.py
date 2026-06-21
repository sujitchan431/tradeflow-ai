"""TradeFlow Warmup Sender - Graduated domain warmup for thebluewhale.online.
Schedule: 10/day (+calendly) -> 25/day -> 50/day full outreach.
Reads from businesses table (status=offer_generated, has email).
"""
import os, json, subprocess, urllib.request, urllib.error
from datetime import datetime

def _env():
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_E = _env()
_URL = _E.get("SUPABASE_URL", "")
_KEY = _E.get("SUPABASE_SERVICE_ROLE_KEY", "")
_RKEY = _E.get("RESEND_API_KEY", "") or _E.get("RESEND_API_KEY_ACCT2", "")
_FROM = "The Blue Whale <admin@thebluewhale.online>"
_REPLY = "sujitchan431@gmail.com"
_CALENDLY = "https://calendly.com/sujitchan431/15min"
_H = {"apikey": _KEY, "Authorization": "Bearer " + _KEY}
_FOOTER = "\n--\nThe Blue Whale\nPune 411015, India\n\nReply 'unsubscribe' to opt out"


def warmup_params():
    today = datetime.utcnow().date()
    if today <= datetime(2026, 6, 28).date():
        return 10, "phase 1 - 10/day +calendly"
    elif today <= datetime(2026, 7, 4).date():
        return 25, "phase 2 - 25/day +calendly"
    else:
        return 50, "phase 3 - 50/day full"


def get_businesses(limit=10):
    url = "%s/rest/v1/businesses?select=id,business_name,email,industry,city,ad_count,website&status=eq.offer_generated&email=not.is.null&email=neq.&order=ad_count.desc.nullsfirst&limit=%d" % (_URL, limit)
    req = urllib.request.Request(url, headers=_H)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except:
        return []


def send_email(to_email, subject, body):
    full = body + _FOOTER
    html = "<!DOCTYPE html><html><body style='font-family:sans-serif;max-width:560px;margin:0 auto;padding:20px'>"
    html += full.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n\n","</p><p>").replace("\n","<br>")
    html += "</p></body></html>"
    payload = json.dumps({"from": _FROM, "to": [to_email], "subject": subject, "text": full, "html": html, "reply_to": _REPLY})
    r = subprocess.run(["curl", "-s", "-X", "POST", "https://api.resend.com/emails",
        "-H", "Authorization: Bearer " + _RKEY, "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True, text=True, timeout=30)
    try:
        resp = json.loads(r.stdout)
        return resp.get("id"), None
    except:
        return None, r.stderr or r.stdout[:200]


def generate_email(biz):
    name = biz.get("business_name", "there")
    city = biz.get("city", "") or ""
    industry = biz.get("industry", "home services")
    ads = biz.get("ad_count") or 1
    first = name.split()[0] if name else "there"

    est_spend = int(ads) * 300
    dl_mo = int(est_spend * 0.30)
    dl_yr = dl_mo * 12

    loc = "in " + city if city else ""
    subject = "%s: ~$%d/mo leaking from your ads" % (name[:40], dl_mo)
    body = "Hey %s,\n\n" % first
    body += "I came across %s %s and noticed you are running ads. " % (name, loc)
    body += "At ~$%d/mo in estimated ad spend, most businesses like yours have 1-3 gaps " % est_spend
    body += "that leak ~30%% of that spend. Conservative estimate: ~$%d/mo (~$%d/yr) not converting.\n\n" % (dl_mo, dl_yr)
    body += "I put together a free audit that shows exactly where. No cost, no pitch.\n\n"
    body += "Two ways to grab it:\n"
    body += "- Reply 'audit' and I will send it over\n"
    body += "- Or book a 10-min call: %s\n\n" % _CALENDLY
    body += "Thanks,\nThe Blue Whale"
    return subject, body


def patch_biz(biz_id, data):
    url = "%s/rest/v1/businesses?id=eq.%s" % (_URL, biz_id)
    h = {**_H, "Content-Type": "application/json", "Prefer": "return=minimal"}
    req = urllib.request.Request(url, method="PATCH", headers=h, data=json.dumps(data).encode())
    try: urllib.request.urlopen(req, timeout=10)
    except: pass


def create_outreach(biz_id, email, subject, body, name):
    record = {
        "business_id": biz_id, "email_to": email, "email_subject": subject,
        "email_body": body, "status": "sent",
        "sent_at": datetime.utcnow().isoformat() + "Z",
        "primary_category": "warmup",
        "notes": json.dumps({"source": "warmup_sender", "business": name})
    }
    url = "%s/rest/v1/stage_outreach" % _URL
    h = {**_H, "Content-Type": "application/json"}
    req = urllib.request.Request(url, method="POST", headers=h, data=json.dumps(record).encode())
    try: urllib.request.urlopen(req, timeout=10)
    except: pass


def run(batch=None, dry_run=False):
    bs, phase = warmup_params()
    if batch: bs = batch
    print("TradeFlow Warmup Sender - %s UTC" % datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
    print("   Phase: %s | Batch: %d | Mode: %s\n" % (phase, bs, "DRY RUN" if dry_run else "LIVE"))

    leads = get_businesses(bs)
    if not leads:
        print("   No businesses with emails found.")
        return

    print("   Found %d businesses:\n" % len(leads))
    sent = 0
    for biz in leads:
        name = biz.get("business_name", "?")[:40]
        email = biz.get("email", "")
        subject, body = generate_email(biz)
        print("   %s -> %s" % (name, email[:40]))
        if dry_run:
            print("      [DRY RUN] %s" % subject[:60])
            continue
        rid, err = send_email(email, subject, body)
        if rid:
            create_outreach(biz["id"], email, subject, body, name)
            patch_biz(biz["id"], {"status": "outreached"})
            sent += 1
            print("      SENT: %s..." % rid[:20])
        else:
            print("      FAILED: %s" % (err or "?")[:80])
    print("\n   Sent: %d/%d" % (sent, len(leads)))
