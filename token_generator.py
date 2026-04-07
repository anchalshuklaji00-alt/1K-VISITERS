import asyncio
import time
import httpx
import json
import base64
from google.protobuf import json_format
from Crypto.Cipher import AES
from proto import FreeFire_pb2

# ============================================================
#   SETTINGS
# ============================================================
MAIN_KEY        = base64.b64decode('WWcmdGMlREV1aDYlWmNeOA==')
MAIN_IV         = base64.b64decode('Nm95WkRyMjJFM3ljaGpNJQ==')
RELEASE_VERSION = "OB52"
USER_AGENT      = "Dalvik/2.1.0 (Linux; U; Android 13; CPH2095 Build/RKQ1.211119.001)"
ACCOUNTS_FILE   = "uidpass.json"  # NAYA FIX: Direct uidpass.json se padhega

# ============================================================
#   ACCOUNTS LOADER (JSON FORMAT)
# ============================================================
def load_accounts(filepath=ACCOUNTS_FILE):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            accounts = json.load(f)
        print(f"[ACCOUNTS] {len(accounts)} accounts load hue '{filepath}' se")
        return accounts
    except Exception as e:
        print(f"[ERROR] '{filepath}' read karne me error: {e}")
        return []

def get_account_credential_string(acc):
    return f"uid={acc['uid']}&password={acc['password']}"

# ============================================================
#   CRYPTO HELPERS
# ============================================================
def _pad(data: bytes) -> bytes:
    pad_len = AES.block_size - (len(data) % AES.block_size)
    return data + bytes([pad_len] * pad_len)

def aes_encrypt(plaintext: bytes) -> bytes:
    cipher = AES.new(MAIN_KEY, AES.MODE_CBC, MAIN_IV)
    return cipher.encrypt(_pad(plaintext))

# ============================================================
#   STEP 1 — GARENA OAUTH
# ============================================================
async def fetch_access_token(credential_str: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = credential_str + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    
    access_token = data.get("access_token", "")
    open_id = data.get("open_id", "")
    
    if not access_token:
        raise ValueError("Access token nahi mila!")
    return access_token, open_id

# ============================================================
#   STEP 2 — FF LOGIN (JWT BEARER)
# ============================================================
async def fetch_jwt_for_account(acc):
    cred_str = get_account_credential_string(acc)
    access_token, open_id = await fetch_access_token(cred_str)

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

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, data=encrypted, headers=headers)
        resp.raise_for_status()

    login_res = FreeFire_pb2.LoginRes()
    login_res.ParseFromString(resp.content)
    msg = json.loads(json_format.MessageToJson(login_res))

    token = msg.get("token", "")
    if not token:
        raise ValueError("JWT token nahi mila!")

    # NAYA FIX: Sirf raw token return karega, format Vercel `tokens.json` ke liye ready hai
    return {"token": token}

# ============================================================
#   MAIN RUNNER
# ============================================================
if __name__ == "__main__":
    import sys
    # Tum chaho toh limit laga sakte ho (e.g., `python token_generator.py 10`)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None

    async def run():
        print("=" * 50)
        print("  ROLEX TOKEN GENERATOR (FOR VERCEL)")
        print("=" * 50)

        accs = load_accounts()
        if not accs:
            print("\n[FAIL] uidpass.json nahi mila ya khaali hai!")
            return

        target = accs[:limit] if limit else accs
        print(f"\nTarget accounts: {len(target)}")
        print("-" * 50)

        success = 0
        fail = 0
        results = []

        for i, acc in enumerate(target, 1):
            print(f"\n[{i}/{len(target)}] UID: {acc['uid']} ka token bana raha hoon...")
            try:
                info = await fetch_jwt_for_account(acc)
                results.append(info)
                success += 1
                print(f"  [OK] Token Generated Successfully!")
            except Exception as e:
                fail += 1
                print(f"  [FAIL] {e}")

        print("\n" + "=" * 50)
        print(f"  RESULT: {success} OK | {fail} Failed")
        print("=" * 50)

        if results:
            # NAYA FIX: Directly `tokens.json` mein save karega
            with open("tokens.json", "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            print(f"\n[SAVED] {len(results)} Tokens successfully saved in 'tokens.json'!")
            print("👉 Ab is tokens.json file ko Vercel/Github par upload kar do.")
        else:
            print("\n[FAIL] Koi token generate nahi hua.")

    asyncio.run(run())
