"""TradeFlow AI Agent — Pipeline State Machine.
Routes businesses through stages: new → enriched → scored → offer_generated → outreached → responded → booked.
"""
from supabase_client import (
    get_businesses, count_businesses, update_business, update_businesses_batch,
    get_stage_scoring, get_stage_offers, get_stage_outreach, get_stage_demos,
    count_outreach_events
)

# Pipeline stage order
STAGES = [
    "new",              # → discovered, not yet enriched
    "enriched",         # Has website/email/phone data
    "scored",           # Scored by scoring agent
    "offer_generated",  # Offers generated
    "outreached",       # Contact sent
    "responded",        # Got reply
    "booked",           # Meeting booked
    "client",           # Closed deal
    "disqualified",     # Not a fit
]

# Which stage comes next
NEXT_STAGE = {
    "new": "enriched",
    "enriched": "scored",
    "scored": "offer_generated",
    "offer_generated": "outreached",
    "outreached": "responded",
    "responded": "booked",
    "booked": "client",
}


def advance_business(business_id, current_stage, force=False):
    """Advance a single business to the next stage if criteria met."""
    next_stage = NEXT_STAGE.get(current_stage)
    if not next_stage:
        return False, "terminal_stage"
    
    # Check criteria for advancement
    can_advance, reason = check_advance_criteria(business_id, current_stage, next_stage)
    if not can_advance and not force:
        return False, reason
    
    result = update_business(business_id, {"status": next_stage})
    if isinstance(result, dict) and "error" in result:
        return False, f"db_error: {result.get('body', result.get('error'))}"
    
    return True, next_stage


def check_advance_criteria(business_id, current, next_stage):
    """Check if a business meets criteria to advance."""
    
    if current == "new" and next_stage == "enriched":
        # Must have enrichment data (website, email, or phone populated)
        biz = get_businesses(state="new", limit=1, offset=0)
        # Actually, enrichment is done by the enrichment agent. Just check
        # if the business has been enriched by looking at enrichment_status
        return True, "enrichment_agent_handles_this"
    
    if current == "enriched" and next_stage == "scored":
        # Must have scoring record
        scoring = get_stage_scoring(business_id)
        if isinstance(scoring, list) and scoring:
            return True, "has_scoring"
        return False, "no_scoring_record"
    
    if current == "scored" and next_stage == "offer_generated":
        # Must have offer record
        offers = get_stage_offers(business_id)
        if isinstance(offers, list) and offers:
            return True, "has_offers"
        return False, "no_offer_record"
    
    if current == "offer_generated" and next_stage == "outreached":
        # Must have outreach record
        count = count_outreach_events(business_id)
        if count > 0:
            return True, "has_outreach"
        return False, "no_outreach_record"
    
    if current == "outreached" and next_stage == "responded":
        # Must have reply
        outreach = get_stage_outreach(business_id)
        if isinstance(outreach, list):
            for o in outreach:
                if o.get("replied_at"):
                    return True, "has_reply"
        return False, "no_reply_yet"
    
    if current == "responded" and next_stage == "booked":
        # Must have booking
        outreach = get_stage_outreach(business_id)
        if isinstance(outreach, list):
            for o in outreach:
                if o.get("booked_at"):
                    return True, "has_booking"
        return False, "no_booking_yet"
    
    return True, "auto"


def tick_batch(state, limit=10):
    """Process a batch of businesses in a given state.
    Calls the appropriate agent for each business.
    Returns (processed, advanced, errors).
    """
    businesses = get_businesses(state=state, limit=limit)
    if isinstance(businesses, dict) and "error" in businesses:
        print(f"  Error fetching {state}: {businesses.get('body','')}")
        return 0, 0, 1
    
    if not businesses:
        return 0, 0, 0
    
    from agents.enrichment_agent import EnrichmentAgent
    from agents.scoring_agent import ScoringAgent
    from agents.offer_agent import OfferAgent
    from agents.outreach_agent import OutreachAgent
    from agents.response_agent import ResponseAgent
    
    agents = {
        "new": EnrichmentAgent(),
        "enriched": ScoringAgent(),
        "scored": OfferAgent(),
        "offer_generated": OutreachAgent(),
        "outreached": ResponseAgent(),
    }
    
    agent = agents.get(state)
    if not agent:
        print(f"  No agent for state: {state}")
        return 0, 0, 0
    
    processed = 0
    advanced = 0
    errors = 0
    
    for biz in businesses:
        try:
            result = agent.process(biz)
            if result.get("advanced"):
                advanced += 1
            processed += 1
        except Exception as e:
            print(f"  Error processing #{biz['id']}: {e}")
            errors += 1
    
    return processed, advanced, errors


def pipeline_status():
    """Get full pipeline status."""
    status = {}
    for stage in STAGES:
        count = count_businesses(state=stage)
        if count > 0:
            status[stage] = count
    
    total = sum(status.values())
    status["total"] = total
    
    # Compute progress
    terminal = status.get("client", 0) + status.get("disqualified", 0)
    if total > 0:
        status["progress_pct"] = round((total - status.get("new", 0)) / total * 100, 1)
    else:
        status["progress_pct"] = 0
    
    return status
