"""Enrichment Agent — checks if business has enrichment data and advances it.
Does NOT generate enrichment data — that's handled by stage2_enrich.py cron.
This agent only checks readiness and advances state.
"""
class EnrichmentAgent:
    """Check enrichment readiness and advance."""
    
    def process(self, business):
        biz_id = business["id"]
        name = business.get("business_name", "Unknown")
        
        has_website = bool(business.get("website"))
        has_email = bool(business.get("email"))
        has_phone = bool(business.get("phone"))
        enrichment_status = business.get("enrichment_status", "raw")
        
        # Already enriched?
        if enrichment_status == "enriched" or has_website:
            from supabase_client import update_business
            update_business(biz_id, {"status": "enriched", "enrichment_status": "enriched"})
            return {"advanced": True, "reason": "has_data"}
        
        # Has some data but not marked enriched
        if has_email or has_phone:
            from supabase_client import update_business
            update_business(biz_id, {"status": "enriched", "enrichment_status": "enriched"})
            return {"advanced": True, "reason": "partial_data"}
        
        # Not ready — stays at 'new'
        return {"advanced": False, "reason": "no_data_yet"}
