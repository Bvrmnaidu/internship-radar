import json, sys

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "").replace("\\", "/")
if path.endswith("profile/cv.json") or path.endswith("/.env"):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": f"{path} is protected: cv.json feeds every cover letter, .env holds secrets."}}))
sys.exit(0)
