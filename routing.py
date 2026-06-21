"""TradeFlow AI Agent — Pipeline State Machine.
Routes businesses through stages. tick runs agents sequentially.
"""
import time
from supabase_client import (
    get_businesses, count_businesses, update_business, update_businesses_batch,
    get_stage_scoring, get_stage_offers, get_stage_outreach,
    count_outreach_events
)

STAGES = [
    "new", "enriched", "scored", "offer_generated",
    "outreached", "responded", "booked", "client", "disqualified",
]

NEXT_STAGE = {
    "new": "enriched",
    "enriched": "scored",
    "scored": "offer_generated",
    "offer_generated": "outreached",
    "outreached": "responded",
    "responded": "booked",
    "booked": "client",
}


def tick_batch(limit=10):
    """Process a batch of businesses through the full pipeline.
    Stage order: new→enriched→scored→offer_generated→outreached.
    Each agent DOES real work (API calls, scraping, scoring, writing).
    """
    from agents.enrichment_agent import EnrichmentAgent
    from agents.scoring_agent import ScoringAgent
    from agents.offer_agent import OfferAgent
    from agents.outreach_agent import OutreachAgent
    from agents.response_agent import ResponseAgent

    enrichment = EnrichmentAgent()
    scoring = ScoringAgent()
    offer = OfferAgent()
    outreach = OutreachAgent()
    response = ResponseAgent()

    results = {'enriched': 0, 'scored': 0, 'offered': 0, 'outreached': 0, 'responded': 0, 'errors': 0}

    # ── Stage 1: Enrichment (new → enriched) ──
    businesses = get_businesses(state='new', limit=limit, has_email=None)
    if isinstance(businesses, list):
        for biz in businesses:
            try:
                r = enrichment.process(biz)
                if r.get('advanced'): results['enriched'] += 1
                else: results['errors'] += 1
            except Exception as e:
                print(f"  Enrichment error #{biz.get('id')}: {e}")
                results['errors'] += 1

    # ── Stage 2: Scoring (enriched → scored) ──
    businesses = get_businesses(state='enriched', limit=limit)
    if isinstance(businesses, list):
        for biz in businesses:
            try:
                r = scoring.process(biz)
                if r.get('advanced'): results['scored'] += 1
            except Exception as e:
                print(f"  Scoring error #{biz.get('id')}: {e}")

    # ── Stage 3: Offers (scored → offer_generated) ──
    businesses = get_businesses(state='scored', limit=limit)
    if isinstance(businesses, list):
        for biz in businesses:
            try:
                r = offer.process(biz)
                if r.get('advanced'): results['offered'] += 1
            except Exception as e:
                print(f"  Offer error #{biz.get('id')}: {e}")

    # ── Stage 4: Outreach (offer_generated → outreached) ──
    businesses = get_businesses(state='offer_generated', limit=limit)
    if isinstance(businesses, list):
        for biz in businesses:
            try:
                r = outreach.process(biz)
                if r.get('advanced'): results['outreached'] += 1
            except Exception as e:
                print(f"  Outreach error #{biz.get('id')}: {e}")

    return results


def sweep_replies(limit=100):
    """Check for replies and advance outreached→responded→booked."""
    from agents.response_agent import ResponseAgent
    agent = ResponseAgent()
    replied = 0
    booked = 0
    disqualified = 0

    for state in ['outreached', 'responded']:
        businesses = get_businesses(state=state, limit=limit)
        if not isinstance(businesses, list):
            continue
        for biz in businesses:
            try:
                r = agent.process(biz)
                if r.get('advanced'):
                    if r.get('stage') == 'responded':
                        replied += 1
                    elif r.get('stage') == 'booked':
                        booked += 1
                    elif r.get('disqualified'):
                        disqualified += 1
            except Exception as e:
                print(f"  Response error #{biz.get('id')}: {e}")

    return {'replied': replied, 'booked': booked, 'disqualified': disqualified}


def advance_batch(limit=50):
    """Fast advance: businesses that already have stage data but state not updated."""
    advanced = 0
    for stage, next_stage in NEXT_STAGE.items():
        businesses = get_businesses(state=stage, limit=limit)
        if not isinstance(businesses, list) or not businesses:
            continue

        ids_to_advance = []
        for biz in businesses:
            bid = biz['id']
            if next_stage == 'scored':
                sc = get_stage_scoring(bid)
                if isinstance(sc, list) and sc:
                    ids_to_advance.append(bid)
            elif next_stage == 'offer_generated':
                off = get_stage_offers(bid)
                if isinstance(off, list) and off:
                    ids_to_advance.append(bid)
            elif next_stage == 'outreached':
                if count_outreach_events(bid) > 0:
                    ids_to_advance.append(bid)

        if ids_to_advance:
            update_businesses_batch(ids_to_advance, {'status': next_stage})
            advanced += len(ids_to_advance)

    return advanced


def pipeline_status():
    """Get full pipeline status."""
    status = {}
    for stage in STAGES:
        count = count_businesses(state=stage)
        if count > 0:
            status[stage] = count

    total = sum(status.values())
    status['total'] = total
    if total > 0:
        status['progress_pct'] = round((total - status.get('new', 0)) / total * 100, 1)
    else:
        status['progress_pct'] = 0

    return status
