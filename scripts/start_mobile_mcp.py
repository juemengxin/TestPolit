"""Start the local Mobile MCP server with its private Bearer token."""
import os
from pathlib import Path


root = Path(__file__).resolve().parents[1]
token = (root / 'backend' / '.mobile-mcp-auth').read_text().strip()
if not token:
    raise SystemExit('Mobile MCP token file is empty')
env = {**os.environ, 'MOBILEMCP_AUTH': token, 'MOBILEMCP_DISABLE_TELEMETRY': '1'}
# Upstream traces include typed passwords and screen text; never persist that output.
env.pop('LOG_FILE', None)
with open(os.devnull, 'w') as sink:
    os.dup2(sink.fileno(), 1)
    os.dup2(sink.fileno(), 2)
os.execvpe('npx', ['npx', '-y', '@mobilenext/mobile-mcp@1.0.4', '--listen', '127.0.0.1:8933'], env)
