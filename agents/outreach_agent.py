"""Outreach Agent — checks stage_outreach and advances.
Does NOT send outreach — that's handled by stage6_outreach.py cron.
"""
class OutreachAgent:
    """Check outreach readiness and advance."""
    
    def process(self, business):
        biz_id = business["id"]
        name = business.get("business_name", "Unknown")
        email = business.get("email")
        
        # No email = disqualified for email outreach
        if not email:
            from supabase_client import update_business
            update_business(biz_id, {"status": "disqualified"})
            return {"advanced": True, "disqualified": True, "reason": "no_email"}
        
        from supabase_client import get_stage_outreach, count_outreach_events, update_business
        
        count = count_outreach_events(biz_id)
        if count > 0:
            update_business(biz_id, {"status": "outreached"})
            return {"advanced": True, "events": count}
        
        return {"advanced": False, "reason": "no_outreach_yet"}
