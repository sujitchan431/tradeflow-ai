"""Response Agent — checks for replies to outreach and advances businesses.
Monitors stage_outreach for replied_at, advances outreached → responded → booked.
"""
class ResponseAgent:
    """Checks for replies and advances businesses."""
    
    def process(self, business):
        """Check replies. Returns {advanced: bool}."""
        biz_id = business["id"]
        name = business.get("business_name", "Unknown")
        current_state = business.get("status", "outreached")
        
        from supabase_client import get_stage_outreach, update_business
        
        outreach = get_stage_outreach(biz_id)
        if isinstance(outreach, dict) and "error" in outreach:
            return {"advanced": False, "error": "db_error"}
        
        if not isinstance(outreach, list) or not outreach:
            return {"advanced": False, "error": "no_outreach_record"}
        
        # Check for replies
        has_reply = False
        has_booking = False
        
        for o in outreach:
            if o.get("replied_at"):
                has_reply = True
            if o.get("booked_at"):
                has_booking = True
            if o.get("response"):
                has_reply = True  # Has response text
        
        if has_booking:
            update_business(biz_id, {"status": "booked"})
            return {"advanced": True, "stage": "booked"}
        
        if has_reply and current_state == "outreached":
            update_business(biz_id, {"status": "responded"})
            return {"advanced": True, "stage": "responded"}
        
        # Check for bounce/opt-out
        for o in outreach:
            status = (o.get("status") or "").lower()
            if status in ("bounced", "failed"):
                update_business(biz_id, {"status": "disqualified"})
                return {"advanced": True, "disqualified": True, "reason": f"outreach_{status}"}
        
        return {"advanced": False, "stage": current_state, "checked": True}
