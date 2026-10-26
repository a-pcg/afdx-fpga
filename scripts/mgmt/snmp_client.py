"""SNMP v1 GET/GETNEXT minimal reference client for the AFDX SNMP agent slice.

Responsibilities
  * encode BER SNMPv1 GET / GETNEXT requests (single varbind, short-form lengths)
  * decode BER SNMPv1 responses and self-check fields/values
  * emit Verilog $readmemh vectors for tb/tb_afdx_snmp_agent.v and the
    expected byte streams that the RTL must reproduce byte-for-byte

Scope boundary: no SNMPv2/v3, no SET, no multi-varbind, no traps, no IP/UDP
encapsulation.  This client is the reference peer for the standalone slice only.

Value model (must match the RTL constants / mgmt_regs wiring):
  sysDescr = b"AFDX dev slice"   sysName = b"afdx-es-01"   ifNumber = 2
  ifDescr  = b"lane0"
  inOctets  = 0x11223344  outOctets = 0x55667788
  inUcast   = 5 (mgmt_regs node 0x0020)   inErrors = 3 (0x0024)
  rtcSec    = 5 (TimeTicks, lower 32 bits) rtcNsec = 3 (Counter32)
"""
from pathlib import Path
import argparse
import json

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "reports/mgmt_snmp"

COMMUNITY = b"public"

# OIDs are tuples of integer arcs.  Keep this list lexicographically sorted:
# GETNEXT relies on table order.
OID_TABLE = [
    ((1, 3, 6, 1, 2, 1, 1, 1, 0), "octet", b"AFDX dev slice", "sysDescr"),
    ((1, 3, 6, 1, 2, 1, 1, 5, 0), "octet", b"afdx-es-01", "sysName"),
    ((1, 3, 6, 1, 2, 1, 2, 1, 0), "int", 2, "ifNumber"),
    ((1, 3, 6, 1, 2, 1, 2, 2, 1, 2, 1), "octet", b"lane0", "ifDescr"),
    ((1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 1), "counter", 0x11223344, "inOctets"),
    ((1, 3, 6, 1, 2, 1, 2, 2, 1, 11, 1), "counter", 5, "inUcastPkts"),
    ((1, 3, 6, 1, 2, 1, 2, 2, 1, 14, 1), "counter", 3, "inErrors"),
    ((1, 3, 6, 1, 2, 1, 2, 2, 1, 16, 1), "counter", 0x55667788, "outOctets"),
    ((1, 3, 6, 1, 4, 1, 55555, 1, 1, 0), "timeticks", 5, "rtcSeconds"),
    ((1, 3, 6, 1, 4, 1, 55555, 1, 2, 0), "counter", 3, "rtcNanoseconds"),
]
BY_OID = {oid: (kind, value, name) for oid, kind, value, name in OID_TABLE}

# request plan: (kind, oid, label)
CASES = [
    ("get", (1, 3, 6, 1, 2, 1, 1, 1, 0), "GET sysDescr"),
    ("get", (1, 3, 6, 1, 2, 1, 1, 5, 0), "GET sysName"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 1, 0), "GET ifNumber"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 2, 1, 2, 1), "GET ifDescr"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 2, 1, 10, 1), "GET inOctets"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 2, 1, 11, 1), "GET inUcastPkts"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 2, 1, 14, 1), "GET inErrors"),
    ("get", (1, 3, 6, 1, 2, 1, 2, 2, 1, 16, 1), "GET outOctets"),
    ("get", (1, 3, 6, 1, 4, 1, 55555, 1, 1, 0), "GET rtcSeconds"),
    ("get", (1, 3, 6, 1, 4, 1, 55555, 1, 2, 0), "GET rtcNanoseconds"),
    ("getnext", (1, 3, 6, 1, 4, 1, 55555, 1, 1, 0), "GETNEXT rtcSeconds -> rtcNanoseconds"),
    ("get", (1, 3, 6, 1, 2, 1, 99, 1, 0), "GET unknown OID -> noSuchName"),
]


def enc_len(n):
    if n < 0 or n > 255:
        raise ValueError("length out of range: %d" % n)
    if n < 0x80:
        return bytes([n])
    return bytes([0x81, n])


def enc_tlv(tag, body):
    return bytes([tag]) + enc_len(len(body)) + body


def enc_int(value):
    """Minimal positive/signed BER INTEGER (enough for v1 request-id / version)."""
    if value == 0:
        return enc_tlv(0x02, b"\x00")
    if value < 0:
        raise ValueError("negative integer not needed in this client")
    out = []
    while value:
        out.append(value & 0xFF)
        value >>= 8
    body = bytes(reversed(out))
    if body[0] & 0x80:
        body = b"\x00" + body
    return enc_tlv(0x02, body)


