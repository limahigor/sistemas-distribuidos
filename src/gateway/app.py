import os
import time
import json
import logging
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urljoin, urlencode

import jwt  # pyjwt
import requests
from flask import Flask, request, jsonify, make_response, Response

from concurrent.futures import ThreadPoolExecutor

AUTH_BASE = os.getenv("AUTH_BASE", "http://localhost:7001/")
PATIENTS_BASE = os.getenv("PATIENTS_BASE", "http://localhost:7002/")
RECORDS_BASE = os.getenv("RECORDS_BASE", "http://localhost:7003/")
SCHEDULING_BASE = os.getenv("SCHEDULING_BASE", "http://localhost:7004/")

JWT_ALG = os.getenv("JWT_ALG", "HS256")
JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME")
JWT_AUDIENCE = os.getenv("JWT_AUD", "emr-gateway")
JWT_ISSUER = os.getenv("JWT_ISS", "auth-service")

# Timeout e resiliência
UPSTREAM_TIMEOUT = float(os.getenv("UPSTREAM_TIMEOUT", "5.0"))
RETRY_COUNT = int(os.getenv("RETRY_COUNT", "1"))

# Rate limit, N req/min por IP ou usuário
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "120"))

# Logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("api-gateway")

AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "/tmp/emr_audit.log")


def audit_log(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Audit log write failed: {e}")


app = Flask(__name__)


# =========================
# CORS
# ========================


@app.before_request
def _handle_cors_preflight():
    if request.method == "OPTIONS":
        return ("", 204)


@app.after_request
def apply_cors(resp: Response):
    resp.headers["Access-Control-Allow-Origin"] = os.getenv("CORS_ALLOW_ORIGIN", "*")
    resp.headers["Access-Control-Allow-Headers"] = (
        "Authorization,Content-Type,Idempotency-Key"
    )
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
    resp.headers["Access-Control-Max-Age"] = "86400"
    resp.headers["Vary"] = "Origin"
    return resp


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok", "service": "emr-api-gateway"}, 200


# =========================
# Rate limit
# =========================
_rate_buckets = {}


def rate_limited(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        user_key = "anon"
        if auth.startswith("Bearer "):
            try:
                payload = jwt.decode(
                    auth.split()[1],
                    JWT_SECRET,
                    algorithms=[JWT_ALG],
                    audience=JWT_AUDIENCE,
                    options={"verify_exp": False},
                    issuer=JWT_ISSUER,
                )
                user_key = payload.get("sub", "anon")
            except Exception:
                pass

        key = f"{user_key}:{request.remote_addr}"
        now = int(time.time())
        window = now // 60  # min

        win_count = _rate_buckets.get(key, (window, 0))
        if win_count[0] != window:
            _rate_buckets[key] = (window, 0)
            win_count = _rate_buckets[key]
        if win_count[1] >= RATE_LIMIT_RPM:
            return jsonify(
                {"error": "rate_limited", "detail": "Too many requests"}
            ), 429

        _rate_buckets[key] = (window, win_count[1] + 1)
        return func(*args, **kwargs)

    return wrapper


# =========================
# JWT / RBAC
# =========================
def _decode_jwt(token: str):
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALG],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
    )


