#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy积分助手 - 只读数据抓取 + 自动签到脚本（纯标准库）。

读取本机已登录 WorkBuddy 的登录态，接口直查 Buddy 加油站状态并输出每日播报。
覆盖：积分余额、连续登录天数、当月活跃地图（每日完成一次对话才能被记录登录 1 天）、
本周进度、本期活动信息与是否可领取，解析连续奖励 / 每日可得积分、
活动期全勤预估等衍生洞察，并基于与官方页面一致的连续奖励分档（7 / 14 / 28 节点），
对今日未记录登录主动催办提醒；额外展示成长计划任务 / 进度 / 当前 Buddy（只读 GET 接口，无需 Turing Shield 设备指纹）。
自动签到：可选 `checkin` / `--checkin` 子命令，调用已验证的写接口
`POST /v2/billing/meter/daily-checkin`（接口幂等，当日已签会返回"今天已签到"），
先查后签，避免无谓写请求。
桌面展示：系统通知为默认行为（非 --json 模式每次执行都会尝试弹一次，跨平台兼容：
Windows 用同目录 toast.ps1、macOS 用 osascript、Linux 用 notify-send，失败静默降级）。
远程消息推送：若已配置推送渠道（默认复用消息推送技能 config.json），报告会自动推送到
钉钉 / 飞书 / 企业微信 / 邮件等（--no-push 可整体关闭，--push-config 可指定配置文件）。
桌面 HTML 报告默认**不**生成，需显式加 `--html` 才会写到桌面 `WorkBuddy积分播报.html`；
这样在对话里可以先看文字播报、再按需询问用户是否要生成 HTML 报告。两者任一失败均静默降级，不影响主结果。
安全约束：只读登录态、绝不打印或外传 accessToken；自动签到仅调用 daily-checkin 这一授权写接口，
绝不调用兑换 / 抽奖等其他写接口；除向桌面写入报告 HTML 与调用系统通知外，不修改或删除任何文件。

注意：STREAK_TIERS 与 Buddy 加油站官方页面节点一致（7 / 14 / 28 天），
14 天节点奖励为脚本推断（截图仅截到 7 / 28 的 tooltip），待用户或官方校准。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sys
import time
import platform
import random
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime

AUTH_FILENAMES = ["workbuddy-desktop.info"]
EXTENSION_DIRS = [os.path.join("CodeBuddyExtension", "Data", "Public", "auth")]
USER_AUTH_DIR = os.path.join(".workbuddy", "auth")

CHECKIN_STATUS_PATH = "/v2/billing/meter/checkin-activity-status"
CHECKIN_PATH = "/v2/billing/meter/daily-checkin"
GROWTH_TASKS_PATH = "/v2/activity/growth/tasks"
GROWTH_PROFILE_PATH = "/v2/activity/growth/profile"
GROWTH_BUDDY_PATH = "/v2/activity/growth/buddy/info"
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# ===== 派猫猫旅行（Buddy Travel）=====
# 注意：旅行接口路径**不带 /v2 前缀**（与成长计划其他接口不同），且统一走官网域
# www.workbuddy.cn（与浏览器实测捕获一致）。全部接口仅需 Bearer Token，
# 无需 Turing Shield 设备指纹（已实测：请求头中无 x-device-token / x-sign）。
TRAVEL_DOMAIN = "www.workbuddy.cn"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"     # GET  旅行状态
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"     # POST 派出 body {"location_id": N}
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"       # POST 领取 body {}
TRAVEL_CONFIG_PATH = "/activity/growth/buddy/travel/config"     # GET  地点配置
TRAVEL_RECORDS_PATH = "/activity/growth/buddy/travel/records"   # GET  旅行历史
GROWTH_ENERGY_PATH = "/activity/growth/energy"                  # GET  能量余额
BUDDY_QUOTA_PATH = "/activity/growth/buddy/quota"               # GET  Buddy 开启配额

# 旅行状态中文映射
TRAVEL_STATE_TEXT = {
    "idle": "空闲（可派遣）",
    "traveling": "旅行中",
    "arrived": "已到达（待领取）",
}

# ===== 连续奖励分档（与 Buddy加油站 官方页面节点一致：7 / 14 / 28 天）=====
# 字段来源：
#   7天  = +50积分 +3能量 +1张补登卡 +1次抽奖（截图 tooltip 可见）
#   14天 = +100积分 +4能量 +1张补登卡 +1次抽奖（脚本推断，截图未截到 tooltip）
#   28天 = +150积分 +5能量 +1张补登卡 +1次抽奖（截图 tooltip 可见）
# 奖励类型：累计发放（达到即自动到账，参考 7 / 14 节点）/ 主动兑换（28 天节点有「兑换奖励」按钮）
STREAK_TIERS = [
    {"streak_days": 7,  "credit": 50,  "energy": 3, "makeup_card": 1, "lottery": 1, "type": "累计发放", "note": "官方页面 tooltip"},
    {"streak_days": 14, "credit": 100, "energy": 4, "makeup_card": 1, "lottery": 1, "type": "累计发放", "note": "脚本推断·待校准"},
    {"streak_days": 28, "credit": 150, "energy": 5, "makeup_card": 1, "lottery": 1, "type": "主动兑换", "note": "官方页面 tooltip"},
]

DESKTOP_REPORT_NAME = "WorkBuddy积分播报.html"

# =============================================================================
# WorkBuddy 5.6.2+ 登录态兼容：AtRestEncryption 解密模块（读端兼容，自包含）
# 说明：本段由签到助手 3.1.2 同款算法精确移植（含进程内存密钥发现），用于兼容 5.6.2+ 客户端将
# auth.accessToken 由明文 JWT 改为 AES-256-GCM 信封的情况。解密为只读操作。
# =============================================================================

def _check_expiry(auth):
    """登录态已过期时给出友好提示（只读取 expiresAt，绝不打印 token）。"""
    raw = auth.get("expiresAt")
    if not raw:
        return  # 无过期字段则跳过检查
    try:
        exp = int(raw)
    except (TypeError, ValueError):
        return
    # 兼容 epoch 毫秒（13 位）/ 秒（10 位）
    if exp > 10 ** 11:
        exp = exp / 1000.0
    if exp <= time.time():
        expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp))
        raise RuntimeError(
            "登录态已过期（过期时间 %s），请重新登录 WorkBuddy 客户端后再试" % expire_str)


# =============================================================================

# =============================================================================
# WorkBuddy 5.6.2+ 登录态 AtRestEncryption 解密（读端兼容，自包含）
# -----------------------------------------------------------------------------
# 背景：5.6.2 客户端把 AtRestEncryption 的 buildMode 从 disabled 改为 required，
# 登录态 workbuddy-desktop.info 中的 auth.accessToken（及 refreshToken 等敏感字段）
# 由明文 JWT 变成 AES-256-GCM 信封： {"$wbEncrypted":1, "envelope":"<base64>"}
# 旧脚本直接把字段当明文 JWT 读取 -> 鉴权失败 -> 技能无法签到。
#
# 本段仅做「读端」兼容解密，复刻客户端 packages/at-rest-crypto 的算法：
#   envelope(suite=1) = {suite, keyId, nonce(b64), authTag(b64), ciphertext(b64)}
#   AES 密钥 key = SHA256(atRestSecretKey 字符串 UTF-8)  -> 32 字节
#   keyId      = SHA256(key 字节).hexdigest()[:16]
#   AAD(sym-v1, framing=field) = 见 _build_field_aad()
# atRestSecretKey 由客户端原生模块 electron.workbuddyStorage.loggerGet() 返回，
# 按「当前用户」DPAPI 保护落盘（CryptProtectData/CryptUnprotectData，无 CredRead），
# 文件名在原生模块里运行时拼接，不是静态字面量；因此这里通过「扫描 DPAPI blob +
# 校验派生 keyId 必须匹配信封 keyId」来自动定位，无需硬编码路径。
# 注：5.6.2+ 部分环境下 atRestSecretKey 只驻留运行中的 WorkBuddy.exe 进程内存（不落盘），
# 此时额外通过「扫描进程内存」兜底（见 _discover_at_rest_key_from_memory）。
# =============================================================================

# ---- 常量（与客户端 at-rest-crypto/dist/key-normalize / field.mjs 完全一致）----
_AAD_DOMAIN = b"WB-AAD\x00"
_FRAMING_CODE = {"file": 1, "field": 2, "record": 3, "stream": 4}
_STANDARD_FORMAT_ID = {"file": "WBEF1", "field": "WBEV1", "record": "WBER1", "stream": "WBES1"}


def _b64_canonical_decode(value, field):
    """复刻 decodeCanonicalBase64：标准 base64、末尾补 =，且与重编码一致才算合法。"""
    if not isinstance(value, str):
        raise ValueError("%s 不是字符串" % field)
    decoded = base64.b64decode(value)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("%s 不是规范 base64" % field)
    return decoded


def _derive_at_rest_key(at_rest_secret_key):
    """复刻 normalizeAtRestKeyPayload：key=SHA256(secret字符串UTF-8)，keyId=SHA256(key)[:16]。

    返回 (key_32bytes, key_id_16hex)。
    """
    if not isinstance(at_rest_secret_key, str) or len(at_rest_secret_key) != 44:
        raise ValueError("atRestSecretKey 必须是 44 字符规范 base64（32 字节）")
    # 校验是规范 base64 且解码为 32 字节
    raw = _b64_canonical_decode(at_rest_secret_key, "atRestSecretKey")
    if len(raw) != 32:
        raise ValueError("atRestSecretKey 解码后必须是 32 字节")
    key = hashlib.sha256(at_rest_secret_key.encode("utf-8")).digest()
    key_id = hashlib.sha256(key).hexdigest()[:16]
    return key, key_id


