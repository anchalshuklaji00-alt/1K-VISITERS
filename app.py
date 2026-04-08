import asyncio
import json
import base64
import httpx
import os
import time
from flask import Flask, request, jsonify
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf import json_format

# 🚨 Dhyan dena: Hum yahan tumhari root directory se import kar rahe hain
from proto import FreeFire_pb2

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

FORCE_REFRESH = False

# ── HELPERS ──────────────────────────────────────────────────

def _pad(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len] * pad_len)

def aes_encrypt(plaintext: bytes) -> bytes:
    cipher = AES.new(MAIN_KEY, AES.MODE_CBC, MAIN_IV)
    return cipher.encrypt(_pad(plaintext))

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

# ── NATIVE TOKEN GENERATOR LOGIC (Protobuf se) ───────────────

async def fetch_access_token(client, credential_str: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = credential_str + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp = await client.post(url, data=payload, headers=headers, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        return data.get("access_token", ""), data.get("open_id", "")
    return "", ""

async def fetch_jwt_for_account(client, acc):
    cred_str = f"uid={acc['uid']}&password={acc['password']}"
    access_token, open_id = await fetch_access_token(client, cred_str)
    
    if not access_token:
        return None

    login_body = {
        "open_id": open_id,
        "open_id_type": "4",
        "login_token": access_token,
        "orign_platform_type": "4",
    }
    
    login_req = FreeFire_pb2.LoginReq()
    json_format.ParseDict(login_body, login_req)
    proto_bytes = login_req.SerializeToString()
    encrypted = aes_encrypt(proto_bytes)

    url = "https://loginbp.ggblueshark.com/MajorLogin"
    headers = {
        "User-Agent": USER_AGENT,
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Content-Type": "application/octet-stream",
        "Expect": "100-continue",
        "X-Unity-Version": "2018.4.11f1",
        "X-GA": "v1 1",
        "ReleaseVersion": RELEASE_VERSION,
    }

    resp = await client.post(url, data=encrypted, headers=headers, timeout=15)
    if resp.status_code == 200:
        login_res = FreeFire_pb2.LoginRes()
        login_res.ParseFromString(resp.content)
        msg = json.loads(json_format.MessageToJson(login_res))
        token = msg.get("token", "")
        if token:
            return {"token": token, "uid": acc.get("uid")}
    return None

async def refresh_tokens_routine():
    try:
        with open("uidpass.json", "r") as f:
            accounts = json.load(f)[:LIMIT]
    except Exception:
        return False

    sem = asyncio.Semaphore(50)
    async with httpx.AsyncClient(timeout=30) as client:
        async def process(acc):
            async with sem:
                try:
                    return await fetch_jwt_for_account(client, acc)
                except Exception:
                    return None
        
        tasks = [process(acc) for acc in accounts]
        results = await asyncio.gather(*tasks)
    
    new_tokens = [r for r in results if r]
    
    if new_tokens:
        try:
            with open("/tmp/tokens.json", "w") as f:
                json.dump(new_tokens, f)
        except Exception:
            pass
        try:
            with open("tokens.json", "w") as f:
                json.dump(new_tokens, f)
        except Exception:
            pass
        return True
    return False

def get_tokens_path():
    if os.path.exists("/tmp/tokens.json"):
        return "/tmp/tokens.json"
    if os.path.exists("tokens.json"):
        return "tokens.json"
    return "tokens.json"

# ── ASYNC ACTIONS ────────────────────────────────────────────

async def do_visit(client: httpx.AsyncClient, token: str, target_uid, region: str) -> bool:
    global FORCE_REFRESH
    url = f"https://client.{region.lower()}.freefiremobile.com/GetPlayerPersonalShow"
    try:
        payload = encrypt_api(f"08{Encrypt_ID(target_uid)}1007")
        r = await client.post(url, headers=game_headers(token, region), content=payload, timeout=10)
        # Token expire detect
        if r.status_code in [401, 403]:
            FORCE_REFRESH = True
        return r.status_code == 200
    except Exception:
        return False

async def do_addfriend(client: httpx.AsyncClient, token: str, target_uid, region: str) -> bool:
    global FORCE_REFRESH
    url = f"https://client.{region.lower()}.freefiremobile.com/RequestAddingFriend"
    try:
        sender_uid = get_sender_uid(token)
        if not sender_uid:
            return False
        payload = encrypt_api(f"08{Encrypt_ID(sender_uid)}10{Encrypt_ID(target_uid)}1801")
        r = await client.post(url, headers=game_headers(token, region), content=payload, timeout=10)
        # Token expire detect
        if r.status_code in [401, 403]:
            FORCE_REFRESH = True
        return r.status_code == 200
    except Exception:
        return False

async def run_bulk(target_uid, action: str, limit: int, region: str) -> dict:
    global FORCE_REFRESH
    
    token_path = get_tokens_path()
    needs_refresh = False
    
    # 6 Hours ya Empty hone par refresh logic
    if not os.path.exists(token_path):
        needs_refresh = True
    else:
        if time.time() - os.path.getmtime(token_path) > 6 * 3600:
            needs_refresh = True
            
    if FORCE_REFRESH:
        needs_refresh = True
        FORCE_REFRESH = False
        
    if needs_refresh:
        await refresh_tokens_routine()
        token_path = get_tokens_path()

    try:
        with open(token_path, "r") as f:
            tokens = json.load(f)
    except FileNotFoundError:
        return {"error": "tokens.json file nahi mili!"}

    sem = asyncio.Semaphore(50)

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

