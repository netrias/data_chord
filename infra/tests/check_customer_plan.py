"""Check the customer root's real mock-provider plan against the deployment receipt."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from deployment_receipt import ReceiptError, _require_customer_platform_forecast

# Given a successful plan-only test of the customer root.
messages = [json.loads(line) for line in sys.stdin]
assert any(message.get("test_summary", {}).get("status") == "pass" for message in messages)
plan, = [message["test_plan"] for message in messages if message["type"] == "test_plan"]
# When the receipt checks its exact forecast.
forecast = [
    {"address": resource["address"], "actions": resource["change"]["actions"]}
    for resource in plan["resource_changes"] if resource["mode"] == "managed"
]
_require_customer_platform_forecast(forecast)
# Then it admits five creates and only state relinquishment for legacy versioning.
assert len(forecast) == 5 and all(change["actions"] == ["create"] for change in forecast)
legacy = "module.data_plane.aws_s3_bucket_versioning.workflow"
_require_customer_platform_forecast([{"address": legacy, "actions": ["forget"]}])
for actions in (["create"], ["update"], ["delete"]):
    try:
        _require_customer_platform_forecast([{"address": legacy, "actions": actions}])
    except ReceiptError:
        continue
    raise AssertionError(f"Customer deployment admitted a versioning mutation: {actions}")
print("Customer plan accepted five creates and a state-only versioning handoff.")
