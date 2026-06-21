"""Offer Agent — generates personalized multi-gap offers for scored businesses.
Reads scoring data, matches gaps to offers, writes to stage_offers table.
Advances: scored → offer_generated.
"""
import json, time, urllib.request, os

# Offer catalog — maps gap types to specific offers
OFFER_CATALOG = {
    'visibility': {
        'name': 'AI Smart Website',
        'type': 'website',
        'monthly': 297,
        'setup': 500,
        'pitch': "Your online presence is invisible. We'll build you a website that ranks on Google and converts visitors into booked jobs — for ⅓ the cost of a human web agency.",
    },
    'conversion': {
        'name': 'Chat + Voice Widget',
        'type': 'chat_widget',
        'monthly': 247,
        'setup': 300,
        'pitch': "42% of visitors leave without booking. Our chat widget captures every lead 24/7 — even when you're on a job site. Pay only when it works.",
    },
    'recovery': {
        'name': 'AI Voice Agent',
        'type': 'phone',
        'monthly': 297,
        'setup': 400,
        'pitch': "62% of callers won't call back if no one answers. Our AI answers every call, books jobs, and never takes a day off. Costs less than a part-time receptionist.",
    },
    'value': {
        'name': 'Reputation Mgmt',
        'type': 'reputation',
        'monthly': 197,
        'setup': 0,
        'pitch': "Get more 5-star reviews on complete autopilot for less than your phone bill. Auto-responds to every review, detects spam, follows up every 3 days. You do nothing — reviews roll in while you work.",
    },
    'booking': {
        'name': 'AI Receptionist',
        'type': 'booking',
        'monthly': 400,
        'setup': 500,
        'pitch': "Every missed call is a missed job. Our AI receptionist answers, books, and follows up — for less than minimum wage. Never lose another lead to voicemail.",
    },
    'social': {
        'name': 'Social Media Mgmt',
        'type': 'social',
        'monthly': 500,
        'setup': 0,
        'pitch': "Your competitors are on social media. We'll manage your Facebook and Instagram, post engaging content, and bring in leads while you focus on the work.",
    },
}


class OfferAgent:
    """Generates personalized offers based on identified gaps."""

    def process(self, business):
        biz_id = business['id']
        name = business.get('business_name', 'Unknown')
        industry = business.get('industry', 'home service')
        city = business.get('city', '')

        # Already has offers for ALL gaps?
        # Get scoring data
        from supabase_client import get_stage_offers, get_stage_scoring, update_business
        existing = get_stage_offers(biz_id)
        
        scoring = get_stage_scoring(biz_id)
        if isinstance(scoring, dict) and 'error' in scoring:
            return {'advanced': False, 'error': 'no_scoring'}
        if not isinstance(scoring, list) or not scoring:
            return {'advanced': False, 'error': 'no_scoring_data'}

        sc = scoring[0]
        
        # ── Compute gaps first ──
        gaps = sc.get('key_gaps', []) or []
        tier = sc.get('pipeline_tier', 'B')
        
        GAP_ALIASES = {
            'visibility': ['visibility', 'website', 'web', 'online presence', 'google maps', 'no website', 'website broken'],
            'conversion': ['conversion', 'booking', 'contact form', 'no contact form', 'no booking', 'no booking system', 'chat'],
            'recovery': ['recovery', 'phone', 'email', 'no email', 'no phone', 'unreachable'],
            'value': ['value', 'reviews', 'rating', 'social proof', 'no reviews'],
            'booking': ['booking', 'receptionist', 'calendar', 'scheduling'],
            'social': ['social', 'facebook', 'instagram', 'no social'],
        }
        
        catalog_keys = set(OFFER_CATALOG.keys())
        if gaps and not any(g in catalog_keys for g in gaps):
            mapped = set()
            for g in gaps:
                g_lower = g.lower()
                for cat_key, aliases in GAP_ALIASES.items():
                    if any(a in g_lower for a in aliases):
                        mapped.add(cat_key)
            gaps = list(mapped) if mapped else []

        if not gaps:
            if (sc.get('visibility_gap') or 0) >= 10: gaps.append('visibility')
            if (sc.get('conversion_gap') or 0) >= 10: gaps.append('conversion')
            if (sc.get('recovery_gap') or 0) >= 10: gaps.append('recovery')
            if (sc.get('value_gap') or 0) >= 10: gaps.append('value')

        if 'visibility' not in gaps and (sc.get('visibility_gap') or 0) >= 5: gaps.append('visibility')
        if 'conversion' not in gaps and (sc.get('conversion_gap') or 0) >= 5: gaps.append('conversion')
        if 'recovery' not in gaps and (sc.get('recovery_gap') or 0) >= 5: gaps.append('recovery')
        if 'value' not in gaps and (sc.get('value_gap') or 0) >= 5: gaps.append('value')

        if business.get('has_facebook') or business.get('has_instagram'):
            if 'social' not in gaps:
                gaps.append('social')

        if not business.get('has_booking_system'):
            if 'booking' not in gaps:
                gaps.append('booking')

        if not gaps:
            gaps = ['visibility']
        
        # ── Already has offers for ALL computed gaps? ──
        if isinstance(existing, list) and existing:
            existing_types = {o.get('offer_type') for o in existing}
            GAP_TO_TYPE = {
                'visibility': 'website', 'conversion': 'chat_widget',
                'recovery': 'phone', 'value': 'reputation',
                'booking': 'booking', 'social': 'social',
            }
            expected_types = {GAP_TO_TYPE[g] for g in gaps if g in GAP_TO_TYPE}
            if expected_types.issubset(existing_types):
                update_business(biz_id, {'status': 'offer_generated'})
                return {'advanced': True, 'offer_count': len(existing), 'already_offered': True}
            # Else: fall through — generate missing offers

        # ── Generate offers for ALL gaps (no limit) ──
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
            'Prefer': 'return=minimal',
        }

        offers_written = 0
        for gap in gaps:  # ALL gaps — one offer per gap
            catalog = OFFER_CATALOG.get(gap)
            if not catalog:
                continue

            city_str = f' in {city}' if city else ''
            headline = f"{catalog['name']} for {name}{city_str}"

            offer = {
                'business_id': biz_id,
                'offer_name': catalog['name'],
                'offer_type': catalog['type'],
                'offer_tier': tier,
                'offer_monthly_price': catalog['monthly'],
                'offer_setup_price': catalog['setup'],
                'offer_headline': headline,
                'offer_pitch': catalog['pitch'],
                'offer_outreach_angle': gap,
                'offer_generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            }

            url = f'{api_url}/rest/v1/stage_offers'
            req = urllib.request.Request(url, data=json.dumps(offer).encode(),
                headers=headers, method='POST')
            try:
                urllib.request.urlopen(req, timeout=10)
                offers_written += 1
            except urllib.error.HTTPError as e:
                if e.code == 409:
                    offers_written += 1  # Already exists — count as success
                else:
                    print(f"  Offer write error for #{biz_id}: HTTP {e.code}")
            except Exception as e:
                print(f"  Offer write error for #{biz_id}: {e}")

        if offers_written > 0:
            update_business(biz_id, {'status': 'offer_generated'})
            return {'advanced': True, 'offer_count': offers_written, 'gaps': gaps[:3]}
        
        return {'advanced': False, 'error': 'no_offers_written'}