def _build_field_aad(key_id):
    """复刻 buildAuthenticatedContextAad(keyId, suite=1, {framing:'field'}, scheme='sym-v1')。"""
    def encode_uint32(v):
        return v.to_bytes(4, "big")
    def encode_length_prefixed(s):
        b = s.encode("utf-8")
        return encode_uint32(len(b)) + b
    return b"".join([
        _AAD_DOMAIN,
        bytes([1]),
        encode_length_prefixed(_STANDARD_FORMAT_ID["field"]),  # "WBEV1"
        encode_length_prefixed("sym-v1"),
        encode_uint32(1),                                       # suite
        encode_length_prefixed(key_id),                          # 16 hex 字符
        bytes([_FRAMING_CODE["field"]]),                         # 2
        bytes([0]),                                              # sequence 缺失
        bytes([0]),                                              # final 缺失
    ])


# ---- AES-256-GCM 后端（cryptography > PyCryptodome > 纯 Python 兜底）----
def _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag):
    """AES-256-GCM 解密，返回明文 bytes；校验失败抛 IntegrityError。"""
    # 1) cryptography（WorkBuddy 托管 Python 自带，优先）
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        return aes.decrypt(nonce, ciphertext + auth_tag, aad)
    except ImportError:
        pass
    # 2) PyCryptodome（常见纯 Python 可安装包）
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        return cipher.decrypt_and_verify(ciphertext, auth_tag)
    except ImportError:
        pass
    # 3) 纯 Python 兜底（零依赖，已与 cryptography 对拍校验）
    return _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag)


