import asyncio
import json
import base64
import httpx
from flask import Flask, request, jsonify
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

app = Flask(__name__)

# ============================================================
# 🚨 SIRF YAHAN LIMIT BADLO 🚨
# 1K = 1000 | 2K = 2000 | 3K = 3000 | 5K = 5000
LIMIT = 1000
# ============================================================

REGION = "IND"
RELEASE_VERSION = "OB52"
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"

MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV  = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')

# ── HELPERS ──────────────────────────────────────────────────

def Encrypt_ID(number):
    try:
        number = int(number)
        encoded_bytes = []
        while True:
            byte = number & 0x7F
            number >>= 7
            if number:
                byte |= 0x80
            encoded_bytes.append(byte)
            if not number:
                break
        return bytes(encoded_bytes).hex()
    except Exception:
        return ""

def encrypt_api(plain_text_hex: str) -> bytes:
    plain_text = bytes.fromhex(plain_text_hex)
    cipher = AES.new(MAIN_KEY, AES.MODE_CBC, MAIN_IV)
    return cipher.encrypt(pad(plain_text, AES.block_size))

def get_sender_uid(token: str) -> int:
    try:
        payload = token.split('.')[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        data = json.loads(base64.b64decode(payload).decode('utf-8'))
        uid = data.get("external_uid") or data.get("account_id")
        return int(uid)
    except Exception:
        return 0

def game_headers(token: str, region: str) -> dict:
    return {
        "Host": f"client.{region.lower()}.freefiremobile.com",
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {token}",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Unity-Version": "2018.4.11f1",
        "Connection": "Keep-Alive",
    }

# ── ASYNC ACTIONS ────────────────────────────────────────────

async def do_visit(client: httpx.AsyncClient, token: str, target_uid, region: str) -> bool:
    url = f"https://client.{region.lower()}.freefiremobile.com/GetPlayerPersonalShow"
    try:
        payload = encrypt_api(f"08{Encrypt_ID(target_uid)}1007")
        r = await client.post(url, headers=game_headers(token, region), content=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

async def do_addfriend(client: httpx.AsyncClient, token: str, target_uid, region: str) -> bool:
    url = f"https://client.{region.lower()}.freefiremobile.com/RequestAddingFriend"
    try:
        sender_uid = get_sender_uid(token)
        if not sender_uid:
            return False
        payload = encrypt_api(f"08{Encrypt_ID(sender_uid)}10{Encrypt_ID(target_uid)}1801")
        r = await client.post(url, headers=game_headers(token, region), content=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

async def run_bulk(target_uid, action: str, limit: int, region: str) -> dict:
    try:
        with open("tokens.json", "r") as f:
            tokens = json.load(f)
    except FileNotFoundError:
        return {"error": "tokens.json file nahi mili!"}

    sem = asyncio.Semaphore(50)  # Vercel timeout se bachne ke liye

    async def bounded(token_dict):
        async with sem:
            t = token_dict.get("token", "")
            if action == "visit":
                return await do_visit(client, t, target_uid, region)
            elif action == "add":
                return await do_addfriend(client, t, target_uid, region)
            return False

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*(bounded(td) for td in tokens[:limit]))

    return {
        "status": "success",
        "action": action,
        "target_uid": target_uid,
        "region": region,
        "accounts_tried": len(results),
        "successful_requests": results.count(True),
    }

# ── ROUTES ───────────────────────────────────────────────────

@app.route('/')
def home():
    return jsonify({"msg": f"Visit Bot Running | Limit: {LIMIT}"}), 200

@app.route('/visit', methods=['GET'])
async def visit_endpoint():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "UID zaruri hai"}), 400
    limit = int(request.args.get('limit', LIMIT))
    region = request.args.get('server_name', request.args.get('region', REGION))
    region = region.replace('(', '').replace(')', '').strip().upper()
    result = await run_bulk(uid, "visit", limit, region)
    return jsonify(result), 200

@app.route('/addfriend', methods=['GET'])
async def addfriend_endpoint():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "UID zaruri hai"}), 400
    limit = int(request.args.get('limit', LIMIT))
    region = request.args.get('server_name', request.args.get('region', REGION))
    region = region.replace('(', '').replace(')', '').strip().upper()
    result = await run_bulk(uid, "add", limit, region)
    return jsonify(result), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

