"""Audit + Scoring Agent — analyzes business gaps, computes multi-dimensional scores.
Writes to stage_scoring table. Advances: enriched → scored.
"""
import json, time

class ScoringAgent:
    """Audits gaps and scores a business. Writes to stage_scoring."""

    # Gap dimensions and their weights
    DIMENSIONS = {
        'visibility': 25,   # Website presence, Google Maps, reviews
        'conversion': 25,   # Booking system, contact form, chat
        'recovery': 25,     # Phone display, response readiness
        'value': 25,        # Rating, review count, social proof
    }

    def process(self, business):
        biz_id = business['id']
        name = business.get('business_name', 'Unknown')

        # Already scored?
        from supabase_client import get_stage_scoring, update_business
        existing = get_stage_scoring(biz_id)
        if isinstance(existing, list) and existing:
            update_business(biz_id, {'status': 'scored'})
            return {'advanced': True, 'score': existing[0].get('pipeline_score', 0),
                    'tier': existing[0].get('pipeline_tier', '?'), 'already_scored': True}

        # ── Visibility Gap (25 pts) ──
        has_website = bool(business.get('website'))
        web_score = business.get('web_presence_score', 0) or 0
        has_google = bool(business.get('google_maps_url'))
        visibility = 0
        if has_website: visibility += 10
        if web_score >= 60: visibility += 10
        if has_google: visibility += 5
        visibility_gap = 25 - visibility

        # ── Conversion Gap (25 pts) ──
        has_booking = business.get('has_booking_system', False)
        has_chat = business.get('has_chat_widget', False)
        has_form = business.get('has_contact_form', False)
        conversion = 0
        if has_booking: conversion += 12
        if has_chat: conversion += 7
        if has_form: conversion += 6
        conversion_gap = 25 - conversion

        # ── Recovery Gap (25 pts) ──
        has_phone = bool(business.get('phone'))
        has_phone_display = business.get('has_phone_display', False)
        has_email = bool(business.get('email'))
        recovery = 0
        if has_phone: recovery += 10
        if has_phone_display: recovery += 8
        if has_email: recovery += 7
        recovery_gap = 25 - recovery

        # ── Value Gap (25 pts) ──
        rating = business.get('rating') or 0
        review_count = business.get('review_count') or 0
        has_fb = business.get('has_facebook', False)
        has_ig = business.get('has_instagram', False)
        value = 0
        if isinstance(rating, (int, float)) and rating >= 4.0: value += 10
        elif isinstance(rating, (int, float)) and rating >= 3.0: value += 5
        if isinstance(review_count, (int, float)) and review_count >= 20: value += 8
        elif isinstance(review_count, (int, float)) and review_count >= 5: value += 4
        if has_fb: value += 4
        if has_ig: value += 3
        value_gap = 25 - value

        # ── Total Score ──
        total_score = visibility + conversion + recovery + value

        # ── Tier ──
        if total_score >= 80: tier = 'S'
        elif total_score >= 65: tier = 'A'
        elif total_score >= 50: tier = 'B'
        elif total_score >= 35: tier = 'C'
        else: tier = 'D'

        # Key gaps identified
        gaps = []
        if visibility_gap >= 15: gaps.append('visibility')
        if conversion_gap >= 15: gaps.append('conversion')
        if recovery_gap >= 15: gaps.append('recovery')
        if value_gap >= 15: gaps.append('value')

        # ── Write to stage_scoring ──
        import urllib.request, os
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

        scoring_record = {
            'business_id': biz_id,
            'visibility_gap': visibility_gap,
            'conversion_gap': conversion_gap,
            'recovery_gap': recovery_gap,
            'pipeline_score': total_score,
            'pipeline_tier': tier,
            'key_gaps': gaps,
            'score_breakdown': {
                'visibility': visibility,
                'conversion': conversion,
                'recovery': recovery,
                'value': value,
            },
            'pipeline_scored_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }

        url = f'{api_url}/rest/v1/stage_scoring'
        req = urllib.request.Request(url, data=json.dumps(scoring_record).encode(),
            headers=headers, method='POST')
        try:
            urllib.request.urlopen(req, timeout=10)
            update_business(biz_id, {'status': 'scored'})
            return {'advanced': True, 'score': total_score, 'tier': tier, 'gaps': gaps}
        except Exception as e:
            return {'advanced': False, 'error': str(e)}
