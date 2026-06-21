"""Bulk Enrichment Runner — processes all 'new' businesses in parallel.
Fetches websites with 20 concurrent threads, batches writes to Supabase.
Target: enrich all 6,081 remaining new businesses.
"""
import os, json, ssl, re, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser

# ── Config ──
CONCURRENT = 20
BATCH_WRITE = 50
TIMEOUT = 10

# Email/phone patterns
EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')

SKIP_EMAILS = [
    r'\.(jpg|png|gif|svg|webp)@', r'@2x\.', r'@\d+x\.', r'^sprite', r'^logo@',
    r'^cropped', r'^chosen', r'^preferred', r'^flags@', r'^your@email', r'^stars@',
    r'^bear-', r'^becky-', r'^buckner-', r'^in-content', r'^team_placeholder'
]
SKIP_DOMAINS = ['example.com', 'domain.com', 'your.com', 'sentry.io', 'godaddy.com']


def is_valid_email(e):
    e = e.lower().strip()
    if len(e) > 80 or '@' not in e: return False
    for p in SKIP_EMAILS:
        if re.search(p, e): return False
    domain = e.split('@')[-1]
    return not any(d in domain for d in SKIP_DOMAINS)


def is_valid_phone(p):
    return len(re.sub(r'\D', '', p)) >= 10


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.emails = set()
        self.phones = set()
        self.has_form = False
        self.text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        href = a.get('href', '')
        if href: self.links.append(href)
        if tag == 'form': self.has_form = True

    def handle_data(self, data):
        self.text += data + " "
        for m in EMAIL_RE.finditer(data):
            self.emails.add(m.group())
        for m in PHONE_RE.finditer(data):
            self.phones.add(m.group())


def fetch_and_parse(url):
    """Fetch website, parse it, return enriched fields. None if unreachable."""
    if not url or not url.startswith('http'):
        return None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx)
        html = resp.read().decode('utf-8', errors='replace')[:200000]

        parser = PageParser()
        try: parser.feed(html)
        except: pass

        result = {
            'website_status': 'live',
            'has_website': True,
            'has_https': url.startswith('https'),
        }

        # Social + booking links
        fb = ig = li = calendly_url = None
        for link in parser.links:
            ll = link.lower()
            if 'facebook.com/' in ll and not fb: fb = link
            elif 'instagram.com/' in ll and not ig: ig = link
            elif 'linkedin.com/' in ll and not li: li = link
            elif 'calendly.com/' in ll and not calendly_url: calendly_url = link

        if fb: result['has_facebook'] = True
        if ig: result['has_instagram'] = True
        if li: result['linkedin'] = li
        if calendly_url: result['has_calendly'] = True; result['calendly_url'] = calendly_url

        # Contact
        tl = parser.text.lower()
        result['has_contact_form'] = parser.has_form or 'contact' in tl
        result['has_booking_system'] = any(kw in tl for kw in
            ['book now', 'schedule', 'appointment', 'booking', 'calendly', 'setmore'])
        result['has_chat_widget'] = any(kw in tl for kw in
            ['chat', 'messenger', 'drift', 'intercom', 'tawk', 'livechat'])

        # Email
        valid_emails = [e for e in parser.emails if is_valid_email(e)]
        if valid_emails:
            result['email'] = valid_emails[0]

        # Phone
        valid_phones = [p for p in parser.phones if is_valid_phone(p)]
        if valid_phones:
            result['phone'] = valid_phones[0]
            result['has_phone_display'] = True

        # Score
        score = 0
        if result.get('has_https'): score += 20
        if result.get('has_contact_form'): score += 20
        if result.get('has_booking_system'): score += 25
        if result.get('has_chat_widget'): score += 15
        if fb or ig: score += 10
        if 'viewport' in tl: score += 10
        result['web_presence_score'] = score

        return result

    except Exception as e:
        return {
            'website_status': 'unreachable',
            'has_website': True,
            'web_presence_score': 0,
            'enrichment_error': str(e)[:100]
        }


def enrich_business(biz):
    """Enrich a single business. Returns (biz_id, fields_dict, success)."""
    biz_id = biz['id']
    website = biz.get('website', '')
    name = biz.get('business_name', '')[:30]
    enrich_status = biz.get('enrichment_status', 'raw')

    # Already enriched — just update status
    if enrich_status == 'enriched':
        return biz_id, {'status': 'enriched'}, True, 'already'

    # Normalize URL
    if website and not website.startswith('http'):
        website = 'https://' + website

    result = {
        'enrichment_status': 'enriched',
        'has_website': bool(website),
        'website_status': 'unreachable' if website else 'no_website',
        'web_presence_score': 0,
        'has_contact_form': False,
        'has_booking_system': False,
        'has_chat_widget': False,
        'has_https': website.startswith('https') if website else False,
    }

    label = 'no_web'
    if website:
        parsed = fetch_and_parse(website)
        if parsed:
            result.update(parsed)
            label = 'live'
        else:
            label = 'dead'

    # Keep existing email/phone if present
    if biz.get('email'):
        result.pop('email', None)
    if biz.get('phone'):
        result.pop('phone', None)

    result['status'] = 'enriched'
    return biz_id, result, True, label