def enc_oid(oid):
    first = 40 * oid[0] + oid[1]
    body = bytearray([first])
    for arc in oid[2:]:
        if arc < 0:
            raise ValueError("bad oid arc")
        groups = [arc & 0x7F]
        arc >>= 7
        while arc:
            groups.append((arc & 0x7F) | 0x80)
            arc >>= 7
        body.extend(reversed(groups))
    return enc_tlv(0x06, bytes(body))


def enc_counter32(value):
    value &= 0xFFFFFFFF
    body = b"\x00" + value.to_bytes(4, "big")
    return enc_tlv(0x41, body)


def enc_timeticks(value):
    value &= 0xFFFFFFFF
    body = b"\x00" + value.to_bytes(4, "big")
    return enc_tlv(0x43, body)


def enc_value(kind, value):
    if kind == "octet":
        return enc_tlv(0x04, value)
    if kind == "int":
        return enc_tlv(0x02, bytes([value]))
    if kind == "counter":
        return enc_counter32(value)
    if kind == "timeticks":
        return enc_timeticks(value)
    if kind == "null":
        return enc_tlv(0x05, b"")
    raise ValueError("unknown value kind " + kind)


def enc_request(kind, oid, request_id, community=COMMUNITY):
    pdu_type = 0xA0 if kind == "get" else 0xA1
    varbind = enc_tlv(0x30, enc_oid(oid) + enc_value("null", b""))
    pdu_body = enc_int(request_id) + enc_int(0) + enc_int(0) + enc_tlv(0x30, varbind)
    pdu = enc_tlv(pdu_type, pdu_body)
    msg_body = enc_int(0) + enc_tlv(0x04, community) + pdu
    return enc_tlv(0x30, msg_body)


def dec_len(data, pos):
    if pos >= len(data):
        raise ValueError("truncated length")
    first = data[pos]
    if first < 0x80:
        return first, pos + 1
    count = first & 0x7F
    if count == 0 or count > 4 or pos + 1 + count > len(data):
        raise ValueError("unsupported length")
    value = int.from_bytes(data[pos + 1:pos + 1 + count], "big")
    return value, pos + 1 + count


def dec_tlv(data, pos):
    if pos >= len(data):
        raise ValueError("truncated tag")
    tag = data[pos]
    length, pos = dec_len(data, pos + 1)
    end = pos + length
    if end > len(data):
        raise ValueError("truncated value")
    return tag, data[pos:end], end


def dec_int(body):
    if not body:
        raise ValueError("empty INTEGER")
    return int.from_bytes(body, "big", signed=True)


