"""Create one short link on a running server and print the response (handy before browser testing).

    python scripts/make_link.py [url] [server]
"""
import json
import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/hello"
server = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"
req = urllib.request.Request(f"{server}/api/shorten", json.dumps({"url": url}).encode())
print(urllib.request.urlopen(req).read().decode())
