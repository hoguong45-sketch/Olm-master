import os
import time
import uuid
import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")

OLM_BASE = "https://olm.vn"

# Lưu phiên tạm thời trong bộ nhớ (nên chuyển sang SQLite/Redis/DB khi chạy lâu dài)
SESSIONS = {}        # token -> {"username": ..., "cookies": {...}, "created_at": ...}
REFRESH_TOKENS = {}  # rtoken -> {"username": ..., "cookies": {...}}
USERS = {}           # username -> thông tin tài khoản, lượt, gói


def get_token():
    return request.headers.get("X-Token", "").strip()


def get_session():
    tok = get_token()
    if not tok:
        return None
    return SESSIONS.get(tok)


def build_olm_session(cookies=None):
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
        "Referer": OLM_BASE + "/",
    })
    if cookies:
        s.cookies.update(cookies)
    return s


def olm_authenticate(username, password):
    """
    Nối logic đăng nhập thật vào OLM tại đây.
    Trả về (ok: bool, msg: str, cookies: dict).
    """
    s = build_olm_session()
    try:
        s.get(OLM_BASE + "/dang-nhap", timeout=15)
        resp = s.post(
            OLM_BASE + "/dang-nhap",
            data={"username": username, "password": password},
            timeout=15,
            allow_redirects=True,
        )
        if resp.status_code == 200 and "dang-xuat" in resp.text.lower():
            return True, "Đăng nhập thành công", requests.utils.dict_from_cookiejar(s.cookies)
        return False, "Sai tài khoản OLM hoặc OLM yêu cầu xác minh thêm", {}
    except requests.RequestException:
        return False, "Không kết nối được tới OLM", {}


def olm_check_alive(cookies):
    """
    Kiểm tra phiên OLM còn sống hay bị đăng nhập ở nơi khác.
    Trả về (alive: bool, relogin: bool, msg: str).
    """
    if not cookies:
        return False, True, "Phiên OLM đã hết hạn"
    s = build_olm_session(cookies)
    try:
        resp = s.get(OLM_BASE + "/", timeout=10, allow_redirects=False)
        if resp.status_code in (301, 302) and "dang-nhap" in (resp.headers.get("Location") or ""):
            return False, True, "Bạn vừa đăng nhập nơi khác, vui lòng đăng nhập lại"
        return True, False, ""
    except requests.RequestException:
        return True, False, ""


def ensure_user(username):
    if username not in USERS:
        USERS[username] = {
            "uname": username,
            "tier": "free",               # "free" | "vip" | "ultra"
            "remain": 5,
            "exam_remain": 0,
            "total": 5,
            "max_keys": 2,
            "first_verified": 1,
            "is_new_user": False,
            "new_user_bonus_done": True,
            "sub_tier": "",
            "sub_remaining_days": 0,
            "sub_end_date": "",
            "keys_used_today": 0,
            "notifications": [],
        }
    return USERS[username]


@app.route("/")
def serve_index():
    return send_from_directory(".", "1008.html")


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "").strip()
    remember = bool(data.get("remember"))

    if not username or not password:
        return jsonify({"ok": False, "msg": "Vui lòng nhập đầy đủ tên đăng nhập và mật khẩu"})

    ok, msg, cookies = olm_authenticate(username, password)
    if not ok:
        return jsonify({"ok": False, "msg": msg})

    u = ensure_user(username)
    token = uuid.uuid4().hex
    rtoken = uuid.uuid4().hex if remember else ""

    SESSIONS[token] = {
        "username": username,
        "cookies": cookies,
        "created_at": int(time.time()),
    }
    if rtoken:
        REFRESH_TOKENS[rtoken] = {
            "username": username,
            "cookies": cookies,
        }

    return jsonify({
        "ok": True,
        "token": token,
        "rtoken": rtoken,
        "uname": u["uname"],
        "tier": u["tier"],
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
        "total": u["total"],
        "max_keys": u["max_keys"],
        "first_verified": u["first_verified"],
        "is_new_user": u["is_new_user"],
        "new_user_bonus_done": u["new_user_bonus_done"],
        "sub_tier": u["sub_tier"],
        "sub_remaining_days": u["sub_remaining_days"],
        "sub_end_date": u["sub_end_date"],
    })


@app.post("/api/restore")
def api_restore():
    rtok = request.headers.get("X-RToken", "").strip()
    rec = REFRESH_TOKENS.get(rtok)
    if not rec:
        return jsonify({"ok": False, "msg": "Phiên ghi nhớ đăng nhập không tồn tại hoặc đã hết hạn"})

    username = rec["username"]
    u = ensure_user(username)
    token = uuid.uuid4().hex
    SESSIONS[token] = {
        "username": username,
        "cookies": rec.get("cookies") or {},
        "created_at": int(time.time()),
    }
    return jsonify({
        "ok": True,
        "token": token,
        "uname": u["uname"],
        "tier": u["tier"],
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
        "total": u["total"],
        "max_keys": u["max_keys"],
        "first_verified": u["first_verified"],
        "sub_tier": u["sub_tier"],
        "sub_remaining_days": u["sub_remaining_days"],
        "sub_end_date": u["sub_end_date"],
    })


@app.post("/api/logout")
def api_logout():
    tok = get_token()
    rtok = request.headers.get("X-RToken", "").strip()
    if tok:
        SESSIONS.pop(tok, None)
    if rtok:
        REFRESH_TOKENS.pop(rtok, None)
    return jsonify({"ok": True})


