# Local Auth Testing (Frag Server)

The frag server no longer has its own account system. All user identity is
brokered through PD's login-broker API using a single API key.

## 1) Install dependencies

```powershell
cd c:\stuffs\pdsitestuffs\frag\server
python -m pip install -r requirements.txt
```

## 2) Get a PD API key

1. Make sure your PD account has the **APIAccess** permission (ask a PD admin).
2. Sign in at <https://pixelateddream.net> and visit `/account/apps`.
3. Click "New app", call it something like "Frag server (dev)", and copy the
   `pd_...` key shown on the detail page. (The raw key is shown exactly once.)

## 3) Run the server

```powershell
$env:FRAG_PD_API_KEY = "pd_..."
$env:FRAG_PD_BASE_URL = "https://pixelateddream.net"  # optional override
$env:FRAG_USER_DATA_ROOT = "userdata"                  # optional override
python flask_app.py
```

Or against a local pdsite dev instance:

```powershell
$env:FRAG_PD_BASE_URL = "http://localhost:8000"
python flask_app.py
```

## 4) Run tests

```powershell
$env:FRAG_PD_API_KEY = "pd_test_dummy"   # tests mock PDClient, but the env var check still runs
python -m unittest discover -s tests -p "test_*.py" -v
```

## Auth flow at a glance

```text
client → POST /frag/v1/auth/start
       → server calls PD login-url with its API key
       ← {handle, authorize_url}
client opens authorize_url in browser, user clicks Allow
client → GET /frag/v1/auth/poll?handle=...
       → server polls PD login-result
       ← {session_token, user_id, expires_at}
client → uses Authorization: Bearer <session_token> for all later calls
       → server caches session 5 min, then re-validates via PD
```

## Env vars

| Var                    | Required | Default                          | Purpose                                  |
| ---------------------- | -------- | -------------------------------- | ---------------------------------------- |
| `FRAG_PD_API_KEY`      | yes      | —                                | App API key from `/account/apps`         |
| `FRAG_PD_BASE_URL`     | no       | `https://pixelateddream.net`     | PD base URL (override for local dev)     |
| `FRAG_USER_DATA_ROOT`  | no       | `./userdata`                     | Per-user file storage root               |
| `FRAG_SESSION_CACHE`   | no       | `<userdata>/.sessions.json`      | Local session cache file                 |
| `FRAG_MAX_FILE_BYTES`  | no       | 2 GB                             | Max upload size                          |
| `PORT`                 | no       | `4543`                           | Listen port                              |