def _aes_gcm_encrypt(key, nonce, plaintext, aad):
    """AES-256-GCM 加密，返回 (ciphertext, auth_tag)；供自测 roundtrip 使用。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aes = AESGCM(key)
        ct_and_tag = aes.encrypt(nonce, plaintext, aad)
        return ct_and_tag[:-16], ct_and_tag[-16:]
    except ImportError:
        pass
    try:
        from Crypto.Cipher import AES  # type: ignore
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        cipher.update(aad)
        ct, tag = cipher.encrypt_and_digest(plaintext)
        return ct, tag
    except ImportError:
        pass
    return _aes_gcm_encrypt_pure(key, nonce, plaintext, aad)


# ---- 纯 Python AES-256-GCM 实现（仅作兜底，已与 cryptography 对拍）----
def _aes_gcm_decrypt_pure(key, nonce, ciphertext, aad, auth_tag):
    pt = _aes_gcm_pure(key, nonce, ciphertext, aad, auth_tag, decrypt=True)
    return pt


def _aes_gcm_encrypt_pure(key, nonce, plaintext, aad):
    ct, tag = _aes_gcm_pure(key, nonce, plaintext, aad, None, decrypt=False)
    return ct, tag


def _aes_gcm_pure(key, nonce, data, aad, auth_tag_in, decrypt):
    # AES 密钥扩展
    Nk = len(key) // 4
    Nr = Nk + 6
    RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]
    SBOX = _aes_sbox()
    w = [list(key[4 * i:4 * i + 4]) for i in range(Nk)]
    for i in range(Nk, 4 * (Nr + 1)):
        temp = list(w[i - 1])
        if i % Nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[b] for b in temp]
            temp[0] ^= RCON[i // Nk - 1]
        elif Nk > 6 and i % Nk == 4:
            temp = [SBOX[b] for b in temp]
        w.append([w[i - Nk][j] ^ temp[j] for j in range(4)])
    round_keys = [[w[i][j] for i in range(4 * (Nr + 1))] for j in range(4)]

    def xtime(a):
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        return a & 0xFF

    def gmul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p & 0xFF

    def add_round_key(state, rnd):
        for c in range(4):
            for r in range(4):
                state[r][c] ^= round_keys[r][4 * rnd + c]

    def sub_bytes(state):
        for r in range(4):
            for c in range(4):
                state[r][c] = SBOX[state[r][c]]

    def shift_rows(state):
        state[1] = state[1][1:] + state[1][:1]
        state[2] = state[2][2:] + state[2][:2]
        state[3] = state[3][3:] + state[3][:3]

    def mix_columns(state):
        for c in range(4):
            a = [state[r][c] for r in range(4)]
            state[0][c] = gmul(a[0], 2) ^ gmul(a[1], 3) ^ a[2] ^ a[3]
            state[1][c] = a[0] ^ gmul(a[1], 2) ^ gmul(a[2], 3) ^ a[3]
            state[2][c] = a[0] ^ a[1] ^ gmul(a[2], 2) ^ gmul(a[3], 3)
            state[3][c] = gmul(a[0], 3) ^ a[1] ^ a[2] ^ gmul(a[3], 2)

    def cipher_block(block):
        state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
        add_round_key(state, 0)
        for rnd in range(1, Nr):
            sub_bytes(state)
            shift_rows(state)
            mix_columns(state)
            add_round_key(state, rnd)
        sub_bytes(state)
        shift_rows(state)
        add_round_key(state, Nr)
        return bytes(state[r][c] for c in range(4) for r in range(4))

    # GCM
    H = cipher_block(b"\x00" * 16)
    # GHASH
    def gf_mult(x, y):
        R = 0xE1000000000000000000000000000000
        z = 0
        v = y
        for i in range(128):
            if (x >> (127 - i)) & 1:
                z ^= v
            if v & 1:
                v = (v >> 1) ^ R
            else:
                v >>= 1
        return z

    def ghash_blocks(blocks):
        y = 0
        for blk in blocks:
            x = int.from_bytes(blk, "big")
            y = gf_mult(y ^ x, int.from_bytes(H, "big"))
        return y.to_bytes(16, "big")

    # 构造计数器块：nonce(12) + 32位计数（从 1 开始）
    def ctr_block(counter):
        cb = nonce + counter.to_bytes(4, "big")
        return cipher_block(cb)

    # 分块（16 字节，末尾补零），空输入返回 []
    def split_blocks(b):
        if not b:
            return []
        pad = b + b"\x00" * ((16 - len(b) % 16) % 16)
        return [pad[i:i + 16] for i in range(0, len(pad), 16)]

    # CTR 加/解密（对称：明文 ^ 密钥流 = 密文）
    # 关键：数据计数器从 inc32(J0) 开始，即 nonce||0x00000002（J0=nonce||0x00000001 只用于给 tag 做 E_K）
    aad_blocks = split_blocks(aad)
    if decrypt:
        ct_blocks = split_blocks(data)          # data = 密文
        out = bytearray()
        for i, blk in enumerate(ct_blocks):
            ks = ctr_block(i + 2)
            out.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        out = bytes(out[:len(data)])
        ct_for_ghash = data
    else:
        pt_blocks = split_blocks(data)          # data = 明文
        ct = bytearray()
        for i, blk in enumerate(pt_blocks):
            ks = ctr_block(i + 2)
            ct.extend(bytes(blk[j] ^ ks[j] for j in range(len(blk))))
        ct = bytes(ct[:len(data)])
        out = ct
        ct_for_ghash = ct

    # GHASH 必须基于"密文"计算（加密时即 CTR 输出；解密时即输入密文）
    ghash_input = aad_blocks + split_blocks(ct_for_ghash)
    ghash_input.append((len(aad) * 8).to_bytes(8, "big") + (len(ct_for_ghash) * 8).to_bytes(8, "big"))
    S = ghash_blocks(ghash_input)

    # J0：12 字节 nonce 时 J0 = nonce || 0x00000001；其它长度按 GHASH(nonce) 处理
    if len(nonce) == 12:
        j0 = nonce + b"\x00\x00\x00\x01"
    else:
        j0 = ghash_blocks(split_blocks(nonce) + [(len(nonce) * 8).to_bytes(16, "big")])
    ek_j0 = cipher_block(j0)
    tag = bytes(S[k] ^ ek_j0[k] for k in range(16))

    if decrypt:
        if auth_tag_in is not None and int.from_bytes(tag, "big") != int.from_bytes(auth_tag_in, "big"):
            raise ValueError("ciphertext authentication failed (GCM tag mismatch)")
        return out
    else:
        return out, tag


_AES_SBOX_CACHE = None


def _aes_sbox():
    # 纯 Python 兜底用的标准 AES S-Box（公开常量，已验证）。
    return _AES_STD_SBOX


def _rotl(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


def _xtime_lite(x):
    x <<= 1
    if x & 0x100:
        x ^= 0x11B
    return x & 0xFF


def _gmul_lookup(a, b):
    # 仅用于扩展欧几里得，简单实现
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p & 0xFF


# 标准 AES S-Box（公开常量，用于纯 Python 兜底，确保正确性）
_AES_STD_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]


# ---- 解析信封（复刻 parseSupportedEnvelope，仅 suite=1）----
def _parse_suite1_envelope(envelope_bytes):
    try:
        env = json.loads(envelope_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError("信封不是合法 JSON: %s" % e)
    if not isinstance(env, dict):
        raise ValueError("信封必须是对象")
    if env.get("suite") != 1:
        raise ValueError("只支持 suite=1，实际 %r" % env.get("suite"))
    key_id = env.get("keyId")
    if not isinstance(key_id, str) or not _re_hex16(key_id):
        raise ValueError("envelope.keyId 非法")
    nonce = _b64_canonical_decode(env["nonce"], "nonce")
    auth_tag = _b64_canonical_decode(env["authTag"], "authTag")
    ct = env.get("ciphertext")
    ciphertext = b"" if (isinstance(ct, str) and ct == "") else _b64_canonical_decode(ct, "ciphertext")
    if len(nonce) != 12 or len(auth_tag) != 16:
        raise ValueError("nonce(12)/authTag(16) 长度错误")
    return key_id, nonce, ciphertext, auth_tag


def _re_hex16(s):
    import re
    return bool(re.fullmatch(r"[0-9a-f]{16}", s))


# ---- 当前用户 DPAPI（Windows）解开 atRestSecretKey 落盘文件 ----
def _dpapi_unprotect(blob):
    """调用 crypt32.CryptUnprotectData（当前用户）解开 DPAPI blob，返回原文 bytes。"""
    if not sys.platform.startswith("win"):
        raise OSError("DPAPI 仅在 Windows 可用")
    import ctypes
    from ctypes import wintypes
    crypt32 = ctypes.windll.crypt32  # type: ignore
    blob_len = len(blob)
    blob_buf = ctypes.create_string_buffer(blob, blob_len)
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]
    in_blob = DATA_BLOB(blob_len, blob_buf)
    out_blob = DATA_BLOB()
    res = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not res:
        raise OSError("CryptUnprotectData 失败（可能不是当前用户/本机加密的 blob）")
    try:
        size = out_blob.cbData
        ptr = ctypes.cast(out_blob.pbData, ctypes.POINTER(ctypes.c_byte * size))
        data = bytes(ptr.contents[:size])
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)  # type: ignore
    return data


# ---- 5.6.2+ 进程内存密钥发现（atRestSecretKey 只驻留运行中客户端内存、磁盘无落盘文件时使用）----
# WorkBuddy 5.6.2 客户端由原生模块 workbuddyStorage.loggerGet() 运行时提供密钥，
# 密钥不存在于任何文件；可用 ReadProcessMemory 扫描运行中 WorkBuddy.exe 进程内存，
# 定位信封 keyId 的 ASCII 串，提取附近 44 字符 base64 候选并按 sha256 派生 keyId 严格校验。
# 密钥材料仅在内存中校验使用，绝不写盘、绝不打印。
_B64_CANDIDATE_RE = r"[A-Za-z0-9+/]{43}="
_B64_CANDIDATE_TEXT_RE = r"[A-Za-z0-9+/]{43}="
# 同一候选模式的 UTF-16LE 形式（V8/Electron 字符串可能以宽字符驻留内存）
_B64_CANDIDATE_RE_UTF16 = rb"(?:[A-Za-z0-9+/]\x00){43}=\x00"
# 全内存兜底扫描的候选去重上限（防长尾）
_MAX_MEMORY_CANDIDATES = 200000


def _list_workbuddy_pids():
    """枚举运行中的 WorkBuddy 客户端进程 PID（仅 Windows）。"""
    if not sys.platform.startswith("win"):
        return []
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_size_t(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),   # ULONG_PTR
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),         # 注意是 LONG，不是指针
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    names = {"workbuddy.exe"}
    extra = os.environ.get("WORKBUDDY_PROCESS_NAMES", "")
    if extra:
        names |= {n.strip().lower() for n in extra.split(",") if n.strip()}

    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile
            if name and name.lower() in names:
                pids.append(int(entry.th32ProcessID))
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def _iter_process_readable_regions(kernel32, handle, max_total_bytes=3 * 1024 * 1024 * 1024):
    """逐段产出目标进程可读已提交内存块（bytes）。只读，跳过不可读/守护页。"""
    import ctypes
    from ctypes import wintypes

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        # 严格对齐 Windows 官方 _MEMORY_BASIC_INFORMATION（64 位）：
        # BaseAddress(8) / AllocationBase(8) / AllocationProtect(4) / RegionSize(8) / State(4) / Protect(4) / Type(4)
        _fields_ = [("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD),
                    ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD)]

    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
    kernel32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    MEM_COMMIT = 0x1000
    PAGE_NOACCESS = 0x01
    PAGE_GUARD = 0x100
    CHUNK = 8 * 1024 * 1024
    total = 0
    addr = 0
    mbi = MEMORY_BASIC_INFORMATION()
    while total < max_total_bytes:
        ret = kernel32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
        if not ret:
            break
        base = mbi.BaseAddress or addr
        region_size = mbi.RegionSize or 0
        if region_size <= 0:
            break
        if mbi.State == MEM_COMMIT and mbi.Protect != PAGE_NOACCESS and not (mbi.Protect & PAGE_GUARD):
            read = 0
            while read < region_size and total < max_total_bytes:
                n = min(CHUNK, region_size - read)
                buf = ctypes.create_string_buffer(n)
                got = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(handle, ctypes.c_void_p(base + read),
                                                buf, n, ctypes.byref(got))
                if ok and got.value:
                    data = buf.raw[:got.value]
                    total += len(data)
                    yield data
                    read += got.value
                else:
                    # 该子段不可读，跳到区域尾
                    read = region_size
        nxt = base + region_size
        if nxt <= addr:
            break
        addr = nxt


def _key_candidate_matches(candidate_text, target_key_id):
    """校验 44 字符 base64 候选是否派生出目标 keyId。"""
    try:
        _, key_id = _derive_at_rest_key(candidate_text)
        return key_id == target_key_id
    except Exception:
        return False


def _extract_key_near_hits(data, hit_indexes, pattern_len, target_key_id, utf16=False):
    """在 keyId 命中点附近 ±4KB 窗口提取 base64 候选并校验。"""
    import re
    win = 4096
    checked = set()
    for h in hit_indexes:
        lo = max(0, h - win)
        hi = min(len(data), h + pattern_len + win)
        seg = data[lo:hi]
        if utf16:
            for m in re.finditer(_B64_CANDIDATE_TEXT_RE, seg.decode("utf-16-le", "ignore")):
                cand = m.group(0)
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
        else:
            for m in re.finditer(_B64_CANDIDATE_RE.encode("ascii", "ignore"), seg):
                cand = m.group(0).decode("ascii")
                if cand in checked:
                    continue
                checked.add(cand)
                if _key_candidate_matches(cand, target_key_id):
                    return cand
    return None


def _discover_at_rest_key_from_memory(target_key_id):
    """扫描运行中 WorkBuddy 客户端进程内存寻找 atRestSecretKey（仅 Windows）。

    策略：
      Pass 1（定向，快速路径）：搜索信封 keyId 的 ASCII / UTF-16LE 串，
                                在命中点 ±4KB 窗口内提取 44 字符 canonical base64 候选并校验；
      Pass 2（兜底）：对全部可读内存做 base64 候选（ASCII 与 UTF-16LE）扫描逐个校验。

    ⚠️ 设计要点：Pass 2 必须无条件执行，不能用「Pass 1 是否命中 keyId」来门控。
      客户端内存中 keyId 的 ASCII 串会因 AAD / 日志等上下文大量出现，而密钥本体未必
      落在这些命中点的 ±4KB 邻域内；若以「是否命中」作门控，就会出现「命中 keyId 但
      取不到密钥 → 跳过全内存兜底 → 误报无法定位」的失败。故 Pass 1 失败后无条件继续
      Pass 2，并补齐 UTF-16LE 候选（V8 字符串可能以宽字符驻留），二者共用去重集合与上限。

    找到返回密钥字符串；找不到返回 None。任何异常都不向外抛（签到主流程不应因扫描崩溃）。
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        import re as _re
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        pattern = target_key_id.encode("ascii", "ignore")
        if len(pattern) != 16:
            return None
        utf16_pattern = target_key_id.encode("utf-16-le")
        b64_ascii_re = _re.compile(_B64_CANDIDATE_RE.encode("ascii", "ignore"))
        b64_utf16_re = _re.compile(_B64_CANDIDATE_RE_UTF16)
        pids = _list_workbuddy_pids()
        if not pids:
            return None
        seen_candidates = set()
        for pid in pids:
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not handle:
                continue
            try:
                for data in _iter_process_readable_regions(kernel32, handle):
                    # ---- Pass 1：keyId 邻域快速路径（ASCII 与 UTF-16LE 各试一次）----
                    for pat, is_u16 in ((pattern, False), (utf16_pattern, True)):
                        hits = []
                        start = 0
                        while True:
                            i = data.find(pat, start)
                            if i < 0:
                                break
                            hits.append(i)
                            start = i + 1
                        if hits:
                            key = _extract_key_near_hits(
                                data, hits, len(pat), target_key_id, utf16=is_u16)
                            if key:
                                return key
                    # ---- Pass 2：全内存候选兜底（不再被 Pass 1 命中与否门控）----
                    for m in b64_ascii_re.finditer(data):
                        cand = m.group(0).decode("ascii")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
                    for m in b64_utf16_re.finditer(data):
                        cand = m.group(0).decode("utf-16-le")
                        if cand in seen_candidates:
                            continue
                        seen_candidates.add(cand)
                        if len(seen_candidates) > _MAX_MEMORY_CANDIDATES:
                            return None
                        if _key_candidate_matches(cand, target_key_id):
                            return cand
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    except Exception:
        return None