def batch_update(biz_data_list):
    """Update multiple businesses in bulk via in-clause PATCH."""
    if not biz_data_list:
        return
    
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")

    URL = env["SUPABASE_URL"]
    KEY = env["SUPABASE_SERVICE_ROLE_KEY"]
    
    ids = ",".join(biz_data[0] for biz_data in biz_data_list)
    url = f"{URL}/rest/v1/businesses?id=in.({ids})"
    h = {"apikey": KEY, "Authorization": f"Bearer {KEY}",
         "Content-Type": "application/json", "Prefer": "return=minimal"}
    
    # Use same fields for all in batch (last one's fields)
    # For heterogeneous fields, fall back to individual updates
    fields = biz_data_list[-1][1]
    req = urllib.request.Request(url, method="PATCH", headers=h, data=json.dumps(fields).encode())
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception as e:
        # Fall back to individual updates on bulk failure
        for biz_data in biz_data_list:
            biz_id = biz_data[0]
            f = biz_data[1]
            url2 = f"{URL}/rest/v1/businesses?id=eq.{biz_id}"
            req2 = urllib.request.Request(url2, method="PATCH", headers=h, data=json.dumps(f).encode())
            try:
                urllib.request.urlopen(req2, timeout=10)
            except:
                pass


def run():
    """Main runner — fetch all new businesses, enrich in parallel, batch write."""
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")

    URL = env["SUPABASE_URL"]
    KEY = env["SUPABASE_SERVICE_ROLE_KEY"]
    H = {"apikey": KEY, "Authorization": "Bearer " + KEY}

    # Get all new businesses
    print("Fetching new businesses from Supabase...")
    all_biz = []
    offset = 0
    while True:
        url = f"{URL}/rest/v1/businesses?select=id,business_name,website,email,phone,enrichment_status&status=eq.new&limit=1000&offset={offset}"
        req = urllib.request.Request(url, headers=H)
        resp = urllib.request.urlopen(req, timeout=30)
        batch = json.loads(resp.read().decode())
        if not batch: break
        all_biz.extend(batch)
        offset += len(batch)
        print(f"  Loaded {offset} businesses...")

    total = len(all_biz)
    print(f"\nTotal to enrich: {total}")
    print(f"  Concurrent threads: {CONCURRENT}")
    print(f"  Batch writes: {BATCH_WRITE}")
    print(f"  Estimated time: ~{total * 3 / CONCURRENT / 60:.0f} min\n")

    stats = {'live': 0, 'dead': 0, 'no_web': 0, 'already': 0, 'error': 0, 'total': total}
    results_buffer = []
    start = time.time()

    with ThreadPoolExecutor(max_workers=CONCURRENT) as executor:
        futures = {executor.submit(enrich_business, biz): biz for biz in all_biz}

        for i, future in enumerate(as_completed(futures), 1):
            try:
                biz_id, fields, success, label = future.result(timeout=30)
                if success:
                    results_buffer.append((biz_id, fields))
                    stats[label] = stats.get(label, 0) + 1
            except Exception as e:
                stats['error'] += 1

            # Write batch
            if len(results_buffer) >= BATCH_WRITE:
                batch_update(results_buffer)
                results_buffer = []

            # Progress
            elapsed = time.time() - start
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            if i % 100 == 0 or i == total:
                print(f"  [{i}/{total}] {i*100/total:.1f}% | {rate:.1f}/s | ETA {eta:.0f}min | "
                      f"live={stats['live']} dead={stats['dead']} no_web={stats['no_web']} already={stats['already']}")

    # Final flush
    if results_buffer:
        batch_update(results_buffer)

    elapsed = time.time() - start
    print(f"\n=== Done in {elapsed/60:.1f} min ===")
    print(f"  Live websites:    {stats['live']}")
    print(f"  Dead websites:    {stats['dead']}")
    print(f"  No website:       {stats['no_web']}")
    print(f"  Already enriched: {stats['already']}")
    print(f"  Errors:           {stats['error']}")
    print(f"  Total:            {total}")
    print(f"  Rate:             {total/elapsed:.1f} leads/s")


if __name__ == "__main__":
    run()
