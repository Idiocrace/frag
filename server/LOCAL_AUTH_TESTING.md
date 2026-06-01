# Local Auth Testing (Frag Server)

## 1) Install dependencies

```powershell
cd c:\_\frag\server
python -m pip install -r requirements.txt
```

## 2) Run auth tests

```powershell
cd c:\_\frag\server
python -m unittest discover -s tests -p "test_*.py" -v
```

## 3) Optional runtime env

```powershell
$env:PD_OAUTH_URL = "http://localhost:5001"
$env:PD_OAUTH_ISSUER = "http://localhost:5001"
$env:FRAG_OAUTH_AUDIENCE = "frag-desktop"
python flask_app.py
```

## Notes

- The server now validates PD-issued RS256 OAuth access tokens by default.
- Legacy HS256 token support is still available for compatibility and can be disabled with:

```powershell
$env:FRAG_ALLOW_LEGACY_HS256 = "false"
```
