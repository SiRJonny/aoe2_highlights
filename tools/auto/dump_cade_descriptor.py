"""Extract the embedded protobuf FileDescriptorProto for cade_api.proto
from the shipped AoE2DE_s.exe and dump it as text + .descriptor file.

The gRPC service is compiled into the exe with its descriptor blob in
plain serialized FileDescriptorProto form (names visible as strings).

Usage:
    py -3.12 tools/auto/dump_cade_descriptor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from google.protobuf import descriptor_pb2

EXE = Path(r"C:\Program Files (x86)\Steam\steamapps\common\AoE2DE\AoE2DE_s.exe")
OUT_DIR = Path("docs/cade")

NEEDLE = b"\x0a\x0ecade_api.proto"  # field 1 (name), len 14


def find_descriptor(data: bytes) -> tuple[int, bytes]:
    off = data.find(NEEDLE)
    if off < 0:
        raise SystemExit("cade_api.proto descriptor head not found in exe")
    # The blob is a serialized FileDescriptorProto starting at `off`.
    # Find its exact end: protobuf round-trip must reproduce the bytes.
    for coarse in range(256, 1_000_000, 256):
        blob = data[off:off + coarse]
        msg = descriptor_pb2.FileDescriptorProto()
        try:
            ok = msg.MergeFromString(blob)
        except Exception:
            continue
        if not ok or msg.name != "cade_api.proto":
            continue
        if msg.SerializeToString() == blob:
            return off, blob
        # coarse window too big or small: fine-tune
        for L in range(max(2, coarse - 256), coarse + 1):
            blob = data[off:off + L]
            msg = descriptor_pb2.FileDescriptorProto()
            if msg.MergeFromString(blob) and msg.name == "cade_api.proto" \
                    and msg.SerializeToString() == blob:
                return off, blob
    raise SystemExit("descriptor blob end not found")


def type_name(t: int, type_name: str) -> str:
    names = {1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
             6: "fixed64", 7: "fixed32", 8: "bool", 9: "string",
             10: "group", 11: "message", 12: "bytes", 13: "uint32",
             14: "enum", 15: "sfixed32", 16: "sfixed64", 17: "sint32",
             18: "sint64"}
    base = names.get(t, f"type{t}")
    if t == 11 or t == 14:
        short = type_name.rsplit(".", 1)[-1]
        return short
    return base


def label_name(l: int) -> str:
    return {1: "optional", 2: "required", 3: "repeated"}.get(l, "optional")


def render(fd: descriptor_pb2.FileDescriptorProto) -> str:
    out = ['syntax = "proto3";', "", f'package {fd.package};', ""]
    # options of interest
    for opt in ("cc_enable_arenas", "optimize_for"):
        if fd.options.HasField(opt):
            v = getattr(fd.options, opt)
            out.append(f"option {opt} = {v};")
    out.append("")

    enums = {e.name for e in fd.enum_type}
    messages = {m.name for m in fd.message_type}

    def ref(name: str) -> str:
        return name.rsplit(".", 1)[-1]

    def render_enum(e, indent: str) -> list[str]:
        lines = [f"{indent}enum {e.name} {{"]
        for v in e.value:
            lines.append(f"{indent}  {v.name} = {v.number};")
        lines.append(f"{indent}}}")
        return lines

    def render_message(m, indent: str) -> list[str]:
        lines = [f"{indent}message {m.name} {{"]
        for e in m.enum_type:
            lines += render_enum(e, indent + "  ")
        for f in m.field:
            label = label_name(f.label)
            t = type_name(f.type, f.type_name)
            if f.type in (11, 14):
                t = ref(f.type_name)
                if f.type_name.startswith(".") and f.type_name.split(".")[1] not in messages and f.type_name.split(".")[-1] not in messages and f.type_name.split(".")[-1] not in enums:
                    # external type (wkt etc.) - keep fully qualified
                    t = f.type_name.lstrip(".")
            if label == "optional":
                lines.append(f"{indent}  {t} {f.name} = {f.number};")
            else:
                lines.append(f"{indent}  {label} {t} {f.name} = {f.number};")
        if m.HasField("oneof_decl") or m.oneof_decl:
            # group fields by oneof index for readability
            pass
        for n in m.nested_type:
            lines += render_message(n, indent + "  ")
        lines.append(f"{indent}}}")
        return lines

    for e in fd.enum_type:
        out += render_enum(e, "")
        out.append("")
    for m in fd.message_type:
        out += render_message(m, "")
        out.append("")
    if fd.service:
        for s in fd.service:
            out.append(f"service {s.name} {{")
            for meth in s.method:
                inp = ref(meth.input_type)
                retn = ref(meth.output_type)
                if meth.client_streaming and meth.server_streaming:
                    kw = "bidi_stream"
                elif meth.server_streaming:
                    kw = "returns (stream "
                    out.append(f"  rpc {meth.name}({inp}) returns (stream {retn});")
                    continue
                elif meth.client_streaming:
                    out.append(f"  rpc {meth.name}(stream {inp}) returns ({retn});")
                    continue
                else:
                    out.append(f"  rpc {meth.name}({inp}) returns ({retn});")
            out.append("}")
    return "\n".join(out) + "\n"


def main() -> None:
    data = EXE.read_bytes()
    off, blob = find_descriptor(data)
    fd = descriptor_pb2.FileDescriptorProto()
    fd.MergeFromString(blob)
    print(f"descriptor at 0x{off:x}, {len(blob)} bytes")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "cade_api.descriptor").write_bytes(blob)
    (OUT_DIR / "cade_api.proto").write_text(render(fd), encoding="utf-8")
    print(f"wrote {OUT_DIR / 'cade_api.proto'}")
    print(f"wrote {OUT_DIR / 'cade_api.descriptor'}")

    # quick summary
    print("\nservices:")
    for s in fd.service:
        for meth in s.method:
            print(f"  {s.name}.{meth.name}({meth.input_type.split('.')[-1]})"
                  f" -> {meth.output_type.split('.')[-1]}"
                  f"{' server-streaming' if meth.server_streaming else ''}"
                  f"{' client-streaming' if meth.client_streaming else ''}")
    print("\ntop-level messages:")
    for m in fd.message_type:
        print(f"  {m.name}: {len(m.field)} fields")


if __name__ == "__main__":
    sys.exit(main())
