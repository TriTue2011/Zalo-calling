"""Bo goi CHUNG cho cac API @softtynet/zca-js (danh muc zca_registry.json, sinh
bang tools/gen_zca_registry.py tu chinh ma nguon cua no).

Cung duong day voi services.py: params = JSON ma hoa AES bang khoa phien zpw_enk,
GET (params trong query) hoac POST (params trong form), zpw_ver/zpw_type chung,
phan hoi hai lop (Services._unwrap). Domain lay tu services map server tra luc dang
nhap (session["service_map"][<dich vu>][0]); thieu thi dung domain co dinh cua zca
(vi du ZCLOUD_FALLBACK) — khong doan domain khac.

Che do cua tung API (xem tools/gen_zca_registry.py):
  template  goi bang cac truong nhap (ten/kieu lay tu chu ky ham zca);
  raw       co bieu thuc phuc tap: truyen san `params` (JSON dung nhu zca gui);
  manual    khong goi tu dong — loi ro rang voi ly do.
Moi API la CHUA XAC MINH voi may chu that cho den khi duoc thu (co co `unverified`).
API co ten nguy hiem (xoa/roi/chan...) can `confirm: true`.
"""

import json
import os
import re
import time
import urllib.parse

from . import protocol

_REG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "zca_registry.json")
_CACHE = {}


class ZcaError(ValueError):
    """Loi nhap/che do (400)."""


class ZcaManual(Exception):
    def __init__(self, name, reason):
        super().__init__(reason)
        self.name, self.reason = name, reason


