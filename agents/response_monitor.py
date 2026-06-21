"""TradeFlow Response Monitor — IMAP reply detection for TradeFlow pipeline.
Monitors inbox, matches replies to stage_outreach records, advances businesses.
Uses BODY.PEEK to preserve unread state (NEVER marks emails as read).
"""
import os, sys, json, re, email, imaplib, socket
from datetime import datetime, timezone, timedelta
from email.header import decode_header
import urllib.request, urllib.error

socket.setdefaulttimeout(15)

CHECK_WINDOW_HOURS = 72
SKIP_DOMAINS = ['example.com', 'domain.com', 'your.com', 'sentry.io']


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
API_URL = ENV.get("SUPABASE_URL", "")
API_KEY = ENV.get("SUPABASE_SERVICE_ROLE_KEY", "")
IMAP_EMAIL = ENV.get("REPLY_MONITOR_EMAIL", "")
IMAP_PASSWORD = ENV.get("REPLY_MONITOR_PASSWORD", "")
IMAP_HOST = ENV.get("REPLY_MONITOR_IMAP_HOST", "imap.gmail.com")

HEADERS = {"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}"}


def supabase_get(path):
    url = f"{API_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def supabase_patch(path, data):
    url = f"{API_URL}/rest/v1/{path}"
    h = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    req = urllib.request.Request(url, method="PATCH", headers=h, data=json.dumps(data).encode())
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except:
        return False


def find_match(sender_email):
    encoded = sender_email.replace("@", "%40")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    path = (
        f"stage_outreach?select=id,email_to,email_subject,replied_at,"
        f"notes,business_id,status&email_to=eq.{encoded}"
        f"&status=in.(sent,delivered,opened)"
        f"&sent_at=gte.{cutoff}"
        f"&replied_at=is.null&limit=5&order=sent_at.desc"
    )
    result = supabase_get(path)
    if isinstance(result, list) and result:
        return result[0]
    return None


def decode_mime_header_chunk(value):
    if not value:
        return ""
    parts = decode_header(value)
    result = []
    for part, charset in parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result)


def extract_reply_info(msg):
    sender = ""
    subject_str = ""
    for header, value in msg.items():
        hl = header.lower()
        if hl == "from":
            m = re.search(r'<([^>]+)>', str(value))
            sender = m.group(1) if m else str(value).strip()
        elif hl == "subject":
            subject_str = decode_mime_header_chunk(value)
    subject_str = re.sub(r'^(Re:\s*)+', '', subject_str, flags=re.IGNORECASE).strip()
    return sender.lower().strip(), subject_str


def get_body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode("utf-8", errors="replace")[:500]
                except:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                return payload.decode("utf-8", errors="replace")[:500]
        except:
            pass
    return ""


def check_inbox():
    if not IMAP_EMAIL or not IMAP_PASSWORD:
        print("REPLY_MONITOR_EMAIL / REPLY_MONITOR_PASSWORD not set")
        return {"replied": 0, "booked": 0, "errors": 0}

    print(f"TradeFlow Response Monitor — connecting to {IMAP_HOST} as {IMAP_EMAIL}...")

    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
        mail.login(IMAP_EMAIL, IMAP_PASSWORD)
        mail.select("INBOX")
    except Exception as e:
        print(f"IMAP connection failed: {e}")
        return {"replied": 0, "booked": 0, "errors": 1}

    since_date = (datetime.now() - timedelta(hours=CHECK_WINDOW_HOURS)).strftime("%d-%b-%Y")
    status, messages = mail.search(None, f'(SINCE "{since_date}")')

    if status != "OK" or not messages[0]:
        print("   No recent emails found.")
        mail.logout()
        return {"replied": 0, "booked": 0, "errors": 0}

    msg_ids = messages[0].split()
    print(f"   Found {len(msg_ids)} recent emails — scanning for replies...")

    replied = 0
    booked = 0
    errors = 0

    for msg_id in msg_ids:
        try:
            status, data = mail.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            sender_email, subject = extract_reply_info(msg)
            if not sender_email:
                continue
            if any(d in sender_email for d in SKIP_DOMAINS):
                continue

            match = find_match(sender_email)
            if not match:
                continue

            body = get_body_text(msg)
            now = datetime.now(timezone.utc).isoformat()

            notes = match.get("notes") or {}
            if isinstance(notes, str):
                try: notes = json.loads(notes)
                except: notes = {}
            notes["reply_captured_at"] = now
            notes["reply_text_preview"] = body[:200]

            supabase_patch(f"stage_outreach?id=eq.{match['id']}", {
                "replied_at": now,
                "response": body[:500] if body else "(empty reply)",
                "notes": json.dumps(notes),
            })

            biz_id = match.get("business_id")
            if biz_id:
                body_lower = body.lower()
                booking_keywords = [
                    "book", "schedule", "appointment", "calendar",
                    "available", "call", "meeting", "demo", "interested",
                    "yes", "sounds good", "tell me more"
                ]
                has_booking = any(kw in body_lower for kw in booking_keywords)

                new_status = "booked" if has_booking else "responded"
                supabase_patch(f"businesses?id=eq.{biz_id}", {"status": new_status})

                if has_booking:
                    booked += 1
                    print(f"   BOOKING INTENT biz #{biz_id}: {sender_email[:35]}")
                else:
                    replied += 1
                    print(f"   Reply biz #{biz_id}: {sender_email[:35]}")
                print(f"      Body: {body[:100]}...")
        except Exception as e:
            errors += 1

    mail.logout()
    return {"replied": replied, "booked": booked, "errors": errors}


def run():
    print(f"TradeFlow Response Monitor — {datetime.now(timezone.utc).isoformat()}")
    print(f"   Window: last {CHECK_WINDOW_HOURS}h\n")
    result = check_inbox()
    print(f"\n   Replied: {result['replied']} | Booked: {result['booked']} | Errors: {result['errors']}")
    return result


if __name__ == "__main__":
    run()
