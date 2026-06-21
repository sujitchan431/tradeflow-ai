"""TradeFlow Follow-Up Agent — generates multi-touch sequences with dollarized hooks.
Touch 1 (Day 0): Icebreaker — reference their business + leak estimate
Touch 2 (Day 2): Specific audit findings — named gaps with dollar impact
Touch 3 (Day 5): Value proposition — offer hook + ROI + Calendly
Touch 4 (Day 7): Last message — short, direct, no pressure

Based on Three Baskets framework: Basket1=Touch1, Basket2=Touches 2-4, Basket3=Touch5+
"""
import json, time, urllib.request, os
from datetime import datetime, timezone, timedelta

def _env():
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

E = _env()
URL = E.get("SUPABASE_URL", "")
KEY = E.get("SUPABASE_SERVICE_ROLE_KEY", "")
H = {"apikey": KEY, "Authorization": "Bearer " + KEY}
CALENDLY = "https://calendly.com/sujitchan431/15min"


def _api(path, method="GET", body=None):
    url = "%s/rest/v1/%s" % (URL, path)
    req = urllib.request.Request(url, method=method, headers=H)
    if body:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def get_outreached(limit=30):
    """Get outreached businesses with no response, ordered by sent_at."""
    return _api(
        "stage_outreach?select=*,businesses!inner(business_name,industry,city)"
        "&status=in.(sent,delivered,opened)"
        "&replied_at=is.null"
        "&order=sent_at.asc"
        "&limit=%d" % limit
    )


def count_touches(biz_id):
    """Count how many touches already sent to this business."""
    r = _api(
        "stage_outreach?select=id&business_id=eq.%s&status=in.(sent,delivered,opened)"
        "&replied_at=is.null" % biz_id
    )
    if isinstance(r, list):
        return len(r)
    return 0


def build_touch1(biz, offer_pitch=""):
    """Touch 1: Icebreaker + leak estimate."""
    name = biz.get("businesses", {}).get("business_name", "there") if isinstance(biz.get("businesses"), dict) else biz.get("business_name", "there")
    city = biz.get("businesses", {}).get("city", "") if isinstance(biz.get("businesses"), dict) else biz.get("city", "")
    first = name.split()[0] if name else "there"
    loc = " in " + city if city else ""

    subject = "Quick question about %s" % name[:45]
    body = "Hey %s,\n\n" % first
    body += "I was looking at %s%s and noticed a few things that might be costing you jobs.\n\n" % (name, loc)
    body += "Most local businesses lose 30-40%% of their leads to slow response times and missed calls.\n\n"
    body += "I put together a free 5-min audit of your online presence — shows exactly where leads are leaking and what it is costing you.\n\n"
    body += "Two ways to grab it:\n"
    body += "- Reply 'audit' and I will send it over — 1 word, 1 second\n"
    body += "- Or book a 10-min call: %s\n\n" % CALENDLY
    body += "Thanks,\nSujit"
    return subject, body, 1


def build_touch2(biz, offer_pitch=""):
    """Touch 2: Specific gaps + dollar impact."""
    name = biz.get("businesses", {}).get("business_name", "there") if isinstance(biz.get("businesses"), dict) else biz.get("business_name", "there")
    first = name.split()[0] if name else "there"

    subject = "%s — found 3 leaks in your setup" % name[:45]
    body = "%s — quick follow-up. Here is what I found:\n\n" % first
    body += "1. No instant response — 42%% of leads expect a reply within 5 minutes. Most wait hours.\n"
    body += "2. No online booking — every lead has to call. That is friction.\n"
    body += "3. No follow-up system — if someone is busy when they see your ad, they forget.\n\n"
    body += "These 3 gaps typically leak ~$2K-$5K/mo for a business your size.\n\n"
    body += "Want me to walk you through the exact fix? Takes 10 minutes.\n"
    body += "Grab a time here: %s\n\n" % CALENDLY
    body += "Thanks,\nSujit"
    return subject, body, 2


def build_touch3(biz, offer_pitch=""):
    """Touch 3: Value proposition + ROI."""
    name = biz.get("businesses", {}).get("business_name", "there") if isinstance(biz.get("businesses"), dict) else biz.get("business_name", "there")
    first = name.split()[0] if name else "there"

    subject = "%s — last message on this" % name[:45]
    body = "%s — one last thing.\n\n" % first
    body += "I put together a custom breakdown for %s with:\n" % name
    body += "- Exact leak estimate in $/mo\n"
    body += "- The 2-3 gaps causing it\n"
    body += "- What it costs to fix vs. what it costs to ignore\n\n"
    body += "No pitch, no obligation. Just data.\n\n"
    body += "Reply 'send it' and I will pass it along.\n\n"
    body += "Thanks,\nSujit"
    return subject, body, 3


def generate_followup(biz, touch_num):
    """Generate the right touch email."""
    if touch_num == 2:
        return build_touch2(biz)
    elif touch_num == 3:
        return build_touch3(biz)
    else:
        return build_touch1(biz)


def create_draft(biz_id, email_to, subject, body, touch_num, offer_info):
    """Write a follow-up draft to stage_outreach."""
    record = {
        "business_id": biz_id,
        "email_to": email_to,
        "email_subject": subject,
        "email_body": body,
        "status": "draft",
        "primary_category": "followup_touch%d" % touch_num,
        "notes": json.dumps({"touch": touch_num, "offer": offer_info, "generated_at": datetime.utcnow().isoformat() + "Z"})
    }
    _api("stage_outreach", method="POST", body=record)


def run(batch_size=30):
    """Generate follow-up emails for businesses that need next touch."""
    now = datetime.now(timezone.utc)
    print("TradeFlow Follow-Up Agent — %s" % now.strftime("%Y-%m-%d %H:%M UTC"))

    outreached = get_outreached(batch_size)
    if not isinstance(outreached, list) or not outreached:
        print("   No outreached businesses found.")
        return {"generated": 0}

    print("   Checking %d outreached businesses for follow-up eligibility...\n" % len(outreached))

    generated = 0
    skipped = 0
    for rec in outreached:
        biz_id = rec.get("business_id")
        email_to = rec.get("email_to", "")
        sent_at = rec.get("sent_at", "")

        if not biz_id or not email_to or not sent_at:
            skipped += 1
            continue

        # How many touches already sent?
        touch_count = count_touches(biz_id)
        next_touch = touch_count + 1

        # Only generate touches 2-3 (touch 1 already sent)
        if next_touch < 2 or next_touch > 3:
            skipped += 1
            continue

        # Check timing: Touch 2 after 2 days, Touch 3 after 5 days
        try:
            sent_dt = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        except:
            sent_dt = now - timedelta(days=10)

        days_since = (now - sent_dt).days
        if next_touch == 2 and days_since < 2:
            skipped += 1
            continue
        if next_touch == 3 and days_since < 5:
            skipped += 1
            continue

        # Get business name for logging
        biz_info = rec.get("businesses", {}) if isinstance(rec.get("businesses"), dict) else {}
        name = biz_info.get("business_name", "?")[:30]

        subject, body, tn = generate_followup(rec, next_touch)
        create_draft(biz_id, email_to, subject, body, tn, "follow-up")

        print("   Touch %d -> %s (%s)" % (tn, name, email_to[:30]))
        generated += 1

    print("\n   Generated: %d | Skipped: %d" % (generated, skipped))
    return {"generated": generated, "skipped": skipped}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--batch", type=int, default=30)
    args = p.parse_args()
    run(batch_size=args.batch)
