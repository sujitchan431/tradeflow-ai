"""Scoring Agent — checks stage_scoring and advances.
Does NOT generate scores — that's handled by stage3_score.py cron.
"""
class ScoringAgent:
    """Check scoring readiness and advance."""
    
    def process(self, business):
        biz_id = business["id"]
        name = business.get("business_name", "Unknown")
        
        from supabase_client import get_stage_scoring, update_business
        
        scoring = get_stage_scoring(biz_id)
        if isinstance(scoring, list) and scoring:
            score = scoring[0].get("pipeline_score", 0)
            tier = scoring[0].get("pipeline_tier", "?")
            update_business(biz_id, {"status": "scored"})
            return {"advanced": True, "score": score, "tier": tier}
        
        return {"advanced": False, "reason": "no_scoring_yet"}
