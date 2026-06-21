#!/usr/bin/env python3
"""Bulk pipeline processor — score → offer → outreach ALL leads. No API calls, DB-only."""
import sys, os, time
sys.path.insert(0, '/root/tradeflow_ai')

from agents.scoring_agent import ScoringAgent
from agents.offer_agent import OfferAgent
from agents.outreach_agent import OutreachAgent
from supabase_client import get_businesses

BATCH = 500

def run():
    scoring = ScoringAgent()
    offer = OfferAgent()
    outreach = OutreachAgent()

    total_scored = 0
    total_offered = 0
    total_outreached = 0
    total_disqualified = 0

    # ── Phase 1: Score all enriched ──
    print("📊 Phase 1: Scoring enriched...")
    while True:
        businesses = get_businesses(state='enriched', limit=BATCH)
        if not isinstance(businesses, list) or not businesses:
            break
        for biz in businesses:
            try:
                r = scoring.process(biz)
                if r.get('advanced'):
                    total_scored += 1
            except Exception as e:
                pass
        print(f"   Scored: {total_scored} so far...")
        if len(businesses) < BATCH:
            break

    # ── Phase 2: Offer all scored ──
    print(f"\n📦 Phase 2: Generating offers (total scored: {total_scored})...")
    while True:
        businesses = get_businesses(state='scored', limit=BATCH)
        if not isinstance(businesses, list) or not businesses:
            break
        for biz in businesses:
            try:
                r = offer.process(biz)
                if r.get('advanced'):
                    total_offered += 1
            except Exception as e:
                pass
        print(f"   Offered: {total_offered} so far...")
        if len(businesses) < BATCH:
            break

    # ── Phase 3: Outreach all offer_generated ──
    print(f"\n📧 Phase 3: Creating drafts (total offered: {total_offered})...")
    while True:
        businesses = get_businesses(state='offer_generated', limit=BATCH)
        if not isinstance(businesses, list) or not businesses:
            break
        for biz in businesses:
            try:
                r = outreach.process(biz)
                if r.get('advanced'):
                    if r.get('disqualified'):
                        total_disqualified += 1
                    else:
                        total_outreached += 1
            except Exception as e:
                pass
        print(f"   Outreached: {total_outreached}, Disqualified: {total_disqualified}")
        if len(businesses) < BATCH:
            break

    print(f"\n✅ DONE")
    print(f"   Scored:       {total_scored}")
    print(f"   Offered:      {total_offered}")
    print(f"   Outreached:   {total_outreached}")
    print(f"   Disqualified: {total_disqualified}")

if __name__ == '__main__':
    run()
