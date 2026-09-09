"""
One-time helper to obtain a Strava refresh_token.

Usage:
  python3 setup_strava.py

What it does:
  1. Generates an auth URL -> open it in your browser
  2. After approval, Strava redirects to localhost -> copy the full URL
  3. Script exchanges the code for a refresh_token
  4. Copy the refresh_token into your .env file
"""
import sys
import urllib.parse
import requests

print("=" * 60)
print("  Strava OAuth Setup")
print("=" * 60)
print()

client_id = input("Enter your STRAVA_CLIENT_ID: ").strip()
client_secret = input("Enter your STRAVA_CLIENT_SECRET: ").strip()

auth_url = (
    "https://www.strava.com/oauth/authorize"
    f"?client_id={client_id}"
    "&response_type=code"
    "&redirect_uri=http://localhost/exchange_token"
    "&approval_prompt=force"
    "&scope=read,activity:read"
)

print()
print("1. Open this URL in your browser:")
print()
print(f"   {auth_url}")
print()
print("2. Authorize the app -> your browser will redirect to localhost")
print("   (the page won't load — that's expected)")
print("3. Copy the FULL URL from your browser's address bar.")
print()

redirect = input("Paste the redirect URL here: ").strip()
parsed = urllib.parse.urlparse(redirect)
params = urllib.parse.parse_qs(parsed.query)

if "code" not in params:
    print("Error: 'code' parameter not found in URL. Try again.")
    sys.exit(1)

code = params["code"][0]

resp = requests.post("https://www.strava.com/oauth/token", data={
    "client_id": client_id,
    "client_secret": client_secret,
    "code": code,
    "grant_type": "authorization_code",
}, timeout=15)

if resp.status_code != 200:
    print(f"Strava error: {resp.status_code} {resp.text}")
    sys.exit(1)

data = resp.json()
refresh_token = data.get("refresh_token", "")
athlete = data.get("athlete", {})

print()
print("=" * 60)
print(f"  Success! Logged in as: {athlete.get('firstname')} {athlete.get('lastname')}")
print("=" * 60)
print()
print("Add these to your .env file:")
print()
print(f"STRAVA_CLIENT_ID={client_id}")
print(f"STRAVA_CLIENT_SECRET={client_secret}")
print(f"STRAVA_REFRESH_TOKEN={refresh_token}")
print()