def dec_oid(body):
    if not body:
        raise ValueError("empty OID")
    first = body[0]
    oid = [first // 40, first % 40]
    arc = 0
    for byte in body[1:]:
        arc = (arc << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            oid.append(arc)
            arc = 0
    return tuple(oid)


def decode_response(data):
    tag, msg, end = dec_tlv(data, 0)
    if tag != 0x30 or end != len(data):
        raise ValueError("response is not a single SEQUENCE")
    pos = 0
    tag, version, pos2 = dec_tlv(msg, pos)
    if tag != 0x02:
        raise ValueError("missing version")
    pos = pos2
    tag, community, pos2 = dec_tlv(msg, pos)
    if tag != 0x04:
        raise ValueError("missing community")
    pos = pos2
    tag, pdu, pos2 = dec_tlv(msg, pos)
    if tag != 0xA2:
        raise ValueError("response PDU type is not 0xA2")
    if pos2 != len(msg):
        raise ValueError("trailing bytes after PDU")
    ppos = 0
    tag, rid_body, ppos2 = dec_tlv(pdu, ppos)
    if tag != 0x02:
        raise ValueError("missing request-id")
    request_id = dec_int(rid_body)
    ppos = ppos2
    tag, err_body, ppos2 = dec_tlv(pdu, ppos)
    if tag != 0x02:
        raise ValueError("missing error-status")
    error_status = dec_int(err_body)
    ppos = ppos2
    tag, idx_body, ppos2 = dec_tlv(pdu, ppos)
    if tag != 0x02:
        raise ValueError("missing error-index")
    error_index = dec_int(idx_body)
    ppos = ppos2
    tag, vbl, ppos2 = dec_tlv(pdu, ppos)
    if tag != 0x30:
        raise ValueError("missing varbind list")
    if ppos2 != len(pdu):
        raise ValueError("trailing bytes after varbind list")
    vpos = 0
    tag, vb, vpos2 = dec_tlv(vbl, vpos)
    if tag != 0x30:
        raise ValueError("missing varbind")
    if vpos2 != len(vbl):
        raise ValueError("expected exactly one varbind")
    vbpos = 0
    tag, oid_body, vbpos2 = dec_tlv(vb, vbpos)
    if tag != 0x06:
        raise ValueError("missing varbind OID")
    oid = dec_oid(oid_body)
    vbpos = vbpos2
    tag, value_body, vbpos2 = dec_tlv(vb, vbpos)
    if vbpos2 != len(vb):
        raise ValueError("trailing bytes in varbind")
    if tag == 0x04:
        value_kind, value = "octet", value_body
    elif tag == 0x02:
        value_kind, value = "int", dec_int(value_body)
    elif tag == 0x41:
        if len(value_body) != 5 or value_body[0] != 0:
            raise ValueError("bad Counter32 encoding")
        value_kind, value = "counter", int.from_bytes(value_body[1:], "big")
    elif tag == 0x43:
        if len(value_body) != 5 or value_body[0] != 0:
            raise ValueError("bad TimeTicks encoding")
        value_kind, value = "timeticks", int.from_bytes(value_body[1:], "big")
    elif tag == 0x05 and value_body == b"":
        value_kind, value = "null", None
    else:
        raise ValueError("unsupported value tag 0x%02x" % tag)
    return {"version": dec_int(version), "community": community,
            "request_id": request_id, "error_status": error_status,
            "error_index": error_index, "oid": oid,
            "value_kind": value_kind, "value": value}


def expected_response(kind, oid, request_id):
    """Reference model: what the RTL must emit for this request."""
    def build(status, idx, resp_oid, value_kind, value):
        varbind = enc_tlv(0x30, enc_oid(resp_oid) + enc_value(value_kind, value))
        pdu_body = (enc_int(request_id) + enc_int(status) + enc_int(idx)
                    + enc_tlv(0x30, varbind))
        pdu = enc_tlv(0xA2, pdu_body)
        return enc_tlv(0x30, enc_int(0) + enc_tlv(0x04, COMMUNITY) + pdu)

    if kind == "get":
        if oid not in BY_OID:
            return build(2, 1, oid, "null", b"")
        vkind, value, _ = BY_OID[oid]
        return build(0, 0, oid, vkind, value)
    # getnext
    nxt = None
    for tab_oid, vkind, value, _ in OID_TABLE:
        if tab_oid > oid:
            nxt = (tab_oid, vkind, value)
            break
    if nxt is None:
        return build(2, 1, oid, "null", b"")
    return build(0, 0, nxt[0], nxt[1], nxt[2])


def selftest():
    checks = 0
    for index, (kind, oid, label) in enumerate(CASES, start=1):
        req = enc_request(kind, oid, index)
        exp = expected_response(kind, oid, index)
        parsed = decode_response(exp)
        checks += 1
        assert parsed["request_id"] == index
        assert parsed["error_status"] == (2 if "unknown" in label else 0)
        if parsed["error_status"] == 0:
            vkind, value, _ = BY_OID[parsed["oid"]] if kind == "get" else \
                next((k, v, n) for o, k, v, n in OID_TABLE if o == parsed["oid"])
            assert parsed["value_kind"] == vkind and parsed["value"] == value, label
        else:
            assert parsed["value_kind"] == "null", label
        assert decode_response(exp)["oid"] == parsed["oid"]
        print("selftest %2d OK  %-45s request=%d bytes expected=%d bytes" %
              (index, label, len(req), len(exp)))
    print("SELFTEST_PASS cases=%d checks=%d" % (len(CASES), checks))


def emit_vectors():
    OUT.mkdir(parents=True, exist_ok=True)
    req_pool, req_len, exp_pool, exp_len = [], [], [], []
    meta = []
    for index, (kind, oid, label) in enumerate(CASES, start=1):
        req = enc_request(kind, oid, index)
        exp = expected_response(kind, oid, index)
        req_pool += list(req)
        exp_pool += list(exp)
        req_len.append(len(req))
        exp_len.append(len(exp))
        meta.append({"index": index, "kind": kind, "label": label,
                     "oid": ".".join(str(x) for x in oid),
                     "request_bytes": len(req), "expected_bytes": len(exp)})
    def write(name, values):
        (OUT / name).write_text("".join("%02x\n" % v for v in values))
    write("req_bytes.hex", req_pool)
    write("exp_bytes.hex", exp_pool)
    write("req_len.hex", req_len)
    write("exp_len.hex", exp_len)
    (OUT / "cases.json").write_text(json.dumps(
        {"community": COMMUNITY.decode(), "cases": meta,
         "total_request_bytes": len(req_pool),
         "total_expected_bytes": len(exp_pool)}, indent=2))
    print("VECTORS_WRITTEN cases=%d request_bytes=%d expected_bytes=%d dir=%s" %
          (len(CASES), len(req_pool), len(exp_pool), OUT))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--emit", action="store_true")
    args = ap.parse_args()
    if args.selftest or not (args.selftest or args.emit):
        selftest()
    if args.emit:
        emit_vectors()
