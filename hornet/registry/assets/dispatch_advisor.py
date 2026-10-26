from ..registry_manager import RegistryManager
from ._common import execute_guarded

@execute_guarded
def execute(args):
    matches = RegistryManager().find(args["goal"], limit=5)
    return {"status": "SUCCESS", "recommendations": matches,
            "next": "Read get_asset_details for a selected URI before calling it. Ranking is keyword-based, not a planner."}