def _discover_at_rest_key(target_key_id):
    """定位并解开 atRestSecretKey（44 字符规范 base64），要求其派生 keyId == target_key_id。

    优先顺序：
      1) 环境变量 WORKBUDDY_ATREST_KEY（直接给密钥串）
      2) 环境变量 WORKBUDDY_ATREST_KEY_FILE（指向密钥文件）
      3) 扫描登录态所在 Data 目录下的 DPAPI blob，逐个解开并校验 keyId
      4) 扫描运行中 WorkBuddy.exe 进程内存（5.6.2 密钥只驻留内存、磁盘无落盘文件的可靠途径）
    返回 atRestSecretKey 字符串；找不到则抛 RuntimeError（带可操作提示）。
    """
    # 1) 直接给密钥
    env_key = os.environ.get("WORKBUDDY_ATREST_KEY")
    if env_key:
        _derive_at_rest_key(env_key)  # 仅校验格式
        return env_key
    # 2) 密钥文件（可能是 DPAPI 密文，也可能是明文 44 字符串）
    env_file = os.environ.get("WORKBUDDY_ATREST_KEY_FILE")
    if env_file and os.path.isfile(env_file):
        try:
            raw = open(env_file, "rb").read()
            return _candidate_to_at_rest_key(raw, target_key_id)
        except Exception:
            pass
    # 3) 扫描 DPAPI blob 自动发现
    roots = _at_rest_scan_roots()
    DPAPI_MAGIC = b"\x01\x00\x00\x00\xd0\x8c\x9d\x0a"
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for path, size in _iter_candidate_files(root):
            if size > 256 * 1024:
                continue
            try:
                content = open(path, "rb").read()
            except Exception:
                continue
            idx = content.find(DPAPI_MAGIC)
            if idx < 0:
                continue
            # 从版本字（magic 前 4 字节）到文件尾，作为完整 blob 尝试
            blob = content[idx:]
            try:
                candidate = _candidate_to_at_rest_key(blob, target_key_id)
                if candidate:
                    return candidate
            except Exception:
                continue
            found.append(path)
    # 4) 扫描运行中客户端进程内存（5.6.2 的 atRestSecretKey 不落盘、只驻留 WorkBuddy.exe 内存）
    try:
        mem_key = _discover_at_rest_key_from_memory(target_key_id)
    except Exception:
        mem_key = None
    if mem_key:
        return mem_key
    raise RuntimeError(
        "无法在本机定位 WorkBuddy 5.6.2+ 的 atRestSecretKey"
        "（已扫描 %d 个候选 DPAPI blob 与运行中客户端进程内存）。\n"
        "可选项：\n"
        "  (a) 确认 WorkBuddy 客户端已启动并登录（密钥只驻留客户端进程内存，客户端未运行则无法解密），"
        "且本脚本与客户端在同一 Windows 用户下运行；\n"
        "  (b) 复制本机登录态所在 Data 目录下由 WorkBuddy 加密的密钥文件，设置环境变量 "
        "WORKBUDDY_ATREST_KEY_FILE=该文件绝对路径；\n"
        "  (c) 直接设置 WORKBUDDY_ATREST_KEY=<44字符规范base64>；\n"
        "  (d) 退回 WorkBuddy 5.5.x 客户端（该版本登录态为明文，旧脚本即可用）。\n"
        "注：客户端以管理员权限运行而本脚本未提权（或相反）时，进程内存扫描会被系统拒绝。"
        % len(found))


def _candidate_to_at_rest_key(blob_or_text, target_key_id):
    """尝试把一段字节变成 atRestSecretKey 并校验 keyId。失败抛异常。"""
    # 先尝试作为 DPAPI 密文解开
    text = None
    try:
        plain = _dpapi_unprotect(blob_or_text)
        text = plain.decode("utf-8", "replace").strip()
    except Exception:
        # 也可能就是明文串（无 DPAPI 包裹）
        try:
            text = blob_or_text.decode("utf-8", "replace").strip()
        except Exception:
            text = None
    if not text:
        raise ValueError("无法解析候选密钥")
    # 去掉可能的引号/空白
    text = text.strip().strip('"').strip("'")
    try:
        key, key_id = _derive_at_rest_key(text)
    except Exception:
        raise ValueError("候选不是合法 atRestSecretKey")
    if key_id != target_key_id:
        raise ValueError("keyId 不匹配（%s != %s）" % (key_id, target_key_id))
    return text


