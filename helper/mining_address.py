# encoding: utf-8
import binascii
from constants import ADDRESS_PREFIX

# Bech32 constants
CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

def _bech32_polymod(values):
    GEN = [0x98f2bc8e61, 0x79b76d99e2, 0xf33e5fb3c4, 0xae2eabe2a8, 0x1e4f43e470]
    chk = 1
    for v in values:
        b = chk >> 35
        chk = (chk & 0x07ffffffff) << 5 ^ v
        for i in range(5):
            if (b >> i) & 1:
                chk ^= GEN[i]
    return chk ^ 1

def _bech32_create_checksum(prefix: str, data):
    values = [ord(c) & 0x1f for c in prefix] + [0] + data + [0] * 8
    mod = _bech32_polymod(values)
    return [(mod >> (5 * (7 - i))) & 0x1f for i in range(8)]

def _convertbits(data, frombits: int, tobits: int, pad: bool = True):
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append(0)
    return ret

def _encode_bech32(prefix: str, payload: bytes, version: int) -> str:
    combined = bytes([version]) + payload
    data_5bit = _convertbits(combined, 8, 5)
    checksum = _bech32_create_checksum(prefix, data_5bit)
    return prefix + ":" + "".join(CHARSET[b] for b in data_5bit + checksum)


def parse_payload(payload: str):
    if not payload:
        return "", ""

    try:
        buf = binascii.unhexlify(payload)

        # Minimal length check
        if len(buf) < 20:
            return payload, ""

        version = buf[16]
        script_len = buf[18]
        script_start = 19
        script_end = script_start + script_len

        if script_end > len(buf):
            return payload, ""

        script = buf[script_start:script_end]
        extra_data = buf[script_end:]

        # Handle 0xaa prefix → version 8 (P2SH-like)
        if script and script[0] == 0xaa:
            version = 8
            script = script[1:]

        # Standard case: first byte of script is push length (OP_PUSHBYTES_n)
        if script and script[0] < 0x76:  # 0x4f = OP_PUSHBYTES_75, 0x50–0x60 reserved, so <0x76 is safe
            addr_len = script[0]
            if len(script) < 1 + addr_len:
                return payload, ""

            addr_payload = script[1:1 + addr_len]
            message = extra_data.rstrip(b'\x00').decode('utf-8', errors='ignore')

            address = _encode_bech32(ADDRESS_PREFIX, addr_payload, version)
            return address, message

        # Fallback: unknown script format
        return payload, ""

    except Exception:
        return payload, ""


def retrieve_miner_info_from_payload(payload: str):
    """Returns (miner_message, miner_address) or (None, None) on failure"""
    try:
        address, message = parse_payload(payload)
        if address == payload:
            return None, None
        return message, address
    except Exception:
        return None, None


def get_miner_payload_from_block(block: dict):
    for tx in block.get("transactions", []):
        if tx.get("subnetworkId") == "0100000000000000000000000000000000000000":
            return tx.get("payload")
    return None