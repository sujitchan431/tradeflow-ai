"""Offer Agent — checks stage_offers and advances.
Does NOT generate offers — that's handled by stage4_offers.py cron.
"""
class OfferAgent:
    """Check offer readiness and advance."""
    
    def process(self, business):
        biz_id = business["id"]
        name = business.get("business_name", "Unknown")
        
        from supabase_client import get_stage_offers, update_business
        
        offers = get_stage_offers(biz_id)
        if isinstance(offers, list) and offers:
            update_business(biz_id, {"status": "offer_generated"})
            return {"advanced": True, "offer_count": len(offers)}
        
        return {"advanced": False, "reason": "no_offers_yet"}
