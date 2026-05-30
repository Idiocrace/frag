import os
import re
import jwt
import datetime
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from functools import wraps

import requests
from werkzeug.utils import secure_filename
from flask import Flask, request, jsonify, abort, redirect, send_from_directory, g

app = Flask("frag_backend_v1")

SECRET_KEY = os.environ.get("FRAG_SECRET_KEY", "SecretKeyForFrag!!!v1:3anda<3")

USER_DATA_ROOT = Path(os.environ.get("FRAG_USER_DATA_ROOT", "userdata")).resolve()
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB per file
DOWNLOAD_TIMEOUT = 30
USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def user_dir(user_id: str) -> Path:
    if not USER_ID_RE.match(user_id):
        abort(400)
    return USER_DATA_ROOT / user_id


def user_agent_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_agent = request.headers.get("User-Agent")
        if user_agent != "FragModdingClient/v1":
            abort(403)
        return f(*args, **kwargs)

    return wrapper


def token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401

        token = auth_header.split(" ")[1]

        try:
            data = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        g.user = data  # ✅ FIX HERE
        return f(*args, **kwargs)

    return wrapper


def create_token(user_id: str):
    payload = {
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
    }

    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


@app.route("/frag/v1/ping", methods=["POST"])
@user_agent_required
def ping():
    content_type = request.headers.get("Content-Type", "")

    if content_type == "application/json":
        return jsonify({"message": "pong"})

    return "Pong!"


@app.route("/frag/v1/authenticate", methods=["POST", "GET"])
@user_agent_required
def authenticate():
    token = request.args.get("token")

    if not token:
        return jsonify(
            {"error": "Bad Request.", "message": "Missing token parameter."}
        ), 400

    # external validation step
    result = subprocess.run(
        ["python", "auth.py", "validate", token], capture_output=True, text=True
    )

    if result.stdout.strip() != "valid":
        return jsonify({"error": "Unauthorized.", "message": "Invalid token."}), 401

    jwt_token = create_token(token)

    return jsonify({"message": "Authentication successful.", "jwt": jwt_token})


@app.route("/frag/v1/fetch-data", methods=["POST", "GET"])
@user_agent_required
@token_required
def fetch_data():
    user_id = g.user["user_id"]

    return jsonify({"message": "Success", "user_id": user_id})


def _download_zip(url: str, dest_dir: Path) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        abort(400, description=f"Invalid URL: {url}")

    filename = secure_filename(os.path.basename(parsed.path) or "file.zip")
    if not filename.lower().endswith(".zip"):
        filename += ".zip"

    dest = dest_dir / filename

    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as resp:
        resp.raise_for_status()
        total = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    fh.close()
                    dest.unlink(missing_ok=True)
                    abort(
                        413,
                        description=f"File exceeds {MAX_DOWNLOAD_BYTES} bytes: {url}",
                    )
                fh.write(chunk)

    return filename


@app.route("/frag/v1/save-data", methods=["POST"])
@user_agent_required
@token_required
def save_data():
    user_id = g.user["user_id"]
    payload = request.get_json(silent=True) or {}
    links = payload.get("files") or payload.get("links")

    if (
        not isinstance(links, list)
        or len(links) != 2
        or not all(isinstance(u, str) for u in links)
    ):
        return jsonify(
            {
                "error": "Bad Request",
                "message": "Expected JSON body with 'files': [url1, url2].",
            }
        ), 400

    dest_dir = user_dir(user_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    try:
        saved = [_download_zip(url, dest_dir) for url in links]
    except requests.RequestException as exc:
        return jsonify(
            {"error": "Bad Gateway", "message": f"Download failed: {exc}"}
        ), 502

    return jsonify(
        {
            "message": "Data saved successfully",
            "user_id": user_id,
            "files": saved,
        }
    )


@app.route("/frag/v1/files/", methods=["GET"])
@app.route("/frag/v1/files/<path:filename>", methods=["GET"])
@user_agent_required
@token_required
def get_file(filename: str = ""):
    user_id = g.user["user_id"]
    dest_dir = user_dir(user_id)

    if not filename:
        if not dest_dir.is_dir():
            return jsonify({"user_id": user_id, "files": []})
        files = sorted(p.name for p in dest_dir.iterdir() if p.is_file())
        return jsonify({"user_id": user_id, "files": files})

    return send_from_directory(dest_dir, filename, as_attachment=True)


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    from waitress import serve

    port = int(os.environ.get("PORT", 4543))
    serve(app, host="0.0.0.0", port=port)
