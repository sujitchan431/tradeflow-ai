"""Outreach Agent — generates personalized email drafts from offers.
Writes to stage_outreach table with status=draft.
Delivery handled by existing delivery-processor.py.
Advances: offer_generated → outreached.
"""
import json, time, urllib.request, os

class OutreachAgent:
    """Generates email drafts for businesses with offers."""

    def process(self, business):
        biz_id = business['id']
        name = business.get('business_name', 'Unknown')
        email = business.get('email')
        industry = business.get('industry', 'home service')
        city = business.get('city', '')

        # No email = can't outreach
        if not email:
            from supabase_client import update_business
            update_business(biz_id, {'status': 'disqualified'})
            return {'advanced': True, 'disqualified': True, 'reason': 'no_email'}

        # Already outreached?
        from supabase_client import get_stage_outreach, get_stage_offers, update_business, count_outreach_events
        count = count_outreach_events(biz_id)
        if count > 0:
            update_business(biz_id, {'status': 'outreached'})
            return {'advanced': True, 'events': count, 'already_outreached': True}

        # Get offers for email content
        offers = get_stage_offers(biz_id)
        if isinstance(offers, dict) and 'error' in offers:
            return {'advanced': False, 'error': 'db_error'}
        if not isinstance(offers, list) or not offers:
            return {'advanced': False, 'error': 'no_offers'}

        offer = offers[0]  # Use primary offer
        offer_name = offer.get('offer_name', 'our service')
        offer_pitch = offer.get('offer_pitch', '')

        # ── Generate email ──
        city_str = f' in {city}' if city else ''
        subject = f"Quick question about {name}"

        body = f"""Hi {name.split()[0] if name else 'there'},

I was looking at {name}{city_str} online and noticed a few things that might be costing you jobs.

{offer_pitch}

I'd love to show you how it works — takes 5 minutes and there's zero obligation. Would Tuesday or Wednesday work for a quick call?

Best,
Sujit"""

        # ── Write to stage_outreach ──
        env = {}
        with open(os.path.expanduser('~/.hermes/.env')) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")

        api_url = env['SUPABASE_URL']
        api_key = env['SUPABASE_SERVICE_ROLE_KEY']
        headers = {
            'apikey': api_key,
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        outreach_record = {
            'business_id': biz_id,
            'email_to': email,
            'email_subject': subject,
            'email_body': body,
            'status': 'draft',
            'primary_category': industry,
            'notes': json.dumps({'offer_name': offer_name, 'gap': offer.get('offer_outreach_angle', '')}),
        }

        url = f'{api_url}/rest/v1/stage_outreach'
        req = urllib.request.Request(url, data=json.dumps(outreach_record).encode(),
            headers=headers, method='POST')
        try:
            urllib.request.urlopen(req, timeout=10)
            update_business(biz_id, {'status': 'outreached'})
            return {'advanced': True, 'email': email, 'offer': offer_name}
        except Exception as e:
            return {'advanced': False, 'error': str(e)}