def _at_rest_scan_roots():
    roots = []
    home = os.path.expanduser("~")
    for env in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env, "")
        if base:
            roots.append(os.path.join(base, "CodeBuddyExtension", "Data"))
            roots.append(os.path.join(base, "WorkBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".config", "CodeBuddyExtension", "Data"))
    roots.append(os.path.join(home, ".workbuddy"))
    # 去重保序
    seen = set()
    out = []
    for r in roots:
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


_SKIP_DIRS = {
    "cache", "code cache", "gpucache", "file-tree", "extensions", "blob_storage",
    "indexeddb", "service worker", "logs", "crashpad", "swreporter", "network",
    "application cache", "shadercache", "webrtc", "videoDecodeStats",
}


def _iter_candidate_files(root):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
            except Exception:
                continue
            yield p, sz
            count += 1
            if count > 4000:
                return


def decrypt_access_token_field(raw_value):
    """复刻 ProtectedFieldCodec.decodeString：字符串直接返回；信封则解 AES-GCM。

    raw_value: auth.accessToken 字段（可能是 str，也可能是 {$wbEncrypted:1, envelope}）
    返回明文 JWT 字符串。
    """
    if isinstance(raw_value, str):
        return raw_value  # 明文 JWT（5.5.x 及更早 / 加密关闭时）
    if isinstance(raw_value, dict) and raw_value.get("$wbEncrypted") == 1:
        envelope_b64 = raw_value.get("envelope")
        if not isinstance(envelope_b64, str):
            raise ValueError("加密信封缺少 envelope 字段")
        envelope_bytes = base64.b64decode(envelope_b64)
        if base64.b64encode(envelope_bytes).decode("ascii") != envelope_b64:
            raise ValueError("envelope 不是规范 base64")
        key_id, nonce, ciphertext, auth_tag = _parse_suite1_envelope(envelope_bytes)
        at_rest_secret_key = _discover_at_rest_key(key_id)
        key, _ = _derive_at_rest_key(at_rest_secret_key)
        aad = _build_field_aad(key_id)
        plaintext = _aes_gcm_decrypt(key, nonce, ciphertext, aad, auth_tag)
        return plaintext.decode("utf-8")
    raise ValueError("accessToken 既不是明文也不是加密信封")




def find_login_state():
    candidates = []
    for env_key in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(env_key)
        if base:
            for ext in EXTENSION_DIRS:
                candidates.append(os.path.join(base, ext))
    home = os.path.expanduser("~")
    candidates.append(os.path.join(home, "Library", "Application Support",
                                   "CodeBuddyExtension", "Data", "Public", "auth"))
    candidates.append(os.path.join(home, ".config", "CodeBuddyExtension",
                                   "Data", "Public", "auth"))
    candidates.append(os.path.join(home, USER_AUTH_DIR))
    for d in candidates:
        for name in AUTH_FILENAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


def load_credentials(path):
    """读取登录态并取出 (token, domain)。

    兼容 WorkBuddy 5.6.2+ 客户端：登录态中的 auth.accessToken 在 5.6.2+ 由明文 JWT
    变为 AES-256-GCM 信封 {"$wbEncrypted":1,"envelope":"<base64>"}（AtRestEncryption
    buildMode=required）。本函数自动识别明文 / 信封两种情况——明文原样返回（旧客户端），
    信封则通过 decrypt_access_token_field 用本机当前用户 DPAPI 定位并解开 atRestSecretKey
    后解密（5.6.2+）。解密是只读操作，绝不回写、绝不外传登录态。
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    auth = data.get("auth", {})
    raw = auth.get("accessToken")
    if raw is None:
        raise ValueError("登录态未包含 accessToken，请确认 WorkBuddy 已登录")
    try:
        token = decrypt_access_token_field(raw)
    except Exception as exc:
        raise ValueError("读取/解密 accessToken 失败（可能为 5.6.2+ 加密登录态）: %s" % exc)
    domain = auth.get("domain") or "www.codebuddy.cn"
    _check_expiry(auth)
    if not token or not domain:
        raise ValueError("登录态缺少 accessToken 或 domain，请先在 WorkBuddy 客户端登录")
    return token, domain


def api_call(domain, token, path, method="POST", payload=None):
    """调用接口。payload 为 None 时保持原行为（POST 发 {}，GET 不带体）。

    payload 非 None 时按 JSON 编码发送（派猫猫旅行的 depart 需要传 location_id）。
    """
    url = "https://" + domain + path
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    else:
        body = b"{}" if method == "POST" else None
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            return exc.code, {"code": -1, "msg": "HTTP " + str(exc.code)}
    except Exception as exc:  # noqa: BLE001
        return 0, {"code": -1, "msg": str(exc)}


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATETIME_FMT)
    except Exception:
        return None


def fetch_status(domain, token):
    status, body = api_call(domain, token, CHECKIN_STATUS_PATH)
    if status != 200 or body.get("code") != 0:
        raise RuntimeError("查询 Buddy 加油站状态失败：HTTP %s, %s" % (status, body.get("msg")))
    return body.get("data", {})


def do_checkin(domain, token):
    """自动签到（写接口）。先查后签，幂等安全。返回结果字典。"""
    try:
        data = fetch_status(domain, token)
    except Exception as exc:  # noqa: BLE001
        return {"action": "failed", "success": False, "message": "查询前置状态失败：%s" % exc}
    if data.get("today_checked_in"):
        return {"action": "skipped", "already_checked_in": True,
                "message": "今日已签到，无需重复打卡"}
    st, chk = api_call(domain, token, CHECKIN_PATH)
    code = chk.get("code")
    msg = chk.get("msg")
    if st == 200 and code == 0:
        return {"action": "checked_in", "success": True,
                "message": msg or "签到成功", "data": chk.get("data")}
    if code == 10001 and "已签到" in (msg or ""):
        return {"action": "skipped", "already_checked_in": True,
                "message": msg or "今日已签到"}
    return {"action": "failed", "success": False,
            "message": msg or ("HTTP %s" % st), "code": code}


def fetch_growth(domain, token):
    """成长计划只读数据（3 个 GET 接口，无需 Turing Shield 设备指纹）。

    任一接口失败 / 非 200 / 非 0 码均静默跳过，不影响主结果；返回原始 data 字典集合。
    """
    out = {"tasks": None, "profile": None, "buddy": None}
    growth_keys = {GROWTH_TASKS_PATH: "tasks", GROWTH_PROFILE_PATH: "profile", GROWTH_BUDDY_PATH: "buddy"}
    for path, key in growth_keys.items():
        try:
            st, body = api_call(domain, token, path, method="GET")
            if st != 200:
                continue
            if isinstance(body, list):
                out[key] = body
            elif isinstance(body, dict) and body.get("code") == 0:
                data = body.get("data")
                if isinstance(data, dict):
                    if key == "tasks":
                        out[key] = data.get("tasks")
                    elif key == "buddy":
                        out[key] = data.get("buddy")
                    else:
                        out[key] = data
                else:
                    out[key] = data
        except Exception:  # noqa: BLE001
            pass
    return out


def parse_growth(raw):
    """将 fetch_growth 原始结果规整为可读结构；字段缺失填 None，绝不编造。"""
    if not isinstance(raw, dict):
        return {"available": False, "tasks": None, "profile": None, "buddy": None}
    tasks = raw.get("tasks")
    profile = raw.get("profile")
    buddy = raw.get("buddy")

    norm_tasks = None
    if isinstance(tasks, list):
        norm_tasks = []
        for t in tasks:
            if not isinstance(t, dict):
                continue
            norm_tasks.append({
                "title": t.get("title") or t.get("name") or t.get("task_name") or "未命名任务",
                "reward_credit": t.get("reward_credit") or t.get("credit") or t.get("reward"),
                "status": t.get("status") or t.get("state"),
            })

    norm_profile = None
    if isinstance(profile, dict):
        norm_profile = {
            "level": profile.get("level"),
            "progress": profile.get("completed") or profile.get("progress") or profile.get("current_step") or profile.get("done"),
            "total": profile.get("total") or profile.get("total_step"),
        }

    norm_buddy = None
    if isinstance(buddy, dict):
        norm_buddy = {
            "name": buddy.get("name") or buddy.get("buddy_name"),
            "tier": buddy.get("rarity") or buddy.get("tier") or buddy.get("level") or buddy.get("grade"),
        }

    available = any(v is not None for v in (norm_tasks, norm_profile, norm_buddy))
    return {"available": available, "tasks": norm_tasks, "profile": norm_profile, "buddy": norm_buddy}


# ===== 派猫猫旅行（Buddy Travel）=====

def _travel_get(token, path):
    """旅行只读 GET；失败 / 非 200 / 非 0 码均返回 None（静默降级，不影响主结果）。"""
    try:
        st, body = api_call(TRAVEL_DOMAIN, token, path, method="GET")
        if st != 200 or not isinstance(body, dict) or body.get("code") != 0:
            return None
        return body.get("data")
    except Exception:  # noqa: BLE001
        return None


def _fmt_duration(seconds):
    """把秒数格式化为「X 小时 Y 分」。"""
    if seconds is None:
        return None
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours > 0:
        return "%d 小时 %d 分" % (hours, minutes)
    if minutes > 0:
        return "%d 分钟" % minutes
    return "%d 秒" % seconds


def fetch_travel(token):
    """派猫猫旅行只读数据：状态 / 地点配置 / 能量 / 配额。

    注：旅行历史 TRAVEL_RECORDS_PATH 已实测可用，但当前不参与展示，故不主动请求以免增加开销。
    """
    return {
        "status": _travel_get(token, TRAVEL_STATUS_PATH),
        "config": _travel_get(token, TRAVEL_CONFIG_PATH),
        "energy": _travel_get(token, GROWTH_ENERGY_PATH),
        "quota": _travel_get(token, BUDDY_QUOTA_PATH),
    }


def parse_travel(raw):
    """规整派猫猫旅行数据；字段缺失填 None，绝不编造。"""
    if not isinstance(raw, dict):
        return {"available": False}
    status = raw.get("status") if isinstance(raw.get("status"), dict) else None
    config = raw.get("config") if isinstance(raw.get("config"), dict) else None
    energy = raw.get("energy") if isinstance(raw.get("energy"), dict) else None
    quota = raw.get("quota") if isinstance(raw.get("quota"), dict) else None

    locations = []
    if config and isinstance(config.get("locations"), list):
        for loc in config["locations"]:
            if not isinstance(loc, dict):
                continue
            locations.append({
                "id": loc.get("id"),
                "code": loc.get("code"),
                "name": loc.get("name") or "未命名地点",
                "duration_min": loc.get("duration_hours_min"),
                "duration_max": loc.get("duration_hours_max"),
                "reward_min": loc.get("reward_credit_min"),
                "reward_max": loc.get("reward_credit_max"),
            })

    state = None
    remaining = None
    loc_name = None
    reward = None
    if status:
        state = status.get("state")
        arrive = status.get("arrive_at")
        now = status.get("server_now")
        if state == "traveling" and isinstance(arrive, int) and isinstance(now, int):
            remaining = max(0, arrive - now)
        loc = status.get("location")
        if isinstance(loc, dict):
            loc_name = loc.get("name")
        reward = status.get("reward_credit")

    return {
        "available": status is not None or bool(locations),
        "state": state,
        "state_text": TRAVEL_STATE_TEXT.get(state, state),
        "location_name": loc_name,
        "reward_credit": reward,
        "remaining_seconds": remaining,
        "remaining_text": _fmt_duration(remaining) if remaining is not None else None,
        "daily_limit_reached": status.get("daily_limit_reached") if status else None,
        "record_id": status.get("record_id") if status else None,
        "energy": (energy or {}).get("balance"),
        "energy_cost_per_open": (quota or {}).get("cost_per_open"),
        "locations": locations,
    }


def travel_claim(token):
    """领取已到达旅行的积分（写接口）。无可领取时服务端会返回非 0 码，本函数如实上报。"""
    try:
        st, body = api_call(TRAVEL_DOMAIN, token, TRAVEL_CLAIM_PATH, method="POST", payload={})
    except Exception as exc:  # noqa: BLE001
        return {"action": "failed", "success": False, "message": str(exc)}
    if st == 200 and isinstance(body, dict) and body.get("code") == 0:
        data = body.get("data") or {}
        credit = data.get("reward_credit")
        return {"action": "claimed", "success": True, "reward_credit": credit,
                "message": "✅ 已领取旅行奖励 +%s 积分" % (credit if credit is not None else "?")}
    return {"action": "failed", "success": False,
            "message": (body.get("msg") if isinstance(body, dict) else None) or ("HTTP %s" % st),
            "code": body.get("code") if isinstance(body, dict) else None}


def travel_depart(token, location_id=None, locations=None):
    """派出 Buddy 旅行（写接口）。location_id 为空时从可选地点中随机。

    仅调用方确认「未达每日上限」后才应调用本函数；本函数不再重复查询状态。
    """
    chosen = location_id
    if chosen is None:
        ids = [loc.get("id") for loc in (locations or []) if loc.get("id") is not None]
        if not ids:
            return {"action": "failed", "success": False, "message": "未能获取可选地点列表，已跳过派遣"}
        chosen = random.choice(ids)
    try:
        st, body = api_call(TRAVEL_DOMAIN, token, TRAVEL_DEPART_PATH,
                            method="POST", payload={"location_id": chosen})
    except Exception as exc:  # noqa: BLE001
        return {"action": "failed", "success": False, "location_id": chosen, "message": str(exc)}
    if st == 200 and isinstance(body, dict) and body.get("code") == 0:
        data = body.get("data") or {}
        loc = data.get("location") or {}
        return {"action": "departed", "success": True, "location_id": chosen,
                "location_name": loc.get("name"), "arrive_at": data.get("arrive_at"),
                "message": "🚀 已派出 Buddy 前往【%s】" % (loc.get("name") or ("地点 %s" % chosen))}
    return {"action": "failed", "success": False, "location_id": chosen,
            "message": (body.get("msg") if isinstance(body, dict) else None) or ("HTTP %s" % st),
            "code": body.get("code") if isinstance(body, dict) else None}


def travel_auto(token, location_id=None, allow_depart=True, allow_claim=True):
    """派猫猫旅行全自动闭环（写操作）：先领取、后派遣。

    流程：查状态 → 若 arrived 则领取 → 重新查状态 → 若 idle 且未达每日上限则派遣。
    安全约束：派遣前必须先确认 daily_limit_reached 为假，达上限绝不发写请求；
             派出去不会丢积分（到达后保持 arrived 等待下次运行补领）。
    """
    log = []
    status = _travel_get(token, TRAVEL_STATUS_PATH)
    if status is None:
        return {"success": False, "available": False, "log": ["查询旅行状态失败，已跳过全部写操作"]}

    if allow_claim and status.get("state") == "arrived":
        result = travel_claim(token)
        log.append(result.get("message") or result.get("action"))
        if result.get("action") == "claimed":
            fresh = _travel_get(token, TRAVEL_STATUS_PATH)
            if fresh is not None:
                status = fresh
    elif status.get("state") == "arrived":
        log.append("Buddy 已到达，未启用自动领取")

    if allow_depart:
        if status.get("daily_limit_reached"):
            log.append("今日派遣次数已用完，跳过派遣")
        elif status.get("state") in (None, "", "idle"):
            config = _travel_get(token, TRAVEL_CONFIG_PATH) or {}
            result = travel_depart(token, location_id, config.get("locations") or [])
            log.append(result.get("message") or result.get("action"))
        else:
            log.append("Buddy 正在旅行中，无需派遣")
    else:
        log.append("未启用自动派遣")

    final = _travel_get(token, TRAVEL_STATUS_PATH) or status
    return {"success": True, "available": True, "state": final.get("state"),
            "status": final, "log": log}


def collect_travel(token, travel_auto_on=False, location_id=None):
    """收集派猫猫旅行数据；travel_auto_on 为真时额外执行全自动闭环（先领后派）。

    返回 (原始只读数据, 自动执行日志或 None)。
    """
    raw = fetch_travel(token)
    auto_log = None
    if travel_auto_on:
        result = travel_auto(token, location_id=location_id, allow_depart=True, allow_claim=True)
        auto_log = result.get("log") or []
        if result.get("status"):
            raw["status"] = result["status"]
    return raw, auto_log


def streak_tier_schedule(streak_days):
    upcoming = []
    for tier in STREAK_TIERS:
        if tier["streak_days"] > streak_days:
            upcoming.append({
                "streak_days": tier["streak_days"],
                "credit": tier["credit"],
                "energy": tier["energy"],
                "makeup_card": tier["makeup_card"],
                "lottery": tier["lottery"],
                "type": tier["type"],
                "remaining": tier["streak_days"] - streak_days,
            })
    return upcoming


def build_report(data, growth=None, travel=None):
    today = date.today()
    checkin_dates = data.get("checkin_dates") or []
    this_month = [d for d in checkin_dates if d.startswith(today.strftime("%Y-%m"))]
    week_progress = data.get("week_progress") or []
    week_done = sum(1 for x in week_progress if x is True) if isinstance(week_progress, list) else None
    week_total = len(week_progress) if isinstance(week_progress, list) else None

    start_dt = _parse_dt(data.get("start_time"))
    end_dt = _parse_dt(data.get("end_time"))
    active = data.get("active")
    if start_dt and today < start_dt.date():
        activity_status = "未开始"
    elif end_dt and today > end_dt.date():
        activity_status = "已结束"
    else:
        activity_status = "进行中" if active else "未开始"
    remaining_days = None
    if end_dt and activity_status == "进行中":
        remaining_days = max(0, (end_dt.date() - today).days)
    daily_credit = data.get("daily_credit")
    full_attendance_estimate = (remaining_days * daily_credit) if (remaining_days is not None and daily_credit) else None

    streak_days = data.get("streak_days")
    next_streak_day = data.get("next_streak_day")
    streak_gap = None
    if isinstance(next_streak_day, int) and isinstance(streak_days, int) and next_streak_day > streak_days:
        streak_gap = next_streak_day - streak_days

    week_remaining = None
    if week_done is not None and week_total:
        week_remaining = max(0, week_total - week_done)

    report = {
        "total_credits": data.get("total_credits"),
        "streak_days": streak_days,
        "today_checked_in": data.get("today_checked_in"),
        "checkin_dates_total": len(checkin_dates),
        "checkin_dates_this_month": this_month,
        "week_checkin_days": data.get("week_checkin_days"),
        "week_progress_done": week_done,
        "week_progress_total": week_total,
        "week_remaining": week_remaining,
        "daily_credit": daily_credit,
        "today_credit": data.get("today_credit"),
        "is_streak_day": data.get("is_streak_day"),
        "next_streak_day": next_streak_day,
        "streak_gap": streak_gap,
        "streak_bonus_days": data.get("streak_bonus_days"),
        "streak_bonus_credit": data.get("streak_bonus_credit"),
        "streak_schedule": streak_tier_schedule(streak_days if isinstance(streak_days, int) else 0),
        "activity": {
            "active": active,
            "status": activity_status,
            "theme_name": data.get("theme_name"),
            "season": data.get("season"),
            "activity_name": data.get("activity_name"),
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "remaining_days": remaining_days,
            "full_attendance_estimate": full_attendance_estimate,
        },
        "claim": {
            "claim_button_text": data.get("claim_button_text"),
            "action_button": data.get("action_button"),
        },
        "remind_checkin": (not data.get("today_checked_in")),
        "growth": parse_growth(growth),
        "travel": parse_travel(travel),
    }
    return report


def render_human(report, with_header=True):
    lines = []
    if with_header:
        lines.append("== WorkBuddy积分助手 · 每日播报 ==")
    lines.append("积分余额（总积分）：%s" % report["total_credits"])

    today_in = report["today_checked_in"]
    daily = report.get("daily_credit")
    today_credit = report.get("today_credit")
    if today_in:
        lines.append("今日记录：已记录（今日可得 %s 分）" % today_credit)
    else:
        lines.append("今日记录：未记录（完成一次对话即可被记录登录 1 天，可得 %s 分）" % (
            daily if daily is not None else "—"))

    lines.append("活跃地图（本月）：本月已记录 %d 天，累计 %d 天" % (
        len(report["checkin_dates_this_month"]), report["checkin_dates_total"]))
    if report["checkin_dates_this_month"]:
        lines.append("  本月已记录日：" + "、".join(report["checkin_dates_this_month"]))

    week_done = report["week_progress_done"]
    if week_done is not None:
        total = report.get("week_progress_total")
        rem = report.get("week_remaining")
        suffix = "（还差 %d 天满勤）" % rem if rem else "（本周已全勤）"
        lines.append("本周进度：已完成 %d / %s 天%s" % (week_done, total if total is not None else "?", suffix))

    is_streak = report.get("is_streak_day")
    nxt = report.get("next_streak_day")
    gap = report.get("streak_gap")
    bonus_credit = report.get("streak_bonus_credit")
    bonus_days = report.get("streak_bonus_days")
    streak_tip = ""
    if is_streak is True:
        streak_tip = "（今日为连续登录日）"
    if nxt is not None and nxt > 0:
        if gap:
            streak_tip += "；下一连续奖励节点 %d 天，还差 %d 天" % (nxt, gap)
        else:
            streak_tip += "；已达连续奖励节点 %d 天" % nxt
    if bonus_credit is not None:
        streak_tip += "；连续奖励累计 %s 分（%s 天）" % (bonus_credit, bonus_days if bonus_days is not None else "?")
    if streak_tip:
        lines.append("连续登录：" + streak_tip.lstrip("；"))

    sched = report.get("streak_schedule") or []
    if sched:
        def fmt(t):
            base = "%d天+%d积分+%d能量+%d补登卡+%d抽奖（%s）" % (
                t["streak_days"], t["credit"], t["energy"], t["makeup_card"], t["lottery"], t["type"])
            if t.get("note"):
                base += " · %s" % t["note"]
            return base
        full = " / ".join(fmt(t) for t in STREAK_TIERS)
        first = sched[0]
        lines.append("连续奖励分档（与官方页面一致）：%s" % full)
        lines.append("  下一档：%d 天（还差 %d 天）" % (first["streak_days"], first["remaining"]))

    act = report["activity"]
    lines.append("本期活动：%s（%s / 第%s期）[%s]" % (
        act.get("activity_name"), act.get("theme_name"), act.get("season"), act.get("status")))
    lines.append("  活动周期：%s ~ %s" % (act.get("start_time"), act.get("end_time")))
    if act.get("remaining_days") is not None:
        lines.append("  活动剩余：%d 天；若全勤预计再得 %s 分" % (
            act["remaining_days"], act.get("full_attendance_estimate") if act.get("full_attendance_estimate") is not None else "—"))

    claim = report["claim"]
    lines.append("领取状态：%s" % (claim.get("claim_button_text") or "—"))
    ab = claim.get("action_button") or {}
    if ab.get("show") and ab.get("text"):
        lines.append("活动入口：%s -> %s" % (ab.get("text"), ab.get("action") or "—"))

    if report.get("remind_checkin"):
        daily_txt = ("可得 %s 分" % daily) if daily is not None else "可得积分"
        lines.append("")
        lines.append("⏰ 记录催办：今日尚未完成对话，去 Buddy 加油站完成一次对话即可记录登录 1 天（%s；入口见上「活动入口」）。" % daily_txt)

    g = report.get("growth") or {}
    if g.get("available"):
        lines.append("")
        lines.append("成长计划：")
        prof = g.get("profile")
        if prof:
            if prof.get("level") is not None:
                lines.append("  成长等级：%s" % prof["level"])
            if prof.get("progress") is not None and prof.get("total") is not None:
                lines.append("  成长进度：%s / %s" % (prof["progress"], prof["total"]))
        buddy = g.get("buddy")
        if buddy and buddy.get("name"):
            btxt = "  当前 Buddy：%s" % buddy["name"]
            if buddy.get("tier"):
                btxt += "（%s 级）" % buddy["tier"]
            lines.append(btxt)
        tks = g.get("tasks") or []
        if tks:
            lines.append("  成长任务：")
            for t in tks:
                rc = t.get("reward_credit")
                st = t.get("status")
                lines.append("    · %s（奖励 %s 积分%s）" % (
                    t.get("title"), rc if rc is not None else "?",
                    (" · %s" % st) if st else ""))

    tr = report.get("travel") or {}
    if tr.get("available"):
        lines.append("")
        lines.append("派猫猫旅行：")
        if tr.get("state_text"):
            lines.append("  旅行状态：%s" % tr["state_text"])
        if tr.get("location_name"):
            lines.append("  当前地点：%s" % tr["location_name"])
        if tr.get("remaining_text"):
            lines.append("  到达倒计时：%s" % tr["remaining_text"])
        if tr.get("reward_credit"):
            lines.append("  可领积分：%s 分" % tr["reward_credit"])
        if tr.get("energy") is not None:
            lines.append("  能量余额：%s" % tr["energy"])
        locs = tr.get("locations") or []
        if locs:
            lines.append("  可选地点：%s" % "、".join(str(L.get("name")) for L in locs))
            lines.append("  （各地点时长 / 积分完全相同：随机 1-4 小时，5-10 积分）")
        if tr.get("daily_limit_reached"):
            lines.append("  今日派遣：已达上限，明日再来")
        alog = tr.get("auto_log") or []
        if alog:
            lines.append("  自动执行：")
            for item in alog:
                lines.append("    · %s" % item)

    lines.append("")
    lines.append("暂不支持：积分明细（流水）、兑换 / 抽奖条件——成长计划任务 / 进度 / 当前 Buddy 与派猫猫旅行均已通过只读 GET 接口展示；积分明细与兑换抽奖仍无可用只读接口，本助手只做已验证字段的只读查询，不编造上述数据。")
    return "\n".join(lines)


def render_checkin(result, report):
    lines = []
    lines.append("== WorkBuddy积分助手 · 自动签到 ==")
    act = result.get("action")
    if act == "skipped":
        lines.append("签到结果：今日已签到，无需重复打卡 ✓")
    elif act == "checked_in":
        lines.append("签到结果：✅ 签到成功！%s" % (result.get("message") or ""))
    else:
        lines.append("签到结果：❌ 失败 - %s" % (result.get("message") or "未知错误"))
    lines.append("")
    lines.append(render_human(report, with_header=True))
    return "\n".join(lines)


# ===== 桌面展示（HTML 报告 + 系统通知）=====

def _esc(v):
    if v is None:
        return ""
    s = str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def render_html(report, checkin_result=None):
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M")
    r = report
    streak_days = r.get("streak_days") or 0

    today_txt = "已记录" if r.get("today_checked_in") else "未记录"
    today_cls = "ok" if r.get("today_checked_in") else "warn"

    tier_rows = []
    for t in STREAK_TIERS:
        reached = streak_days >= t["streak_days"]
        remaining = t["streak_days"] - streak_days if not reached else 0
        note = ("（%s）" % t["note"]) if t.get("note") else ""
        tier_rows.append(
            "<tr class='%s'><td>%d 天</td><td>+%d 积分</td><td>+%d 能量</td>"
            "<td>+%d 补登卡</td><td>+%d 抽奖</td><td>%s%s</td><td>%s</td></tr>" % (
                "reached" if reached else "pending",
                t["streak_days"], t["credit"], t["energy"], t["makeup_card"], t["lottery"],
                t["type"], note, ("已达成 ✓" if reached else "还差 %d 天" % remaining)))
    tier_table = "\n".join(tier_rows)

    act = r.get("activity") or {}
    claim = r.get("claim") or {}
    ab = claim.get("action_button") or {}

    checkin_block = ""
    if checkin_result:
        act_c = checkin_result.get("action")
        if act_c == "checked_in":
            ctext = "✅ 签到成功"
        elif act_c == "skipped":
            ctext = "今日已签到，无需重复打卡"
        else:
            ctext = "❌ 失败：" + _esc(checkin_result.get("message") or "未知")
        checkin_block = "<div class='card'><b>签到结果：</b>%s</div>" % ctext

    weekly = ""
    if r.get("week_progress_done") is not None:
        rem = r.get("week_remaining")
        suffix = "（还差 %d 天满勤）" % rem if rem else "（本周已全勤）"
        weekly = "<div class='card'>本周进度：已完成 %d / %s 天 %s</div>" % (
            r["week_progress_done"], r.get("week_progress_total") if r.get("week_progress_total") is not None else "?", suffix)

    month_block = ("<div class='card'>活跃地图（本月）：本月已记录 %d 天，累计 %d 天</div>" % (
        len(r.get("checkin_dates_this_month") or []), r.get("checkin_dates_total") or 0))

    act_block = ("<div class='card'>本期活动：%s（%s / 第%s期）[%s]<br>"
                 "周期：%s ~ %s" % (
        _esc(act.get("activity_name")), _esc(act.get("theme_name")), _esc(act.get("season")),
        _esc(act.get("status")), _esc(act.get("start_time")), _esc(act.get("end_time"))))
    if act.get("remaining_days") is not None:
        est = act.get("full_attendance_estimate")
        act_block += "<br>活动剩余：%d 天；若全勤预计再得 %s 分" % (
            act["remaining_days"], est if est is not None else "—")
    act_block += "</div>"

    claim_block = "<div class='card'>领取状态：%s" % _esc(claim.get("claim_button_text") or "—")
    if ab.get("show") and ab.get("text"):
        claim_block += "<br>活动入口：<a href='%s' target='_blank' rel='noopener noreferrer'>%s</a>" % (
            _esc(ab.get("action") or "#"), _esc(ab.get("text")))
    claim_block += "</div>"

    g = r.get("growth") or {}
    growth_block = ""
    if g.get("available"):
        gparts = []
        prof = g.get("profile")
        if prof:
            if prof.get("level") is not None:
                gparts.append("成长等级：%s" % _esc(prof["level"]))
            if prof.get("progress") is not None and prof.get("total") is not None:
                gparts.append("成长进度：%s / %s" % (_esc(prof["progress"]), _esc(prof["total"])))
        buddy = g.get("buddy")
        if buddy and buddy.get("name"):
            btxt = "当前 Buddy：%s" % _esc(buddy["name"])
            if buddy.get("tier"):
                btxt += "（%s 级）" % _esc(buddy["tier"])
            gparts.append(btxt)
        tks = g.get("tasks") or []
        if tks:
            tlines = []
            for t in tks:
                rc = t.get("reward_credit")
                st = t.get("status")
                tlines.append("· %s（奖励 %s 积分%s）" % (
                    _esc(t.get("title")), _esc(rc if rc is not None else "?"),
                    (" · %s" % _esc(st)) if st else ""))
            gparts.append("成长任务：<br>" + "<br>".join(tlines))
        if gparts:
            growth_block = "<div class='card'><b>成长计划</b><br>" + "<br>".join(gparts) + "</div>"

    tr = r.get("travel") or {}
    travel_block = ""
    if tr.get("available"):
        tparts = []
        if tr.get("state_text"):
            tparts.append("旅行状态：%s" % _esc(tr["state_text"]))
        if tr.get("location_name"):
            tparts.append("当前地点：%s" % _esc(tr["location_name"]))
        if tr.get("remaining_text"):
            tparts.append("到达倒计时：%s" % _esc(tr["remaining_text"]))
        if tr.get("reward_credit"):
            tparts.append("可领积分：%s 分" % _esc(tr["reward_credit"]))
        if tr.get("energy") is not None:
            tparts.append("能量余额：%s" % _esc(tr["energy"]))
        locs = tr.get("locations") or []
        if locs:
            tparts.append("可选地点：%s" % _esc("、".join(str(L.get("name")) for L in locs)))
        if tr.get("daily_limit_reached"):
            tparts.append("今日派遣：<span style='color:#ffd98a'>已达上限，明日再来</span>")
        alog = tr.get("auto_log") or []
        if alog:
            tparts.append("自动执行：<br>" + "<br>".join(_esc(x) for x in alog))
        if tparts:
            travel_block = "<div class='card'><b>派猫猫旅行</b><br>" + "<br>".join(tparts) + "</div>"

    remind = ""
    if r.get("remind_checkin"):
        remind = ("<div class='card warn-card'>⏰ <b>记录催办</b>：今日尚未完成对话，"
                  "去 Buddy 加油站完成一次对话即可记录登录 1 天"
                  "（可得 %s 分）。</div>" % (r.get("daily_credit") if r.get("daily_credit") is not None else "—"))

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkBuddy 积分播报</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Microsoft YaHei","微软雅黑",Arial,sans-serif;
    background: linear-gradient(160deg,#0a1733 0%%,#0f2a52 60%%,#0a1733 100%%); color:#e8f0ff;
    padding:24px; }
  .wrap { max-width: 820px; margin: 0 auto; }
  h1 { font-size:22px; color:#7fd4ff; margin:0 0 4px; letter-spacing:1px; }
  .sub { color:#9fb6d8; font-size:12px; margin-bottom:18px; }
  .stats { display:flex; gap:14px; margin-bottom:16px; flex-wrap:wrap; }
  .stat { flex:1; min-width:150px; background:rgba(20,48,92,.6); border:1px solid #1f4f8f;
    border-radius:12px; padding:16px; text-align:center; }
  .stat .v { font-size:28px; font-weight:700; color:#7fd4ff; }
  .stat .k { font-size:12px; color:#9fb6d8; margin-top:6px; }
  .stat .v.warn { color:#ff8a8a; }
  .card { background:rgba(15,38,74,.55); border:1px solid #1c447f; border-radius:10px;
    padding:12px 14px; margin-bottom:10px; font-size:14px; line-height:1.6; }
  .warn-card { border-color:#a33; background:rgba(80,20,20,.5); color:#ffc9c9; }
  table { width:100%%; border-collapse:collapse; margin-bottom:10px; font-size:13px; }
  th,td { border:1px solid #1c447f; padding:8px 10px; text-align:center; }
  th { background:rgba(31,79,143,.5); color:#bfe0ff; }
  tr.reached td { color:#9affc0; }
  tr.pending td { color:#ffd98a; }
  a { color:#7fd4ff; text-decoration:none; }
  a:hover { text-decoration:underline; }
  .foot { color:#7d93b8; font-size:11px; margin-top:14px; line-height:1.6; }
</style>
</head>
<body>
<div class="wrap">
  <h1>WorkBuddy 积分助手 · 桌面播报</h1>
  <div class="sub">更新时间：%s（北京时间）</div>
  <div class="stats">
    <div class="stat"><div class="v">%s</div><div class="k">积分余额</div></div>
    <div class="stat"><div class="v">%s</div><div class="k">连续登录(天)</div></div>
    <div class="stat"><div class="v %s">%s</div><div class="k">今日记录</div></div>
  </div>
  %s
  %s
  %s
  %s
  %s
  %s
  <div class="card"><b>连续奖励分档</b>（与官方页面一致 7/14/28 节点）</div>
  <table>
    <tr><th>节点</th><th>积分</th><th>能量</th><th>补登卡</th><th>抽奖</th><th>类型</th><th>状态</th></tr>
    %s
  </table>
  %s
  %s
  <div class="foot">暂不支持：积分明细（流水）、兑换/抽奖条件——成长计划任务/进度/当前 Buddy 与派猫猫旅行均已通过只读 GET 接口展示；积分明细与兑换抽奖仍无可用只读接口。本助手仅做已验证字段的只读查询与本地展示，不编造数据。<br>版本 2.1.0 · 派猫猫旅行的「领取 / 派出」仅在显式加 --travel-auto 时执行，且派出前必查每日上限；除签到与旅行外不调用其他写接口。</div>
</div>
</body>
</html>
""" % (
        _esc(ts), _esc(r.get("total_credits")), _esc(streak_days), today_cls, today_txt,
        checkin_block, month_block, weekly, act_block, growth_block, travel_block,
        tier_table, remind, claim_block,
    )
    return html


def write_desktop_report(html):
    """把 HTML 报告写到桌面（覆盖更新）。桌面目录不存在时自动创建。失败静默降级，不影响主结果。"""
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        os.makedirs(desktop, exist_ok=True)
        path = os.path.join(desktop, DESKTOP_REPORT_NAME)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
        sys.stderr.write("[desktop] 已生成桌面报告：%s\n" % path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[desktop] 桌面报告写入失败（不影响主结果）：%s\n" % exc)


def build_toast_content(report, checkin_result=None):
    title = "WorkBuddy 积分播报"
    parts = []
    if checkin_result:
        act_c = checkin_result.get("action")
        if act_c == "checked_in":
            parts.append("✅ 签到成功")
        elif act_c == "skipped":
            parts.append("今日已签到")
        else:
            parts.append("签到失败")
    parts.append("积分 %s" % report.get("total_credits"))
    parts.append("连续 %s 天" % report.get("streak_days"))
    parts.append("今日%s" % ("已记录" if report.get("today_checked_in") else "未记录"))
    act = report.get("activity") or {}
    parts.append("活动%s" % act.get("status"))
    return title, " · ".join(parts)


def notify(title, message):
    """跨平台系统通知（默认触发）。失败静默降级，不影响主结果。

    - Windows：调用同目录 toast.ps1（优先 WinRT Toast，失败回退 Windows Forms 气泡）
    - macOS：   调用 osascript display notification
    - Linux：   调用 notify-send（回退 kdialog）
    """
    system = platform.system()
    try:
        if system == "Windows":
            _notify_windows(title, message)
        elif system == "Darwin":
            _notify_macos(title, message)
        elif system == "Linux":
            _notify_linux(title, message)
        else:
            sys.stderr.write("[toast] 未知操作系统 %r，跳过系统通知\n" % system)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[toast] 通知发送失败（不影响主结果）：%s\n" % exc)


def _notify_windows(title, message):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ps1 = os.path.join(script_dir, "toast.ps1")
    if not os.path.isfile(ps1):
        return
    payload = os.path.join(script_dir, ".toast_payload.json")
    with open(payload, "w", encoding="utf-8") as handle:
        json.dump({"title": title, "message": message}, handle, ensure_ascii=False)
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1, payload],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
    )


def _notify_macos(title, message):
    esc_t = title.replace("\\", "\\\\").replace('"', '\\"')
    esc_m = message.replace("\\", "\\\\").replace('"', '\\"')
    script = 'display notification "%s" with title "%s"' % (esc_m, esc_t)
    subprocess.run(["osascript", "-e", script],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)


def _notify_linux(title, message):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", title, message],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return
    if shutil.which("kdialog"):
        subprocess.run(["kdialog", "--passivepopup", "%s\n%s" % (title, message), "5"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return
    raise RuntimeError("未找到可用的桌面通知工具（notify-send / kdialog）")


def _do_push(report, checkin_result=None, push_config_path=None):
    """自动把积分播报推送到已配置的远程渠道（配置即自动推送）。失败静默降级。"""
    try:
        from push_notify import push_send, render_results, load_push_config
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[push] 推送模块加载失败（不影响主结果）：%s\n" % exc)
        return
    try:
        cfg = load_push_config(push_config_path) if push_config_path else None
        title = "WorkBuddy 积分播报"
        content = render_human(report)
        if checkin_result:
            act = checkin_result.get("action")
            sign = {"checked_in": "✅ 签到成功", "skipped": "今日已签到",
                    "failed": "❌ 签到失败"}.get(act)
            if sign:
                content = sign + "\n\n" + content
        results = push_send(title, content, content_type="markdown", config=cfg, confirm_paid=False)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("[push] 推送失败（不影响主结果）：%s\n" % exc)
        return
    if not results:
        sys.stderr.write("[push] 未配置任何推送渠道，跳过远程推送\n")
        return
    sys.stderr.write("[push] 推送结果：\n" + render_results(results) + "\n")


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    as_checkin = ("checkin" in args) or ("--checkin" in args)
    gen_html = "--html" in args
    no_toast = "--no-toast" in args
    travel_auto_on = "--travel-auto" in args
    # --location N：指定派遣地点（1-4，对应咖啡馆/商场店铺/健身房/古镇客栈），缺省随机
    location_id = None
    for idx, arg in enumerate(args):
        if arg == "--location" and idx + 1 < len(args):
            try:
                location_id = int(args[idx + 1])
            except ValueError:
                sys.stderr.write("[travel] --location 需为整数（1-4），已忽略\n")
    no_push = "--no-push" in args
    push_config_path = None
    for idx, arg in enumerate(args):
        if arg == "--push-config" and idx + 1 < len(args):
            push_config_path = args[idx + 1]
    path = find_login_state()
    if not path:
        sys.stderr.write("未找到本机登录态文件，请先在 WorkBuddy 客户端登录。\n")
        return 1
    try:
        token, domain = load_credentials(path)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write("读取登录态失败：%s\n" % exc)
        return 1

    if as_checkin:
        result = do_checkin(domain, token)
        try:
            data = fetch_status(domain, token)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("签到后查询状态失败：%s\n" % exc)
            return 1
        growth = fetch_growth(domain, token)
        travel_raw, auto_log = collect_travel(token, travel_auto_on, location_id)
        report = build_report(data, growth, travel_raw)
        if auto_log:
            report.setdefault("travel", {})
            report["travel"]["auto_log"] = auto_log
            report["travel"]["available"] = True
        if as_json:
            out = report
            out["checkin_result"] = result
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(render_checkin(result, report))
        if gen_html:
            write_desktop_report(render_html(report, result))
        if not no_toast:
            t = build_toast_content(report, result)
            notify(t[0], t[1])
        if not no_push:
            _do_push(report, result, push_config_path)
        return 0 if result.get("action") != "failed" else 1

    status, body = api_call(domain, token, CHECKIN_STATUS_PATH)
    if status != 200 or body.get("code") != 0:
        sys.stderr.write("查询 Buddy 加油站状态失败：HTTP %s, %s\n" % (status, body.get("msg")))
        return 1
    data = body.get("data", {})
    growth = fetch_growth(domain, token)
    travel_raw, auto_log = collect_travel(token, travel_auto_on, location_id)
    report = build_report(data, growth, travel_raw)
    if auto_log:
        report.setdefault("travel", {})
        report["travel"]["auto_log"] = auto_log
        report["travel"]["available"] = True
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))
        if gen_html:
            write_desktop_report(render_html(report, None))
        if not no_toast:
            t = build_toast_content(report, None)
            notify(t[0], t[1])
        if not no_push:
            _do_push(report, None, push_config_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
