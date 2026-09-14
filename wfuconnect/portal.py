"""潍坊学院校园网(gwifi 门户)认证客户端。

协议逆向自 source/login.html：
  * 明文是 jQuery serialize() 的结果（整个 frmLogin 表单）；
  * AES-128-CBC + ZeroPadding 加密，密钥硬编码为 1234567887654321（login.html:319）；
  * IV 是页面隐藏域 iv 中由服务端下发的 16 位十六进制字符串，
    JS 按 UTF-8 文本解析成 16 字节后直接使用（login.html:317、320）；
  * 最终 POST 的 body 为 data=<Base64>&iv=<明文iv>。

因此每次登录都必须先 GET 登录页，从页面里取当次的 iv 与 sign。
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests
from Crypto.Cipher import AES

log = logging.getLogger("wfu.portal")

# --- 协议常量 -------------------------------------------------------------
KEY = b"1234567887654321"          # login.html:319，AES-128 密钥
BLOCK = AES.block_size             # 16
DEFAULT_BASE_URL = "http://210.44.64.60"
LOGIN_PATH = "/gportal/web/login"
AUTH_PATH = "/gportal/web/authLogin"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.6778.140 Safari/537.36"
)

# 门户自带的在线检测接口，resultCode == 0 表示设备已在线
ONLINE_TEST_URL = "http://nettest.gwifi.com.cn"
# 备用探测：多家厂商的 captive portal 检测地址，返回 204 即网络通路正常
FALLBACK_PROBES = (
    "http://connect.rom.miui.com/generate_204",
    "http://wifi.vivo.com.cn/generate_204",
    "http://www.gstatic.com/generate_204",
)

# 门户返回的失败原因码 -> 说明（login.html:394-440）
REASON_TEXT = {
    "30": "账号不存在或未开通",
    "32": "需要先设置密码（忘记/设置密码入口）",
    "40": "需要先完善个人信息",
    "41": "需要先绑定手机号",
    "43": "MAC 地址变更，需要重新绑定",
}


# --- 编码工具（对齐前端行为） ---------------------------------------------
def encode_component(value: object) -> str:
    """等价于 JS 的 encodeURIComponent()。"""
    return quote(str(value), safe="!'()*-._~")


def form_encode(items: list[tuple[str, object]]) -> str:
    """等价于 jQuery 的表单序列化，空格编码为加号。"""
    return "&".join(
        f"{encode_component(k)}={encode_component(v).replace('%20', '+')}"
        for k, v in items
    )


# --- 加解密 ---------------------------------------------------------------
def aes_cbc_encrypt(plain: str, iv: str) -> str:
    """AES-128-CBC + ZeroPadding，返回 Base64。"""
    raw = plain.encode("utf-8")
    raw += b"\x00" * (-len(raw) % BLOCK)
    cipher = AES.new(KEY, AES.MODE_CBC, iv.encode("utf-8"))
    return base64.b64encode(cipher.encrypt(raw)).decode("ascii")


def aes_cbc_decrypt(payload: str, iv: str) -> str:
    """解密门户密文（调试/自检用），去掉尾部零填充。"""
    raw = AES.new(KEY, AES.MODE_CBC, iv.encode("utf-8")).decrypt(
        base64.b64decode(payload)
    )
    return raw.rstrip(b"\x00").decode("utf-8", "replace")


# --- 页面解析 -------------------------------------------------------------
_FORM_RE = re.compile(r'<form[^>]+id="frmLogin".*?</form>', re.S | re.I)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')


class PortalError(Exception):
    """无法获取或解析登录页等致命错误。"""


def parse_login_form(html: str) -> tuple[list[tuple[str, str]], str]:
    """从登录页 HTML 中取出 frmLogin 表单的字段（含 iv）。

    返回 (items, iv)，items 保持 DOM 顺序，可直接交给 form_encode()。
    """
    match = _FORM_RE.search(html)
    if not match:
        raise PortalError("登录页中未找到 frmLogin 表单，门户结构可能已变更")
    items: list[tuple[str, str]] = []
    for tag in _INPUT_RE.findall(match.group(0)):
        attrs = dict(_ATTR_RE.findall(tag))
        name = attrs.get("name")
        if not name:
            continue
        if attrs.get("type", "text").lower() == "checkbox" and "checked" not in tag:
            continue
        # 账号与密码输入框在页面里没有 value 属性，由调用方填入
        if name in ("name", "password") and "value" not in attrs:
            continue
        items.append((name, attrs.get("value", "")))
    iv = next((v for k, v in items if k == "iv"), "")
    if not re.fullmatch(r"[0-9A-Za-z]{16}", iv):
        raise PortalError(f"未取到合法的 iv（实际为 {iv!r}）")
    return items, iv


# --- 客户端 ---------------------------------------------------------------
@dataclass
class LoginResult:
    ok: bool
    info: str = ""
    reason: str = ""
    redirect_url: str = ""
    raw: dict | None = None

    def __str__(self) -> str:
        flag = "成功" if self.ok else "失败"
        extra = ""
        if self.reason:
            text = REASON_TEXT.get(self.reason, "")
            extra = f"（reasoncode={self.reason} {text}）"
        return f"认证{flag}: {self.info}{extra}"


class PortalClient:
    """校园网门户认证客户端（自带会话，自动复用 PHPSESSID）。"""

    def __init__(
        self,
        username: str = "",
        password: str = "",
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}{LOGIN_PATH}",
                "Connection": "keep-alive",
            }
        )

    # -- 页面 ---------------------------------------------------------------
    def fetch_login_form(self) -> tuple[list[tuple[str, str]], str]:
        """抓取登录页并解析出表单字段与本次的 iv。"""
        resp = self.session.get(f"{self.base_url}{LOGIN_PATH}", timeout=self.timeout)
        resp.raise_for_status()
        items, iv = parse_login_form(resp.text)
        if not self.session.cookies.get("PHPSESSID"):
            log.debug("登录页未下发 PHPSESSID，仍然继续尝试")
        return items, iv

    def build_payload(self, items, iv) -> dict[str, str]:
        """拼装明文 -> 加密 -> 返回可直接 POST 的 data 与 iv。"""
        plain = form_encode(
            items + [("name", self.username), ("password", self.password)]
        )
        return {"data": aes_cbc_encrypt(plain, iv), "iv": iv}

    # -- 认证 ---------------------------------------------------------------
    def login(self) -> LoginResult:
        """执行一次完整登录；每次都会重新取页面，以获得新的 iv 与 sign。"""
        items, iv = self.fetch_login_form()
        payload = self.build_payload(items, iv)
        url = f"{self.base_url}{AUTH_PATH}?round={random.randint(0, 1000)}"
        resp = self.session.post(
            url,
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            raise PortalError(f"认证响应不是 JSON：{resp.text[:200]!r}") from exc

        status = body.get("status")
        data = body.get("data")
        if not isinstance(data, dict):
            data = {}
        result = LoginResult(
            ok=status == 1,
            info=str(body.get("info", "")).strip(),
            reason=str(data.get("reasoncode", "")),
            redirect_url=str(data.get("redirectUrl", "")),
            raw=body,
        )
        log.info("登录结果: %s", result)
        return result

    # -- 在线检测 -----------------------------------------------------------
    def is_online(self, timeout: float | None = None) -> bool:
        """判断当前是否已联网（断线时网关会劫持 HTTP 请求返回门户页）。"""
        timeout = timeout or self.timeout
        try:
            resp = self.session.get(ONLINE_TEST_URL, timeout=timeout)
            if resp.status_code == 200 and not resp.history:
                return int(resp.json().get("resultCode", -1)) == 0
        except (requests.RequestException, ValueError):
            pass

        for url in FALLBACK_PROBES:
            try:
                resp = self.session.get(url, timeout=timeout, allow_redirects=False)
                if resp.status_code == 204:
                    return True
            except requests.RequestException:
                continue
        return False


def _selftest() -> None:
    """加解密自检：验证填充、块对齐与往返一致性。"""
    iv = "753a697fa4a6e386"
    plain = "nasName=WFXY&pid=20&iv=" + iv + "&name=test&password=p@ss+word"
    cipher = aes_cbc_encrypt(plain, iv)
    assert len(base64.b64decode(cipher)) % BLOCK == 0, "密文必须是 16 字节整数倍"
    assert aes_cbc_decrypt(cipher, iv) == plain, "往返解密不一致"
    # 编码行为须与前端一致：百分号要变成 %25，空格要变成加号
    items = [("sign", "a%2Bb%3D%3D"), ("k", "a b"), ("name", "测试*1")]
    expect = "sign=a%252Bb%253D%253D&k=a+b&name=%E6%B5%8B%E8%AF%95*1"
    assert form_encode(items) == expect, form_encode(items)
    print("selftest ok")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    _selftest()
