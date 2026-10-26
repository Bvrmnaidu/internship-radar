import json, os, subprocess, sys

data = json.load(sys.stdin)
path = data.get("tool_input", {}).get("file_path", "").replace("\\", "/")
if "/src/" not in path and "/tests/" not in path:
    sys.exit(0)
result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "tests"],
                        cwd=os.environ["CLAUDE_PROJECT_DIR"], capture_output=True, text=True)
if result.returncode != 0:
    print(result.stdout[-3000:], file=sys.stderr)
    sys.exit(2)
