#!/usr/bin/env python3
"""Bulk email extraction from TradeFlow business websites."""
import os, urllib.request, json, re, ssl, time
from concurrent.futures import ThreadPoolExecutor, as_completed

env = {}
with open(os.path.expanduser('~/.hermes/.env')) as f:
    for line in f:
        if '=' in line and not line.startswith('#'):
            k, v = line.strip().split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

URL = env['SUPABASE_URL']
KEY = env['SUPABASE_SERVICE_ROLE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}'}

EMAIL_RE = re.compile(r'\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b')
SKIP = [
    r'@example\.com', r'@sentry\.', r'@domain\.com', r'\.(jpg|png|gif|svg|webp)@',
    r'filler@godaddy', r'@2x', r'@1x', r'@0\.', r'@3x', r'sprite-', r'logo@',
    r'cropped-', r'chosen-sprite', r'nav-logo', r'@300x', r'@100x', r'@75x',
    r'\.(png|jpg|gif|svg|webp)$', r'@2x-', r'\.png@', r'\.jpg@',
]


def is_good(e):
    for p in SKIP:
        if re.search(p, e, re.I):
            return False
    if len(e) > 100:
        return False
    if '.' not in e.split('@')[-1]:
        return False
    return True


def scrape_business(biz):
    bid = biz['id']
    name = biz.get('business_name', '')[:40]
    web = biz.get('website', '')
    if not web.startswith('http'):
        web = 'https://' + web

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(web, headers={'User-Agent': 'Mozilla/5.0 (compatible; TradeFlow/1.0)'})
        html = urllib.request.urlopen(req, timeout=10, context=ctx).read().decode('utf-8', errors='ignore')[:200000]

        emails = set()
        for m in EMAIL_RE.finditer(html):
            e = m.group(1).lower()
            if is_good(e):
                emails.add(e)

        if emails:
            # Prefer email matching business name
            name_words = name.lower().split()[:2]
            best = sorted(emails, key=lambda e: sum(1 for w in name_words if w in e), reverse=True)[0]
            return (bid, best, web, name, 'found')
        return (bid, None, web, name, 'none')
    except Exception as e:
        return (bid, None, web, name, 'error')


def batch_update(updates):
    """Batch update emails in Supabase."""
    if not updates:
        return
    for bid, email in updates:
        try:
            req = urllib.request.Request(
                f'{URL}/rest/v1/businesses?id=eq.{bid}',
                data=json.dumps({'email': email}).encode(),
                headers={**H, 'Content-Type': 'application/json', 'Prefer': 'return=minimal'},
                method='PATCH',
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


def main():
    print('Loading businesses with website, no email...')
    biz_list = []
    offset = 0
    while True:
        req_url = (
            f'{URL}/rest/v1/businesses'
            f'?select=id,business_name,website'
            f'&website=not.is.null'
            f'&website=neq.'
            f'&or=(email.is.null,email.eq.)'
            f'&limit=1000&offset={offset}'
        )
        req = urllib.request.Request(req_url, headers=H)
        resp = urllib.request.urlopen(req, timeout=30)
        batch = json.loads(resp.read().decode())
        if not batch:
            break
        biz_list.extend(batch)
        offset += len(batch)
        print(f'  {offset}...')

    total = len(biz_list)
    print(f'\nTotal: {total} | Threads: 10\n')

    stats = {'found': 0, 'none': 0, 'error': 0}
    buf = []
    BATCH = 25
    start = time.time()

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(scrape_business, b): b for b in biz_list}
        for i, f in enumerate(as_completed(futures), 1):
            bid, email, web, name, label = f.result(timeout=15)
            stats[label] = stats.get(label, 0) + 1
            if email:
                buf.append((bid, email))
            if len(buf) >= BATCH:
                batch_update(buf)
                buf = []
            if i % 500 == 0 or i == total:
                e = time.time() - start
                r = i / e if e > 0 else 0
                pct = i * 100 / total
                print(f'  [{i}/{total}] {pct:.1f}% | {r:.1f}/s | found={stats["found"]} err={stats["error"]}')

    if buf:
        batch_update(buf)

    e = time.time() - start
    print(f'\nDone {e/60:.1f}min | Found: {stats["found"]} | None: {stats["none"]} | Errors: {stats["error"]}')


if __name__ == '__main__':
    main()
