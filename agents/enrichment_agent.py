"""Enrichment Agent — fetches business website, extracts social links, contact info, booking/chat detection.
Writes enriched data back to businesses table. Advances status: new → enriched.
"""
import json, time, re, ssl, urllib.request
from html.parser import HTMLParser

EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_RE = re.compile(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}')

SKIP_DOMAINS = ['example.com', 'domain.com', 'your.com', 'email.com', 'sentry', 'godaddy']
SKIP_EMAILS = [r'\.(jpg|png|gif|svg|webp)@', r'@2x\.', r'@\d+x\.', r'^sprite', r'^logo@', r'^cropped', r'^chosen', r'^preferred', r'^%20', r'^flags@', r'^team_placeholder', r'^your@email', r'^stars@', r'^bear-', r'^becky-', r'^buckner-', r'^in-content']

def is_valid_email(e):
    e = e.lower().strip()
    if len(e) > 80 or '@' not in e: return False
    for p in SKIP_EMAILS:
        if re.search(p, e): return False
    domain = e.split('@')[-1]
    if any(d in domain for d in SKIP_DOMAINS): return False
    return True

def is_valid_phone(p):
    digits = re.sub(r'\D', '', p)
    return len(digits) >= 10

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

def fetch_page(url, timeout=10):
    if not url or not url.startswith('http'):
        return None
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        return resp.read().decode('utf-8', errors='replace')[:300000]
    except:
        return None


class EnrichmentAgent:
    """Fetches website, analyzes it, writes enriched data to businesses table."""

    def process(self, business):
        biz_id = business['id']
        name = business.get('business_name', 'Unknown')
        website = business.get('website', '')
        enrichment_status = business.get('enrichment_status', 'raw')

        # Already enriched
        if enrichment_status == 'enriched':
            from supabase_client import update_business
            update_business(biz_id, {'status': 'enriched'})
            return {'advanced': True, 'reason': 'already_enriched'}

        # Normalize website URL
        if website and not website.startswith('http'):
            website = 'https://' + website

        result = {
            'enrichment_status': 'enriched',
            'has_website': bool(website),
            'website_status': 'unreachable',
            'web_presence_score': 0,
            'has_contact_form': False,
            'has_booking_system': False,
            'has_chat_widget': False,
            'has_https': website.startswith('https') if website else False,
        }

        if website:
            html = fetch_page(website)
            if html:
                result['website_status'] = 'live'
                parser = PageParser()
                try: parser.feed(html)
                except: pass

                # Social + booking links
                fb = ig = li = calendly_url = None
                for link in parser.links:
                    link_lower = link.lower()
                    if 'facebook.com/' in link_lower and not fb:
                        fb = link
                    elif 'instagram.com/' in link_lower and not ig:
                        ig = link
                    elif 'linkedin.com/' in link_lower and not li:
                        li = link
                    elif 'calendly.com/' in link_lower and not calendly_url:
                        calendly_url = link

                if fb: result['has_facebook'] = True
                if ig: result['has_instagram'] = True
                if li: result['linkedin'] = li
                if calendly_url: result['has_calendly'] = True; result['calendly_url'] = calendly_url

                # Contact detection
                text_lower = parser.text.lower()
                result['has_contact_form'] = parser.has_form or 'contact' in text_lower
                result['has_booking_system'] = any(kw in text_lower for kw in
                    ['book now', 'schedule', 'appointment', 'booking', 'calendly', 'setmore'])
                result['has_chat_widget'] = any(kw in text_lower for kw in
                    ['chat', 'messenger', 'drift', 'intercom', 'tawk', 'livechat'])
                result['has_https'] = website.startswith('https')

                # Extract email if missing
                if not business.get('email'):
                    valid_emails = [e for e in parser.emails if is_valid_email(e)]
                    if valid_emails:
                        # Prefer email matching business name domain
                        domain_hint = name.lower().split()[0] if name else ''
                        best = sorted(valid_emails,
                            key=lambda e: 100 if domain_hint in e.lower() else 0,
                            reverse=True)[0]
                        result['email'] = best

                # Extract phone if missing
                if not business.get('phone'):
                    valid_phones = [p for p in parser.phones if is_valid_phone(p)]
                    if valid_phones:
                        result['phone'] = valid_phones[0]
                        result['has_phone_display'] = True

                # Web presence score (0-100)
                score = 0
                if result['has_https']: score += 20
                if result['has_contact_form']: score += 20
                if result['has_booking_system']: score += 25
                if result['has_chat_widget']: score += 15
                if fb or ig: score += 10
                if 'viewport' in text_lower: score += 10
                result['web_presence_score'] = score

        # Write all enriched data
        from supabase_client import update_business
        update_business(biz_id, result)
        update_business(biz_id, {'status': 'enriched'})

        return {
            'advanced': True,
            'website_status': result['website_status'],
            'web_score': result['web_presence_score'],
            'found_email': 'email' in result,
            'found_phone': 'phone' in result,
        }
