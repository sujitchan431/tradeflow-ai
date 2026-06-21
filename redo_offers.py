#!/usr/bin/env python3
"""Reset and redo: generate multiple offers per lead based on ALL gaps."""
import sys, os, json, time
sys.path.insert(0, '/root/tradeflow_ai')

import urllib.request, urllib.error
from supabase_client import get_businesses

def load_env():
    env = {}
    with open(os.path.expanduser('~/.hermes/.env')) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env = load_env()
URL = env['SUPABASE_URL']
KEY = env['SUPABASE_SERVICE_ROLE_KEY']
H = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json', 'Prefer': 'return=minimal'}

BATCH = 500

print("🗑️  Step 1: Delete ALL existing stage_offers...")
try:
    req = urllib.request.Request(f'{URL}/rest/v1/stage_offers?id=gt.0', headers=H, method='DELETE')
    urllib.request.urlopen(req, timeout=30)
    print("   Deleted.")
except Exception as e:
    print(f"   Error: {e}")

print("\n🔄 Step 2: Reset offer_generated → scored...")
try:
    data = json.dumps({'status': 'scored'}).encode()
    req = urllib.request.Request(
        f'{URL}/rest/v1/businesses?status=eq.offer_generated',
        headers=H, method='PATCH', data=data)
    urllib.request.urlopen(req, timeout=30)
    print("   Reset.")
except Exception as e:
    print(f"   Error: {e}")

print("\n📊 Step 3: Score unscored businesses...")
from agents.scoring_agent import ScoringAgent
scoring_agent = ScoringAgent()
scored_count = 0
while True:
    businesses = get_businesses(state='scored', limit=BATCH)
    if not isinstance(businesses, list) or not businesses:
        break
    for biz in businesses:
        try:
            r = scoring_agent.process(biz)
            if r.get('advanced'):
                scored_count += 1
        except: pass
    print(f"   Scored: {scored_count} so far...")
    if len(businesses) < BATCH:
        break

print(f"\n📦 Step 4: Generate multi-offer for ALL scored leads...")
from agents.offer_agent import OfferAgent

agent = OfferAgent()
total = 0
total_offers = 0
offer_counts = {}

while True:
    businesses = get_businesses(state='scored', limit=BATCH)
    if not isinstance(businesses, list) or not businesses:
        break
    for biz in businesses:
        try:
            r = agent.process(biz)
            if r.get('advanced'):
                total += 1
                oc = r.get('offer_count', 0)
                total_offers += oc
                offer_counts[oc] = offer_counts.get(oc, 0) + 1
        except Exception as e:
            pass
    print(f"   {total} businesses processed, {total_offers} offers generated...")
    if len(businesses) < BATCH:
        break

print(f"\n✅ DONE: {total} businesses, {total_offers} offers")
print(f"   Avg offers/business: {total_offers/total:.1f}" if total else "")
print(f"   Distribution: {sorted(offer_counts.items())}")
