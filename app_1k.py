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
from proto import FreeFire_pb2 # Tumhara apna proto

app = Flask(__name__)

# 🚨 LIMIT YAHAN SET KARO 🚨
# 1K ke liye: 1000 | 3K ke liye: 3000 | 5K ke liye: 5000 | 6K ke liye: 6000
LIMIT = 1000 

REGION = "IND"
RELEASE_VERSION = "OB52"
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"

MAIN_KEY = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')

# === HELPER FUNCTIONS ===
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
            if number: byte |= 0x80
            encoded_bytes.append(byte)
            if not number: break
        return bytes(encoded_bytes).hex()
    except: return ""

# === TOKEN GENERATOR LOGIC (From Your Script) ===
async def fetch_access_token(credential_str: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = credential_str + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=payload, headers=headers)
        data = resp.json()
    return data.get("access_token", ""), data.get("open_id", "")

async def fetch_jwt_for_account(acc):
    cred_str = f"uid={acc['uid']}&password={acc['password']}"
    access_token, open_id = await fetch_access_token(cred_str)
    if not access_token: return None

    login_body = {"open_id": open_id, "open_id_type": "4", "login_token": access_token, "orign_platform_type": "4"}
    login_req = FreeFire_pb2.LoginReq()
    json_format.ParseDict(login_body, login_req)
    encrypted = aes_encrypt(login_req.SerializeToString())

    url = "https://loginbp.ggblueshark.com/MajorLogin"
    headers = {"User-Agent": USER_AGENT, "Connection": "Keep-Alive", "Accept-Encoding": "gzip", "Content-Type": "application/octet-stream", "Expect": "100-continue", "X-Unity-Version": "2018.4.11f1", "X-GA": "v1 1", "ReleaseVersion": RELEASE_VERSION}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=encrypted, headers=headers)

    login_res = FreeFire_pb2.LoginRes()
    login_res.ParseFromString(resp.content)
    msg = json.loads(json_format.MessageToJson(login_res))
    return msg.get("token", "")

async def refresh_tokens_routine():
    try:
        with open("uidpass.json", "r") as f: accs = json.load(f)[:LIMIT]
    except: return []
    
    new_tokens = []
    # Concurrency limit to prevent Vercel timeout crashes
    sem = asyncio.Semaphore(50) 
    async def process(acc):
        async with sem:
            t = await fetch_jwt_for_account(acc)
            if t: new_tokens.append({"token": t})
            
    await asyncio.gather(*(process(acc) for acc in accs))
    
    # Vercel mein data save karne ke liye /tmp folder zaroori hai
    with open("/tmp/tokens.json", "w") as f: 
        json.dump(new_tokens, f)
    return new_tokens

# === VISIT LOGIC ===
async def do_visit(client, token_dict, target_uid):
    url = f"https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    try:
        hex_payload = f"08{Encrypt_ID(target_uid)}1007"
        # Isme AES CBC encryption lag raha hai hex bytes par
        payload = AES.new(MAIN_KEY, AES.MODE_CBC, MAIN_IV).encrypt(pad(bytes.fromhex(hex_payload), AES.block_size))
        headers = {"Host": "client.ind.freefiremobile.com", "User-Agent": USER_AGENT, "Accept-Encoding": "gzip", "Authorization": f"Bearer {token_dict['token']}", "X-GA": "v1 1", "ReleaseVersion": RELEASE_VERSION, "Content-Type": "application/x-www-form-urlencoded", "X-Unity-Version": "2018.4.11f1"}
        r = await client.post(url, headers=headers, content=payload, timeout=10)
        return r.status_code == 200
    except: return False

# === FLASK ROUTES ===

@app.route('/')
def home():
    return jsonify({"msg": f"Visit Bot is Running (Limit: {LIMIT})!"}), 200

# 🔴 CRON-JOB WALA LINK: Ye har 6 ghante hit hona chahiye
@app.route('/refresh')
async def refresh_endpoint():
    tokens = await refresh_tokens_routine()
    return jsonify({"msg": f"Tokens Refreshed: {len(tokens)}", "status": "success"})

# 🟢 VISIT BADHANE WALA LINK
@app.route('/visit')
async def visit_endpoint():
    target_uid = request.args.get('uid')
    if not target_uid: return jsonify({"error": "UID zaruri hai"}), 400
    
    tokens = []
    if os.path.exists("/tmp/tokens.json"):
        with open("/tmp/tokens.json", "r") as f: tokens = json.load(f)
    
    # Agar tokens expire ho gaye ya file nahi mili, toh pehle naye banayega
    if not tokens:
        tokens = await refresh_tokens_routine()

    async with httpx.AsyncClient() as client:
        tasks = [do_visit(client, t, target_uid) for t in tokens[:LIMIT]]
        results = await asyncio.gather(*tasks)

    return jsonify({"status": "success", "successful_visits": results.count(True)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