def _snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def registry():
    """{ten_zca: muc} + tra cuu theo snake_case."""
    if "reg" not in _CACHE:
        with open(_REG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        by = {}
        for e in data["apis"]:
            by[e["name"]] = e
            by.setdefault(_snake(e["name"]), e)
        _CACHE["reg"] = by
        _CACHE["raw"] = data
    return _CACHE["reg"]


def catalog():
    """Danh muc gon cho UI/custom: khong kem bieu thuc ben trong."""
    registry()
    out = []
    for e in _CACHE["raw"]["apis"]:
        out.append({"name": e["name"], "action": _snake(e["name"]),
                    "domain": e["domain"], "summary": e["summary"],
                    "mode": e["mode"], "danger": e["danger"],
                    "covered_by": e.get("covered_by", ""),
                    "method": e.get("method", ""),
                    "args": e.get("args", []), "reason": e.get("reason", ""),
                    "unverified": True})
    return {"apis": out, "total": len(out)}


def _coerce(value, typ):
    t = (typ or "").replace(" ", "")
    if isinstance(value, str):
        v = value.strip()
        if t.startswith("number") or t in ("int", "float"):
            try:
                return int(v) if re.fullmatch(r"-?\d+", v) else float(v)
            except ValueError:
                raise ZcaError("khong phai so: %r" % value)
        if t.startswith("boolean"):
            return v.lower() in ("1", "true", "yes", "on")
        if t.endswith("[]") or t.startswith("Array"):
            if v.startswith("["):
                return json.loads(v)
            return [p.strip() for p in v.split(",") if p.strip()]
        if v[:1] in "{[" and not t.startswith("string"):
            try:
                return json.loads(v)
            except ValueError:
                pass
    return value


def _split_top(text):
    """Tach `a: T = 1, b: Record<string, number>` theo dau phay o MUC NGOAI CUNG (bo qua phay trong <>, (), [], {} va chuoi)."""
    parts, depth, cur, quote = [], 0, [], ""
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "<([{":
            depth += 1
        elif ch in ">)]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur))
    return parts


_LITERAL_NUM = re.compile(r"^-?\d+(?:\.\d+)?$")


def signature_defaults(signature):
    """Gia tri mac dinh don gian (so, true/false, chuoi) trong chu ky ham cua zca-js, vi du
    `api.friend.getAliasListV2(page: number = 1, count: number = 100)` -> {"page": 1, "count": 100}.
    zca-js tu ap cac mac dinh nay truoc khi gui; bo goi chung PHAI ap giong vay, khong thi tham so bo trong khong duoc gui va Zalo tra
    114 / 111 «tham so khong hop le / thieu» (do that 14/11/2026: getAliasList, getPhonebookV2, getDefaultGuggyGifs…). Mac dinh phuc tap
    (doi tuong `= {}`, bieu thuc) bo qua."""
    m = re.search(r"\((.*)\)\s*$", signature or "", re.S)
    out = {}
    for part in _split_top(m.group(1) if m else ""):
        m2 = re.match(r"\s*(\w+)\??\s*:\s*.+?=\s*(.+?)\s*$", part, re.S)
        if not m2:
            continue
        name, lit = m2.group(1), m2.group(2)
        if _LITERAL_NUM.match(lit):
            out[name] = float(lit) if "." in lit else int(lit)
        elif lit in ("true", "false"):
            out[name] = lit == "true"
        elif len(lit) >= 2 and lit[0] == lit[-1] and lit[0] in "\"'":
            out[name] = lit[1:-1]
    return out


def _eval(expr, inputs, args, ctx):
    kind = expr[0]
    if kind == "lit":
        return expr[1]
    if kind == "now":
        return int(time.time() * 1000)
    if kind == "ctx":
        return ctx.get(expr[1])
    name = expr[1]
    val = inputs.get(name)
    if val == "":
        val = None
    typ = (args.get(name) or {}).get("type", "")
    if kind == "arg":
        if val is None:
            if len(expr) > 2:
                return expr[2]
            return (args.get(name) or {}).get("sig_default")       # mac dinh trong chu ky zca-js (xem signature_defaults)
        return _coerce(val, typ)
    if val is None:
        return None
    if kind == "bool01":
        return 1 if _coerce(val, "boolean") else 0
    if kind == "str":
        return str(val)
    if kind == "num":
        return _coerce(val, "number")
    if kind == "json":
        return json.dumps(val if not isinstance(val, str) else _coerce(val, ""),
                          separators=(",", ":"), ensure_ascii=False)
    if kind == "join":
        return ",".join(val) if isinstance(val, (list, tuple)) \
            else str(val)
    raise ZcaError("bieu thuc khong ho tro: %r" % (expr,))


def _thread_type_num(value):
    """type cua Zalo: 0 = nguoi dung (1-1), 1 = nhom. Nhan so hoac chu user/group."""
    if value in (None, ""):
        return 0
    v = str(value).strip().lower()
    if v in ("0", "user"):
        return 0
    if v in ("1", "group"):
        return 1
    raise ZcaError("type phai la 0/user (1-1) hoac 1/group (nhom)")


def _translate_params(entry, inputs):
    """Tham so dich cua Zalo AI (translateMessage 13397 / translateMessageSuggest 13399), dung nhu ban fork @softtynet/zca-js
    2.3.13 gui: {reqId: pcw_<ms>, [msgId], threadId, type, srcLang, desLang, msg: {text, metadata: {immutableItems}}}.
    translateMessageAuto la lo nhieu tin (msgs) nen van truyen `params`."""
    manual = entry["name"] == "translateMessage"
    need = ("msgId",) * manual + ("threadId", "srcLang", "desLang", "text")
    missing = [n for n in need if inputs.get(n) in (None, "")]
    if missing:
        raise ZcaError("thieu truong bat buoc: %s" % ", ".join(missing))
    items = inputs.get("immutableItems")
    if isinstance(items, str) and items.strip():
        try:
            items = json.loads(items)
        except ValueError:
            raise ZcaError("immutableItems phai la danh sach JSON")
    if items is not None and not isinstance(items, list):
        raise ZcaError("immutableItems phai la danh sach")
    out = {"reqId": str(inputs.get("reqId") or "pcw_%d" % int(time.time() * 1000))}
    if manual:
        out["msgId"] = str(inputs["msgId"])
    out.update({"threadId": str(inputs["threadId"]), "type": _thread_type_num(inputs.get("type")),
                "srcLang": str(inputs["srcLang"]), "desLang": str(inputs["desLang"]),
                "msg": {"text": str(inputs["text"]), "metadata": {"immutableItems": items or []}}})
    return out


_RAW_BUILDERS = {"translateMessage": _translate_params, "translateMessageSuggest": _translate_params}


def build_params(entry, inputs, ctx):
    """Dung params gui di. `params` (neu co) la JSON nguyen khoi, THANG THE."""
    args = {a["name"]: dict(a) for a in entry.get("args", [])}
    for _n, _v in signature_defaults(entry.get("signature")).items():
        if _n in args:
            args[_n]["sig_default"] = _v
    raw = inputs.get("params")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ZcaError("params phai la JSON")
    if isinstance(raw, dict) and raw:
        return raw
    missing = [n for n, a in args.items()
               if a.get("required") and inputs.get(n) in (None, "")]
    if entry["mode"] == "raw" and entry["name"] in _RAW_BUILDERS:
        return _RAW_BUILDERS[entry["name"]](entry, inputs)
    if entry["mode"] == "raw":
        raise ZcaError(
            "%s co bieu thuc phuc tap (%s): truyen san `params` (JSON nhu zca-js gui)"
            % (entry["name"], ", ".join(entry.get("complex", []))[:80]))
    if missing:
        raise ZcaError("thieu truong bat buoc: %s" % ", ".join(missing))
    out = {}
    for key, expr in (entry.get("params") or {}).items():
        v = _eval(expr, inputs, args, ctx)
        if v is not None:
            out[key] = v
    return out


class ZcaApi:
    def __init__(self, svc, ctx=None):
        self.svc = svc                      # services.Services
        self.ctx = {"language": "vi", **(ctx or {})}

    # Domain cua app Zalo PC (da chay that, xem protocol.py) UU TIEN hon services map
    # trong phien: map cua phien tra ten "tt-*-wpa.zalo.me" khong phan giai duoc o
    # nhieu mang (do 09/11 tren .200), con domain PC thi chay.
    _PC_DOMAINS = {"chat": "CHAT_DOMAIN", "group": "GROUP_DOMAIN",
                   "profile": "PROFILE_DOMAIN", "friend": "FRIEND_DOMAIN",
                   "alias": "ALIAS_DOMAIN", "label": "LABEL_DOMAIN",
                   "group_board": "BOARD_DOMAIN", "sticker": "STICKER_DOMAIN",
                   "file": "FILE_DOMAIN", "conversation": "CONVERS_DOMAIN",
                   "voice_call": "VOICECALL_DOMAIN"}

    def _domain(self, entry):
        pc = getattr(protocol, self._PC_DOMAINS.get(entry.get("service") or "", ""), None)
        if pc:
            return pc
        sm = getattr(self.svc, "service_map", None) or {}
        raw = sm.get(entry.get("service") or "")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        if not raw:
            raw = entry.get("fixed_domain")
        if not raw:
            raise ZcaManual(entry["name"],
                            "domain %r chua co trong services map (dang nhap lai de "
                            "lay map) va zca khong co domain du phong"
                            % (entry.get("service"),))
        raw = str(raw).strip()
        if "://" not in raw:
            raw = "https://" + raw
        return raw.rstrip("/")

    def raw_call(self, service, path, params, method="GET", fixed_domain=None):
        """Goi mot endpoint zca-js theo (dich vu, duong dan) khi API do la ham nhieu buoc
        (vd. getStickersDetail) — van cung duong day ma hoa/giai ma."""
        entry = {"name": path, "service": service, "path": path,
                 "fixed_domain": fixed_domain}
        domain = self._domain(entry)
        common = dict(protocol.common_params())
        enc = self.svc._aes(json.dumps(params, separators=(",", ":"),
                                       ensure_ascii=False))
        if method == "POST":
            url = "%s%s?%s" % (domain, path, urllib.parse.urlencode(common))
            status, payload = self.svc.http_post(
                url, "params=" + urllib.parse.quote(enc, safe=""), timeout=30)
        else:
            common["params"] = enc
            url = "%s%s?%s" % (domain, path, urllib.parse.urlencode(common))
            status, payload = self.svc.http_get(url, timeout=30)
        return self.svc._unwrap(status, payload, "%s %s" % (method, path))

    def call(self, name, inputs=None):
        inputs = dict(inputs or {})
        entry = registry().get(name) or registry().get(_snake(name))
        if entry is None:
            raise KeyError(name)
        if entry["mode"] == "manual":
            raise ZcaManual(entry["name"], entry.get("reason") or "chua ho tro")
        if entry["danger"] and not inputs.get("confirm") in (True, "true", "1", 1):
            raise ZcaError("%s la thao tac nguy hiem: them confirm: true de chay"
                           % entry["name"])
        params = build_params(entry, inputs, self.ctx)
        domain = self._domain(entry)
        common = dict(protocol.common_params())
        common.update(entry.get("query") or {})
        svc = self.svc
        path = entry["path"]
        if entry.get("encrypted", True):
            enc = svc._aes(json.dumps(params, separators=(",", ":"),
                                      ensure_ascii=False))
            if entry.get("method") == "POST":
                url = "%s%s?%s" % (domain, path, urllib.parse.urlencode(common))
                form = "params=" + urllib.parse.quote(enc, safe="")
                if svc.http_post is None:
                    raise RuntimeError("thieu transport POST")
                status, payload = svc.http_post(url, form, timeout=30)
            else:
                common["params"] = enc
                url = "%s%s?%s" % (domain, path, urllib.parse.urlencode(common))
                status, payload = svc.http_get(url, timeout=30)
        else:
            q = {**common, **{k: v for k, v in params.items()
                              if isinstance(v, (str, int, float))}}
            url = "%s%s?%s" % (domain, path, urllib.parse.urlencode(q))
            status, payload = svc.http_get(url, timeout=30)
        return svc._unwrap(status, payload, "%s %s" % (entry.get("method", "GET"), path))