@app.get("/api/keep-alive")
def api_keep_alive():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "relogin": True, "msg": "Phiên đăng nhập đã hết hạn"})
    alive, relogin, msg = olm_check_alive(sess.get("cookies"))
    if not alive:
        return jsonify({"ok": False, "relogin": relogin, "msg": msg})
    return jsonify({"ok": True})


@app.post("/api/validate-token")
def api_validate_token():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False})
    u = ensure_user(sess["username"])
    return jsonify({
        "ok": True,
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
        "sub_remaining_days": u["sub_remaining_days"],
        "sub_end_date": u["sub_end_date"],
        "sub_tier": u["sub_tier"],
    })


@app.get("/api/assignments")
def api_assignments():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})

    pages = max(1, min(100, int(request.args.get("pages", 3))))
    mode = request.args.get("mode", "pending")

    # Nối logic quét danh sách bài từ OLM theo pages và mode ("pending" | "all")
    sample_items = []
    return jsonify({"ok": True, "assignments": sample_items, "pages": pages, "mode": mode})


@app.post("/api/solve")
def api_solve():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})

    u = ensure_user(sess["username"])
    body = request.get_json(silent=True) or {}
    assignment = body.get("assignment") or {}
    is_exam = bool(assignment.get("is_kiem_tra") or assignment.get("is_kiem_tra_pdf"))

    if is_exam and u["tier"] != "ultra":
        if (u.get("exam_remain") or 0) <= 0:
            return jsonify({"ok": False, "msg": "Hết lượt bài kiểm tra", "remain": u["remain"], "exam_remain": u["exam_remain"]})
        u["exam_remain"] -= 1
    elif u["tier"] == "free":
        if (u.get("remain") or 0) <= 0:
            return jsonify({"ok": False, "msg": "Hết lượt bài tập", "remain": u["remain"], "exam_remain": u["exam_remain"]})
        u["remain"] -= 1

    # Nối logic giải & nộp bài lên OLM dựa trên assignment và tham số tùy chọn
    return jsonify({
        "ok": True,
        "msg": "Đã giải xong bài",
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
    })


@app.post("/api/exam-info")
def api_exam_info():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})

    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()

    # Nối logic nhận diện dạng bài từ link OLM
    exam_type = "kiem_tra" if "kiem-tra" in url or "de-thi" in url else ("video" if "video" in url else "bai_tap")
    return jsonify({
        "ok": True,
        "exam_type": exam_type,
        "total_questions": 0,
    })


@app.post("/api/solve-link")
def api_solve_link():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})

    u = ensure_user(sess["username"])
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "msg": "Link trống"})

    if u["tier"] == "free":
        if (u.get("remain") or 0) <= 0:
            return jsonify({"ok": False, "msg": "Hết lượt bài tập", "remain": u["remain"], "exam_remain": u["exam_remain"]})
        u["remain"] -= 1

    # Nối logic giải bài theo url và tham số tùy chọn trong body
    return jsonify({
        "ok": True,
        "msg": "Đã xử lý link",
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
    })


@app.post("/api/exam/info")
def api_exam_info_by_id():
    if not get_session():
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    return jsonify({"ok": True, "nq": 0})


@app.get("/api/key/status")
def api_key_status():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False})
    u = ensure_user(sess["username"])
    used = u["keys_used_today"]
    left = max(0, u["max_keys"] - used)
    return jsonify({
        "ok": True,
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
        "keys_used": used,
        "keys_left": left,
    })


@app.post("/api/key/start")
def api_key_start():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    u = ensure_user(sess["username"])
    if u["keys_used_today"] >= u["max_keys"]:
        return jsonify({"ok": False, "msg": "Bạn đã tạo tối đa 2 key hôm nay"})
    return jsonify({"ok": True, "link": "https://olm.vn"})


@app.post("/api/key/verify")
def api_key_verify():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    u = ensure_user(sess["username"])
    key = ((request.get_json(silent=True) or {}).get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "msg": "Vui lòng nhập key"})
    if u["keys_used_today"] >= u["max_keys"]:
        return jsonify({"ok": False, "msg": "Đã hết lượt kích hoạt key hôm nay"})

    u["keys_used_today"] += 1
    u["remain"] = (u.get("remain") or 0) + 5
    return jsonify({
        "ok": True,
        "remain": u["remain"],
        "exam_remain": u["exam_remain"],
    })


@app.post("/api/canva/request")
def api_canva_request():
    if not get_session():
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    email = ((request.get_json(silent=True) or {}).get("email") or "").strip()
    if "@" not in email:
        return jsonify({"ok": False, "msg": "Email không hợp lệ"})
    return jsonify({"ok": True})


@app.post("/api/feedback")
def api_feedback():
    if not get_session():
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    text = ((request.get_json(silent=True) or {}).get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "msg": "Nội dung trống"})
    return jsonify({"ok": True})


@app.post("/api/giftcode/redeem")
def api_giftcode_redeem():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False, "msg": "Chưa đăng nhập"})
    u = ensure_user(sess["username"])
    code = ((request.get_json(silent=True) or {}).get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "msg": "Mã code trống"})
    u["remain"] = (u.get("remain") or 0) + 3
    return jsonify({"ok": True, "bonus": "+3 lượt", "remain": u["remain"]})


@app.get("/api/notifications")
def api_notifications():
    sess = get_session()
    if not sess:
        return jsonify({"ok": False})
    u = ensure_user(sess["username"])
    return jsonify({"ok": True, "notifications": u["notifications"], "unread": 0})


@app.post("/api/notifications/read")
def api_notifications_read():
    if not get_session():
        return jsonify({"ok": False})
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