def jwt_required(scopes=None, roles=None):
    scopes = set(scopes or [])
    roles = set(roles or [])

    def deco(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return jsonify({"error": "unauthorized"}), 401
            token = auth.split()[1]
            try:
                payload = _decode_jwt(token)
            except Exception as e:
                logger.warning(f"JWT invalid: {e}")
                return jsonify({"error": "unauthorized"}), 401

            req_scopes = set(payload.get("scopes", []))
            req_roles = set(payload.get("roles", []))

            if scopes and not scopes.issubset(req_scopes):
                return jsonify({"error": "forbidden", "detail": "missing scopes"}), 403
            if roles and roles.isdisjoint(req_roles):
                return jsonify({"error": "forbidden", "detail": "missing role"}), 403

            request.user = {
                "sub": payload.get("sub"),
                "roles": list(req_roles),
                "scopes": list(req_scopes),
            }
            return func(*args, **kwargs)

        return wrapper

    return deco


# =========================
# Util: forward para upstream
# =========================
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _filtered_headers():
    headers = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP or lk == "host" or lk == "content-length":
            continue
        headers[k] = v
    if getattr(request, "user", None):
        headers["X-User-Id"] = request.user["sub"]
        headers["X-User-Roles"] = ",".join(request.user["roles"])
        headers["X-User-Scopes"] = ",".join(request.user["scopes"])
    return headers


def _request_upstream(base: str, path_suffix=""):
    url = urljoin(base, path_suffix)
    if request.query_string:
        url = f"{url}?{request.query_string.decode('utf-8')}"
    data = request.get_data()
    headers = _filtered_headers()

    last_exc = None
    for attempt in range(RETRY_COUNT + 1):
        try:
            resp = requests.request(
                method=request.method,
                url=url,
                headers=headers,
                data=data,
                timeout=UPSTREAM_TIMEOUT,
                allow_redirects=False,
            )
            flask_resp = make_response(resp.content, resp.status_code)
            for k, v in resp.headers.items():
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-length":
                    flask_resp.headers[k] = v
            return flask_resp
        except requests.RequestException as e:
            last_exc = e
            logger.warning(f"Upstream attempt {attempt + 1} failed: {e}")
            time.sleep(0.1 * (attempt + 1))
    return jsonify({"error": "bad_gateway", "detail": str(last_exc)}), 502


# =========================
#           Auth
# =========================
@app.route("/auth/login", methods=["POST", "OPTIONS"])
@rate_limited
def auth_login():
    audit_log({"actor": "anonymous", "action": "auth_login_attempt"})
    return _request_upstream(AUTH_BASE, "auth/login")


@app.route("/auth/refresh", methods=["POST", "OPTIONS"])
@rate_limited
def auth_refresh():
    return _request_upstream(AUTH_BASE, "auth/refresh")


# =========================
#       Patients
# =========================
@app.route("/patients", methods=["GET", "POST", "OPTIONS"])
@rate_limited
@jwt_required(
    scopes=["patients:read"], roles={"MEDICO", "ENFERMEIRO", "RECEPCIONISTA", "ADMIN"}
)
def patients_collection():
    if request.method == "POST":
        if "patients:write" not in request.user["scopes"]:
            return jsonify(
                {"error": "forbidden", "detail": "patients:write required"}
            ), 403
        audit_log({"actor": request.user["sub"], "action": "patient_create"})
    return _request_upstream(PATIENTS_BASE, "patients")


@app.route("/patients/<path:suffix>", methods=["GET", "PUT", "DELETE", "OPTIONS"])
@rate_limited
@jwt_required(
    scopes=["patients:read"], roles={"MEDICO", "ENFERMEIRO", "RECEPCIONISTA", "ADMIN"}
)
def patients_resource(suffix):
    if request.method in ("PUT", "DELETE"):
        if "patients:write" not in request.user["scopes"]:
            return jsonify(
                {"error": "forbidden", "detail": "patients:write required"}
            ), 403
        audit_log(
            {
                "actor": request.user["sub"],
                "action": f"patient_{request.method.lower()}",
                "target": suffix,
            }
        )
    return _request_upstream(PATIENTS_BASE, f"patients/{suffix}")


# =========================
#       Prontuários
# =========================
@app.route("/records", methods=["GET", "POST", "OPTIONS"])
@rate_limited
@jwt_required(scopes=["records:read"], roles={"MEDICO", "ENFERMEIRO", "ADMIN"})
def records_collection():
    if request.method == "POST":
        if "records:write" not in request.user["scopes"]:
            return jsonify(
                {"error": "forbidden", "detail": "records:write required"}
            ), 403
        audit_log({"actor": request.user["sub"], "action": "record_create"})
    return _request_upstream(RECORDS_BASE, "records")


@app.route(
    "/records/<path:suffix>", methods=["GET", "PUT", "PATCH", "DELETE", "OPTIONS"]
)
@rate_limited
@jwt_required(scopes=["records:read"], roles={"MEDICO", "ENFERMEIRO", "ADMIN"})
def records_resource(suffix):
    if request.method in ("PUT", "PATCH", "DELETE"):
        if "records:write" not in request.user["scopes"]:
            return jsonify(
                {"error": "forbidden", "detail": "records:write required"}
            ), 403
        audit_log(
            {
                "actor": request.user["sub"],
                "action": f"record_{request.method.lower()}",
                "target": suffix,
            }
        )
    return _request_upstream(RECORDS_BASE, f"records/{suffix}")


# =========================
#       Scheduling
# =========================
_idem_cache = set()


def require_idempotency_for_post(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if request.method == "POST":
            key = request.headers.get("Idempotency-Key")
            if not key:
                return jsonify({"error": "idempotency_required"}), 400
            if key in _idem_cache:
                return jsonify(
                    {"status": "duplicate", "detail": "already processed"}
                ), 409
            _idem_cache.add(key)
        return func(*args, **kwargs)

    return wrapper


@app.route("/appointments", methods=["GET", "POST", "OPTIONS"])
@rate_limited
@jwt_required(scopes=["scheduling:read"], roles={"MEDICO", "RECEPCIONISTA", "ADMIN"})
@require_idempotency_for_post
def appointments_collection():
    if request.method == "POST" and "scheduling:write" not in request.user["scopes"]:
        return jsonify(
            {"error": "forbidden", "detail": "scheduling:write required"}
        ), 403
    if request.method == "POST":
        audit_log({"actor": request.user["sub"], "action": "appointment_create"})
    return _request_upstream(SCHEDULING_BASE, "appointments")


@app.route("/appointments/<path:suffix>", methods=["GET", "PUT", "DELETE", "OPTIONS"])
@rate_limited
@jwt_required(scopes=["scheduling:read"], roles={"MEDICO", "RECEPCIONISTA", "ADMIN"})
def appointments_resource(suffix):
    if (
        request.method in ("PUT", "DELETE")
        and "scheduling:write" not in request.user["scopes"]
    ):
        return jsonify(
            {"error": "forbidden", "detail": "scheduling:write required"}
        ), 403
    if request.method in ("PUT", "DELETE"):
        audit_log(
            {
                "actor": request.user["sub"],
                "action": f"appointment_{request.method.lower()}",
                "target": suffix,
            }
        )
    return _request_upstream(SCHEDULING_BASE, f"appointments/{suffix}")


# =========================
#       Agregação
# =========================
@app.route("/patient/<patient_id>/summary", methods=["GET"])


@rate_limited
@jwt_required(
    scopes=["patients:read", "records:read", "scheduling:read"],
    roles={"MEDICO", "ENFERMEIRO", "ADMIN", "RECEPCIONISTA"},
)
def patient_summary(patient_id):
    headers = _filtered_headers()

    def _call(base, path, params=None):
        try:
            url = urljoin(base, path)
            if params:
                url += f"?{urlencode(params)}"
            r = requests.get(url, headers=headers, timeout=UPSTREAM_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    with ThreadPoolExecutor(max_workers=3) as exe:
        f_patient = exe.submit(_call, PATIENTS_BASE, f"patients/{patient_id}")
        f_records = exe.submit(
            _call, RECORDS_BASE, "records", {"patientId": patient_id, "limit": 10}
        )
        f_schedule = exe.submit(
            _call,
            SCHEDULING_BASE,
            "appointments",
            {"patientId": patient_id, "upcoming": "true"},
        )

    result = {
        "patient": f_patient.result(),
        "recentRecords": f_records.result(),
        "upcomingAppointments": f_schedule.result(),
    }
    audit_log(
        {
            "actor": request.user["sub"],
            "action": "patient_summary",
            "target": patient_id,
        }
    )
    return jsonify(result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=False)
