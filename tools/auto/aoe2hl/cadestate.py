"""Python applier for the CadeRemote state-delta patch format.

Format + model registry reverse engineered by the LibreMatch project
(docs/cade/{patcher.rs,model.rs,model2.rs} - AGPL-3.0; used here as a
protocol spec only). The registry is parsed from the Rust model
definition; the state tree is generic (dicts/lists/scalars) so no Rust
types are needed.

Instruction stream (1 byte action id, then action-specific args):
  1 Pop | 2 AssignField | 3 PushField | 4 PushCreateAndAssignField |
  5 ResetField | 6 AssignKey | 7 PushKey | 8 PushCreateAndAssignKey |
  9 ResetKey | 10 Insert | 11 PushCreateAndInsert | 12 Remove |
  13 Swap | 14 Resize

The 2021 registry lags the live game schema (apiVersion 22 appended
fields, and a few scalar types changed). Fields missing from the
registry are inferred from the touching action; their exact scalar size
is found by BACKTRACKING: every guess is a choice point, the patch is
re-applied with the next candidate size after a rollback. Decisions that
survive a whole patch are frozen in self.schema and can be pinned via
save_overrides()/load_overrides().
"""
from __future__ import annotations

import re
import struct
from collections import deque
from pathlib import Path

MODEL_RS = Path(__file__).resolve().parents[3] / "docs" / "cade" / "model.rs"

SCALARS = {
    "u8": "UInt8", "i8": "Int8", "u16": "UInt16", "i16": "Int16",
    "u32": "UInt32", "i32": "Int32", "u64": "UInt64", "i64": "Int64",
    "f32": "Float", "f64": "Double", "bool": "Boolean", "String": "String",
}

POP, ASSIGN_FIELD, PUSH_FIELD, PUSH_CREATE_FIELD = 1, 2, 3, 4
RESET_FIELD, ASSIGN_KEY, PUSH_KEY, PUSH_CREATE_KEY = 5, 6, 7, 8
RESET_KEY, INSERT, PUSH_CREATE_INSERT, REMOVE = 9, 10, 11, 12
SWAP, RESIZE = 13, 14

SIZE_CANDS = ("Int32", "Int16", "UInt8", "Int8", "UInt16", "Boolean",
              "UInt32", "Int64", "String")
_SIZE_BYTES = {"Int8": 1, "UInt8": 1, "Int16": 2, "UInt16": 2,
               "Int32": 4, "UInt32": 4, "Float": 4, "Int64": 8,
               "UInt64": 8, "Double": 8, "Boolean": 1}
_WIDEN_ORDER = ("Int32", "Int64", "Int16", "UInt8", "UInt16", "Int8",
                "UInt32", "Boolean", "String")
_MISSING = object()


class FieldDef:
    __slots__ = ("index", "name", "kind", "ftype", "key_type", "model_type",
                 "cands", "pend", "avoid")

    def __init__(self, index, name, kind, ftype=None, key_type=None,
                 model_type=None):
        self.index = index
        self.name = name
        self.kind = kind      # value | model_ref | list_model | list_value | map_model | map_ref
        self.ftype = ftype
        self.key_type = key_type
        self.model_type = model_type
        self.cands = None
        self.pend = None
        self.avoid = None

    def __repr__(self):
        return (f"<FieldDef {self.index}:{self.name} {self.kind}"
                f" ftype={self.ftype} model={self.model_type}>")


class ModelDef:
    __slots__ = ("type_id", "name", "fields")

    def __init__(self, type_id, name):
        self.type_id = type_id
        self.name = name
        self.fields = {}


def parse_registry(path: Path | None = None) -> dict[int, ModelDef]:
    """Parse the Rust model spec; flattens `#[uncage(extends)]` parents."""
    src = (path or MODEL_RS).read_text(encoding="utf-8")
    block_re = re.compile(
        r"#\[uncage\(type\s*=\s*(\d+)\)\]\s*pub struct (\w+)\s*\{(.*?)\n\}",
        re.S)
    field_re = re.compile(
        r"#\[uncage\((?:index\s*=\s*(\d+)[^)]*)\)\]\s*pub (r#\w+|\w+)\s*:\s*(.+?),?\s*$",
        re.M)
    extends_re = re.compile(
        r"#\[uncage\(extends\)\]\s*pub (\w+)\s*:\s*(\w+)\s*,")
    raw = {}
    for m in block_re.finditer(src):
        type_id, sname, body = int(m.group(1)), m.group(2), m.group(3)
        fields = {}
        parent = None
        for em in extends_re.finditer(body):
            parent = em.group(2)
        for fm in field_re.finditer(body):
            idx, fname, rtype = (int(fm.group(1)),
                                 fm.group(2).replace("r#", ""),
                                 fm.group(3).strip())
            fields[idx] = _field_def(idx, fname, rtype)
        raw[sname] = (type_id, fields, parent)

    def resolve(name: str) -> dict[int, FieldDef]:
        type_id, fields, parent = raw[name]
        merged = {}
        if parent:
            merged.update(resolve(parent))
        merged.update(fields)
        return merged

    registry: dict[int, ModelDef] = {}
    for sname, (type_id, _fields, _parent) in raw.items():
        md = ModelDef(type_id, sname)
        md.fields = resolve(sname)
        registry[type_id] = md
    return registry


def _field_def(idx: int, name: str, rtype: str) -> FieldDef:
    rt = rtype.strip()
    if rt == "Ref":
        return FieldDef(idx, name, "model_ref")
    m = re.fullmatch(r"ModelRef<(\w+)>", rt)
    if m:
        return FieldDef(idx, name, "model_ref", model_type=m.group(1))
    m = re.fullmatch(r"ModelVec<(\w+)>", rt)
    if m:
        return FieldDef(idx, name, "list_model", model_type=m.group(1))
    m = re.fullmatch(r"Model(?:BTree|Hash)Map<\s*(\w+)\s*,\s*(\w+)\s*>", rt)
    if m:
        return FieldDef(idx, name, "map_model", key_type=m.group(1),
                        model_type=m.group(2))
    m = re.fullmatch(r"(?:BTree|Hash)Map<\s*(\w+)\s*,\s*Ref\s*>", rt)
    if m:
        return FieldDef(idx, name, "map_ref", key_type=m.group(1))
    m = re.fullmatch(r"Vec<\s*(\w+)\s*>", rt)
    if m:
        el = m.group(1)
        if el in SCALARS:
            return FieldDef(idx, name, "list_value", ftype=SCALARS[el])
        return FieldDef(idx, name, "list_model", model_type=el)
    if rt in SCALARS:
        return FieldDef(idx, name, "value", ftype=SCALARS[rt])
    raise ValueError(f"unhandled rust type {rtype!r} for field {name}")


# ------------------------------------------------------------------ reader

def _cur_size(label):
    """Stream size of a scalar label; None for dynamic (String)."""
    if label == "ref":
        return 4
    return _SIZE_BYTES.get(label)


class Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def u8(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def i8(self) -> int:
        v = struct.unpack_from("<b", self.buf, self.pos)[0]
        self.pos += 1
        return v

    def i16(self) -> int:
        v = struct.unpack_from("<h", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.buf, self.pos)[0]
        self.pos += 2
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.buf, self.pos)[0]
        self.pos += 4
        return v

    def f64(self) -> float:
        v = struct.unpack_from("<d", self.buf, self.pos)[0]
        self.pos += 8
        return v

    def string(self) -> str:
        ln = self.i32()
        s = self.buf[self.pos:self.pos + ln].decode("utf-8")
        self.pos += ln
        return s

    def boolean(self) -> bool:
        return self.u8() != 0


_SCALAR_READ = {
    "Int8": Reader.i8, "UInt8": Reader.u8, "Int16": Reader.i16,
    "UInt16": Reader.u16, "Int32": Reader.i32, "UInt32": Reader.u32,
    "Int64": Reader.i64, "UInt64": Reader.u64, "Float": Reader.f32,
    "Double": Reader.f64, "String": Reader.string, "Boolean": Reader.boolean,
}


# ------------------------------------------------------------------- state

class State:
    """Generic document: nodes[id] = [model_type, fields_dict]."""

    def __init__(self, registry: dict[int, ModelDef] | None = None):
        self.registry = registry or parse_registry()
        self.nodes: dict[int, list] = {}
        self.next_id = 0
        root_type = 0  # Root
        self.root_id = self._new_node(root_type)
        self.stack: list[int] = []
        self.schema: dict[tuple[int, int], FieldDef] = {}
        self.learned: dict[tuple[int, int], FieldDef] = {}
        self.trace_enabled = False
        self.trace: deque = deque(maxlen=800)
        self._undo: list[tuple] = []
        self._choices: list[tuple] = []
        self._pending: list[FieldDef] = []
        self.parse_only = False
        self._widen_tried: dict = {}
        self._window: deque = deque(maxlen=96)
        self._win_tried: dict = {}
        self._resume_off = 0
        self.stats = {"retries": 0, "guesses": 0, "patches": 0}

    # -- schema ----------------------------------------------------------
    def load_overrides(self, path: Path) -> None:
        import json
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for k, d in data.items():
            mt, fi = k.split(":")
            fd = FieldDef(int(fi), d.get("name", f"f{fi}"), d["kind"],
                          ftype=d.get("ftype"), key_type=d.get("key_type"),
                          model_type=d.get("model_type"))
            self.schema[(int(mt), int(fi))] = fd

    def save_overrides(self, path: Path) -> None:
        import json
        data = {f"{mt}:{fi}": {"name": fd.name, "kind": fd.kind,
                               "ftype": fd.ftype, "key_type": fd.key_type,
                               "model_type": fd.model_type}
                for (mt, fi), fd in self.learned.items()}
        Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")

    def _resolve(self, node: list, field: int, action: int) -> FieldDef:
        key = (node[0], field)
        fd = self.schema.get(key)
        if fd is not None:
            return fd
        fd = self.registry[node[0]].fields.get(field)
        if fd is None:
            name = f"?{field}"
            if action in (PUSH_FIELD, PUSH_CREATE_FIELD):
                fd = FieldDef(field, name, "model_ref")
            elif action in (ASSIGN_KEY, PUSH_KEY, RESET_KEY, REMOVE, SWAP,
                            RESIZE, INSERT, PUSH_CREATE_INSERT,
                            PUSH_CREATE_KEY):
                fd = FieldDef(field, name, "map_ref", key_type="i32")
                fd.cands = ("map_ref", "list_value")
            else:  # ASSIGN_FIELD or RESET_FIELD
                fd = FieldDef(field, name, "value", ftype="Unknown")
                fd.cands = SIZE_CANDS
            self.learned[key] = fd
        self.schema[key] = fd
        return fd

    def _new_node(self, model_type: int) -> int:
        nid = self.next_id
        self.next_id += 1
        self.nodes[nid] = [model_type, {}]
        return nid

    def _node(self, nid: int) -> list:
        node = self.nodes.get(nid)
        if node is None:
            raise KeyError(f"node {nid} gone")
        return node

    def top(self) -> list:
        return self._node(self.stack[-1] if self.stack else self.root_id)

    def _model_name(self, node: list) -> str:
        md = self.registry.get(node[0])
        return md.name if md else f"type{node[0]}"

    # -- undo --------------------------------------------------------------
    def _fset(self, node: list, field: int, value) -> None:
        old = node[1].get(field, _MISSING)
        self._undo.append(("fset", node, field, old))
        node[1][field] = value

    def _dset(self, cont: dict, key, value) -> None:
        old = cont.get(key, _MISSING)
        self._undo.append(("dset", cont, key, old))
        cont[key] = value

    def _lset(self, cont: list, idx: int, value) -> None:
        old = cont[idx] if 0 <= idx < len(cont) else _MISSING
        if old is _MISSING:
            self._undo.append(("lresize", cont, idx))
            while len(cont) <= idx:
                cont.append(None)
        self._undo.append(("lset", cont, idx, old))
        cont[idx] = value

    def _linsert(self, cont: list, idx: int, value) -> None:
        self._undo.append(("ldel", cont, idx))
        cont.insert(idx, value)

    def _rollback(self, depth: int) -> None:
        for entry in reversed(self._undo):
            kind = entry[0]
            if kind == "fset":
                _, node, field, old = entry
                if old is _MISSING:
                    node[1].pop(field, None)
                else:
                    node[1][field] = old
            elif kind == "dset":
                _, cont, key, old = entry
                if old is _MISSING:
                    cont.pop(key, None)
                else:
                    cont[key] = old
            elif kind == "lset":
                _, cont, idx, old = entry
                if old is _MISSING:
                    while len(cont) > idx:
                        cont.pop()
                else:
                    cont[idx] = old
            elif kind == "ldel":
                _, cont, idx = entry
                cont.pop(idx)
            elif kind == "lresize":
                _, cont, old_len = entry
                if isinstance(cont, list):
                    del cont[old_len:]
            elif kind == "lswap":
                _, cont, a, b = entry
                cont[a], cont[b] = cont[b], cont[a]
            elif kind == "linsert":
                _, cont, idx, old = entry
                cont.insert(idx, old)
        del self._undo[:]
        del self.stack[depth:]

    # -- value helpers -----------------------------------------------------
    def _read_value(self, r: Reader, ftype: str):
        return _SCALAR_READ[ftype](r)

    def _field_container(self, node: list, fdef: FieldDef, create: bool):
        v = node[1].get(fdef.index)
        if v is None and create:
            if fdef.kind in ("map_model", "map_ref"):
                v = {}
            elif fdef.kind in ("list_model", "list_value"):
                v = []
            else:
                raise ValueError(f"field {fdef.name} is {fdef.kind}")
            node[1][fdef.index] = v
        return v

    def _read_unknown(self, r: Reader, node: list, fdef: FieldDef,
                      instr_off: int | None = None):
        """Read an unknown scalar; size chosen by deep lookahead scoring."""
        return self._decide(r, node, fdef, is_key=False,
                            instr_off=instr_off)

    def _read_map_or_value(self, r: Reader, node: list, fdef: FieldDef,
                           instr_off: int | None = None):
        """AssignKey on a learned container: ref id or scalar."""
        return self._decide(r, node, fdef, is_key=True,
                            instr_off=instr_off)

    # -- deep-lookahead decision ------------------------------------------
    def _stack_types(self) -> list[int]:
        out = []
        for nid in self.stack:
            node = self.nodes.get(nid)
            out.append(node[0] if node else -1)
        return out

    def _kind_of(self, mt: int, field: int):
        """(kind, ftype, model_type, known) without learning."""
        if mt < 0:
            return None
        key = (mt, field)
        fd = self.schema.get(key)
        if fd is not None:
            return fd.kind, fd.ftype, fd.model_type, True
        md = self.registry.get(mt)
        if md is None:
            return None
        fd = md.fields.get(field)
        if fd is None:
            return None
        return fd.kind, fd.ftype, fd.model_type, True

    def _simulate(self, buf: bytes, pos: int, types: list[int], k: int = 24):
        """Structurally parse up to k instructions; returns (score, end).

        Neutral on unknown fields (4-byte default); errors abort."""
        score = 0
        t = list(types)
        n = len(buf)
        p = pos
        for _ in range(k):
            if p >= n:
                score += 5          # reached the patch end: strong signal
                return score, p
            action = buf[p]
            if not 1 <= action <= 14:
                return -1000, p
            p += 1
            mt = t[-1] if t else -1

            def kind(f, _mt=mt):
                return self._kind_of(_mt, f)

            if action == POP:
                if not t:
                    return -1000, p
                t.pop()
                score += 1
            elif action == ASSIGN_FIELD:
                f = buf[p]
                p += 1
                ki = kind(f)
                if ki is None:
                    p += 4
                    score += 1
                else:
                    kkind, ftype, _m, _kn = ki
                    if kkind != "value":
                        return -1000, p
                    size = _SIZE_BYTES.get(ftype)
                    if ftype == "String":
                        if p + 4 > n:
                            return -1000, p
                        ln = struct.unpack_from("<i", buf, p)[0]
                        if not 0 <= ln <= 4096 or p + 4 + ln > n:
                            return -1000, p
                        p += 4 + ln
                    elif size:
                        p += size
                    else:
                        p += 4
                    score += 4
            elif action == PUSH_FIELD:
                f = buf[p]
                p += 1
                ki = kind(f)
                if ki is None:
                    t.append(-1)
                    score += 1
                else:
                    kkind, _ftype, mdl, _kn = ki
                    if kkind != "model_ref":
                        return -1000, p
                    t.append(self._model_type_id(mdl))
                    score += 4
            elif action in (PUSH_CREATE_FIELD, PUSH_CREATE_INSERT):
                f = buf[p]
                mt2 = buf[p + 1]
                p += 2
                if action == PUSH_CREATE_INSERT:
                    p += 4
                t.append(mt2)
                score += 4
            elif action == RESET_FIELD:
                p += 1
                score += 1
            elif action in (ASSIGN_KEY, PUSH_KEY, RESET_KEY, REMOVE, RESIZE):
                f = buf[p]
                p += 5
                ki = kind(f)
                if ki is None:
                    score += 1
                    if action == ASSIGN_KEY:
                        p += 4
                else:
                    kkind, ftype, _m, _kn = ki
                    if action == ASSIGN_KEY:
                        if kkind == "list_value":
                            size = _SIZE_BYTES.get(ftype, 4)
                            if ftype == "String":
                                if p + 4 > n:
                                    return -1000, p
                                ln = struct.unpack_from("<i", buf, p)[0]
                                if not 0 <= ln <= 4096 or p + 4 + ln > n:
                                    return -1000, p
                                p += 4 + ln
                            else:
                                p += size
                        elif kkind == "map_ref":
                            p += 4
                        else:
                            return -1000, p
                        score += 4
                    elif action == PUSH_KEY:
                        if kkind not in ("map_ref", "map_model", "list_model"):
                            return -1000, p
                        if kkind in ("map_model", "list_model"):
                            t.append(self._model_type_id(_m))
                        else:
                            t.append(-1)
                        score += 4
                    else:
                        score += 2
            elif action == PUSH_CREATE_KEY:
                f = buf[p]
                mt2 = buf[p + 1]
                p += 6
                t.append(mt2)
                score += 4
            elif action == INSERT:
                f = buf[p]
                p += 5
                ki = kind(f)
                if ki is None:
                    p += 4
                    score += 1
                else:
                    kkind, ftype, _m, _kn = ki
                    if kkind == "list_value":
                        size = _SIZE_BYTES.get(ftype, 4)
                        p += size
                    elif kkind == "map_ref":
                        p += 4
                    else:
                        return -1000, p
                    score += 3
            elif action == SWAP:
                p += 9
                score += 2
            else:
                return -1000, p
        return score, p

    def _model_type_id(self, name) -> int:
        if not name:
            return -1
        mid = getattr(self, "_name2id", None)
        if mid is None:
            mid = {m.name: t for t, m in self.registry.items()}
            self._name2id = mid
        return mid.get(name, -1)

    def _decide(self, r: Reader, node: list, fdef: FieldDef, is_key: bool,
                instr_off: int | None = None):
        """Pick the encoding of an unknown field by deep lookahead."""
        start = r.pos
        if instr_off is None:
            instr_off = start - (6 if is_key else 2)
        types = self._stack_types()
        if is_key:
            cands = [("ref", 4)]
            cands += [(c, _SIZE_BYTES[c]) for c in SIZE_CANDS
                      if c != "String"]
            cands.append(("String", -1))
        else:
            cands = [(c, _SIZE_BYTES[c]) for c in SIZE_CANDS
                     if c != "String"]
            cands.append(("String", -1))
        best = None
        for label, size in cands:
            if fdef.avoid and label in fdef.avoid:
                continue
            if label == "String":
                if start + 4 > len(r.buf):
                    continue
                ln = struct.unpack_from("<i", r.buf, start)[0]
                if not 0 <= ln <= 4096 or start + 4 + ln > len(r.buf):
                    continue
                body = r.buf[start + 4:start + 4 + ln]
                if not body or sum(1 for b in body if 32 <= b < 127) \
                        < len(body) * 0.9:
                    continue     # real strings are text; do not swallow binary
                consume = 4 + ln
            else:
                consume = size
            end = start + consume
            if end > len(r.buf):
                continue
            sc, _ = self._simulate(r.buf, end, types)
            if sc <= -100:
                continue
            if best is None or sc > best[0]:
                best = (sc, label, consume)
        if best is None:
            raise ValueError(
                f"no feasible size for unknown field {fdef.name} of "
                f"{self._model_name(node)} at offset {start}")
        _sc, label, consume = best
        labels_all = [l for l, _s in cands]
        self._choices.append((fdef, label, labels_all, len(self._undo),
                              len(self.stack), instr_off))
        self.stats["guesses"] += 1
        if fdef.avoid is None:
            fdef.avoid = set()
            self._pending.append(fdef)
        fdef.avoid.add(label)
        if is_key:
            if label == "ref":
                fdef.kind = "map_ref"
                fdef.ftype = None
                return "ref", r.i32()
            fdef.kind = "list_value"
            fdef.ftype = label
            return "value", self._read_value(r, label)
        fdef.ftype = label
        return self._read_value(r, label)

    # -- patch ------------------------------------------------------------
    _SIZE_CYCLE = ("Int32", "Int64", "Int16", "UInt8", "String")

    def _run(self, patch: bytes, cap: int) -> None:
        """One patch pass with DFS backtracking over field encodings.

        Two flip sources, both resuming AT the flipped instruction (the
        undo log restores the structural state, so a flip costs only the
        suffix re-parse):
        - unknown-field choices recorded by _decide;
        - a bounded window of recent scalar assignments (v22 changed some
          2021 scalar sizes, so registry-known fields may need re-typing
          even though they never open a choice on their own)."""
        start_depth = len(self.stack)
        self.stats["patches"] += 1
        pre_learned = set(self.learned)
        retries = 0
        restarts = 0
        off = 0
        while True:
            if off == 0:
                # full (re)start: wipe speculative state; on resume the
                # undo log / window / stack prefix stay valid
                self._undo.clear()
                self._choices.clear()
                self._window.clear()
                del self.stack[start_depth:]
            try:
                self._apply(patch, off)
                for fd in self._pending:
                    fd.avoid = None
                self._pending.clear()
                return
            except Exception as e:
                flipped = False
                while self._choices:
                    fdef, label, labels_all, ulen, slen, coff = \
                        self._choices.pop()
                    if self.trace_enabled:
                        print(f"  flip @{coff} {fdef.name}"
                              f" {label} -> avoid+1")
                    if fdef.avoid is None:
                        fdef.avoid = set()
                        self._pending.append(fdef)
                    fdef.avoid.add(label)
                    if any(l not in fdef.avoid for l in labels_all):
                        self.stats["retries"] += 1
                        retries += 1
                        # restore the structural state to the choice point
                        while len(self._undo) > ulen:
                            self._undo_one()
                        del self.stack[slen:]
                        self._prune_window()
                        off = coff
                        flipped = True
                        break
                    # exhausted: unlearn this field, keep backtracking
                    fdef.avoid = None
                    fdef.ftype = "Unknown" if fdef.kind in ("value",
                                                            "list_value") \
                        else None
                if not flipped:
                    if self._flip_window(off):
                        off = self._resume_off
                        continue
                    # window exhausted: unlearn the fields this patch
                    # taught us and restart (avoid sets on surviving
                    # schema entries steer the next combination)
                    self._rollback(start_depth)
                    unlearned = 0
                    for key in [k for k in list(self.learned)
                                if k not in pre_learned]:
                        self.learned.pop(key)
                        self.schema.pop(key, None)
                        unlearned += 1
                    restarts += 1
                    if not unlearned or restarts > 60 or retries > cap:
                        self._rollback(start_depth)
                        self._pending.clear()
                        raise ValueError(
                            f"{e} | retries={retries} restarts={restarts} "
                            f"choices exhausted") from e
                    self.stats["retries"] += 1
                    retries += 1
                    off = 0

    def _prune_window(self) -> None:
        """Drop window entries invalidated by a rollback."""
        keep = deque()
        ulen = len(self._undo)
        for entry in self._window:
            if entry[5] <= ulen:
                keep.append(entry)
        self._window.clear()
        self._window.extend(keep)

    def _flip_window(self, fail_off: int) -> bool:
        """Re-type a recent windowed scalar assignment; prefer the
        instruction whose END lands nearest the failure offset (its size
        is what pushed the stream off the rails). Sets self._resume_off
        and returns True when a flip was made."""
        best = None
        for i in range(len(self._window) - 1, -1, -1):
            off, action, field, mt, cur_label, ulen, slen = self._window[i]
            key = (mt, field)
            tried = self._win_tried.setdefault((key, off), {cur_label})
            cands = self._SIZE_CYCLE if action == ASSIGN_FIELD \
                else ("ref",) + self._SIZE_CYCLE
            nxt = None
            for label in cands:
                if label in tried:
                    continue
                if label != "String" and label != "ref" \
                        and _cur_size(cur_label) is not None \
                        and _cur_size(label) == _cur_size(cur_label):
                    continue     # same stream size: parse-identical
                nxt = label
                break
            if nxt is not None:
                best = (i, off, action, field, mt, cur_label, nxt,
                        ulen, slen, key)
                break
        if best is None:
            return False
        i, off, action, field, mt, cur_label, nxt, ulen, slen, key = best
        self._win_tried[(key, off)].add(nxt)
        fd2 = FieldDef(field, f"w{field}_{nxt}",
                       "map_ref" if nxt == "ref" else
                       ("list_value" if action == ASSIGN_KEY else "value"),
                       ftype=None if nxt == "ref" else nxt)
        self.schema[key] = fd2
        self.learned[key] = fd2
        self.stats["retries"] += 1
        while len(self._undo) > ulen:
            self._undo_one()
        del self.stack[slen:]
        del self._window[i]
        self._prune_window()
        if self.trace_enabled:
            print(f"  window-flip @{off} model{mt} f{field} "
                  f"{cur_label} -> {nxt} (fail @{fail_off})")
        self._resume_off = off
        return True

    def _undo_one(self) -> None:
        entry = self._undo.pop()
        kind = entry[0]
        if kind == "fset":
            _, node, field, old = entry
            if old is _MISSING:
                node[1].pop(field, None)
            else:
                node[1][field] = old
        elif kind == "dset":
            _, cont, key, old = entry
            if old is _MISSING:
                cont.pop(key, None)
            else:
                cont[key] = old
        elif kind == "lset":
            _, cont, idx, old = entry
            if old is _MISSING:
                while len(cont) > idx:
                    cont.pop()
            else:
                cont[idx] = old
        elif kind == "ldel":
            _, cont, idx = entry
            cont.pop(idx)
        elif kind == "linsert":
            _, cont, idx, old = entry
            cont.insert(idx, old)
        elif kind == "lresize":
            _, cont, old_len = entry
            if isinstance(cont, list):
                del cont[old_len:]
        elif kind == "lswap":
            _, cont, a, b = entry
            cont[a], cont[b] = cont[b], cont[a]

    def discover(self, patch: bytes) -> None:
        """Parse-only pass that resolves unknown-field encodings via DFS.

        Cheaper than apply+rollback: only scalar stores are skipped, the
        structural skeleton (nodes, refs, containers) is built for real.
        Run before apply() so the real pass has no open choices.
        """
        self.parse_only = True
        try:
            self._run(patch, cap=4000)
        finally:
            self.parse_only = False

    def apply(self, patch: bytes) -> None:
        """Apply one patch (schema should already be resolved by discover)."""
        self.stats["patches"] += 1
        self._run(patch, cap=400)

    def _apply(self, patch: bytes, start_off: int = 0) -> None:
        r = Reader(patch, start_off)
        while r.pos < len(r.buf):
            off = r.pos
            action = r.u8()
            node0 = self.top()[0]
            self._last_instr = (off, action, -1, node0)
            if self.trace_enabled:
                node = self.top()
                nxt = r.buf[off + 1] if off + 1 < len(r.buf) else -1
                self.trace.append((off, action, nxt, node[0],
                                   len(self.stack)))
            if action == POP:
                if self.stack:
                    self.stack.pop()
            elif action == ASSIGN_FIELD:
                field = r.u8()
                node = self.top()
                fdef = self._resolve(node, field, action)
                if fdef.kind != "value":
                    raise ValueError(
                        f"AssignField on {fdef.kind} field {fdef.name}")
                if fdef.ftype == "Unknown":
                    val = self._read_unknown(r, node, fdef, instr_off=off)
                else:
                    val = self._read_value(r, fdef.ftype)
                self._window.append((off, action, field, node[0],
                                     fdef.ftype, len(self._undo),
                                     len(self.stack)))
                if not self.parse_only:
                    self._fset(node, field, val)
            elif action == PUSH_FIELD:
                field = r.u8()
                node = self.top()
                fdef = self._resolve(node, field, action)
                if fdef.kind != "model_ref":
                    raise ValueError(
                        f"PushField on {fdef.kind} field {fdef.name} "
                        f"(model_type {node[0]}, offset {r.pos - 2})")
                target = node[1].get(field)
                if not isinstance(target, int):
                    raise KeyError(
                        f"PushField on unset ref {fdef.name} of "
                        f"{self._model_name(node)} (offset {r.pos - 2})")
                self.stack.append(target)
            elif action == PUSH_CREATE_FIELD:
                field = r.u8()
                model_type = r.u8()
                node = self.top()
                self._resolve(node, field, action)
                nid = self._new_node(model_type)
                self._undo.append(("fset", node, field,
                                   node[1].get(field, _MISSING)))
                node[1][field] = nid
                self.stack.append(nid)
            elif action == RESET_FIELD:
                field = r.u8()
                node = self.top()
                self._resolve(node, field, action)
                old = node[1].pop(field, _MISSING)
                if old is not _MISSING:
                    self._undo.append(("fset", node, field, old))
            elif action == ASSIGN_KEY:
                field = r.u8()
                key = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cur_label = "ref" if fdef.kind == "map_ref" \
                    else (fdef.ftype or "Unknown")
                self._window.append((off, action, field, node[0],
                                     cur_label, len(self._undo),
                                     len(self.stack)))
                if fdef.kind in ("map_ref", "list_value") \
                        and fdef.cands and fdef.cands[0] in ("map_ref",
                                                             "list_value") \
                        and fdef.kind == "map_ref" \
                        and fdef.name.startswith("?"):
                    # undecided container: choice between ref map and
                    # scalar list/map
                    kind, val = self._read_map_or_value(r, node, fdef,
                                                        instr_off=off)
                    cont = self._field_container(node, fdef, True)
                    if isinstance(cont, dict) and kind == "ref":
                        self._dset(cont, key, val)
                    elif isinstance(cont, dict):
                        self._dset(cont, key, val)
                    else:
                        self._lset(cont, key, val)
                    continue
                cont = self._field_container(node, fdef, True)
                if fdef.kind == "list_value":
                    val = (self._read_unknown(r, node, fdef, instr_off=off)
                           if fdef.ftype == "Unknown"
                           else self._read_value(r, fdef.ftype))
                    if not self.parse_only:
                        self._lset(cont, key, val)
                elif fdef.kind == "map_ref":
                    self._dset(cont, key, r.i32())
                else:
                    raise ValueError(
                        f"AssignKey on {fdef.kind} field {fdef.name}")
            elif action == PUSH_KEY:
                field = r.u8()
                key = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cont = node[1].get(field)
                if cont is None:
                    raise KeyError(
                        f"PushKey on missing {fdef.name} of "
                        f"{self._model_name(node)} (offset {r.pos - 2})")
                if isinstance(cont, dict):
                    target = cont.get(key)
                else:
                    target = cont[key] if 0 <= key < len(cont) else None
                if not isinstance(target, int):
                    raise KeyError(
                        f"PushKey {self._model_name(node)}.{fdef.name}"
                        f"[{key}] -> {target!r} (offset {r.pos - 2})")
                self.stack.append(target)
            elif action == PUSH_CREATE_KEY:
                field = r.u8()
                model_type = r.u8()
                key = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cont = self._field_container(node, fdef, True)
                nid = self._new_node(model_type)
                if isinstance(cont, dict):
                    self._dset(cont, key, nid)
                else:
                    self._lset(cont, key, nid)
                self.stack.append(nid)
            elif action == RESET_KEY:
                field = r.u8()
                key = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cont = node[1].get(field)
                if isinstance(cont, dict):
                    old = cont.pop(key, _MISSING)
                    if old is not _MISSING:
                        self._undo.append(("dset", cont, key, old))
                elif isinstance(cont, list) and 0 <= key < len(cont):
                    self._undo.append(("lset", cont, key, cont[key]))
                    cont[key] = None
            elif action == INSERT:
                field = r.u8()
                index = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                if fdef.kind not in ("list_value", "map_ref"):
                    raise ValueError(
                        f"Insert on {fdef.kind} field {fdef.name}")
                cont = self._field_container(node, fdef, True)
                if fdef.kind == "list_value":
                    val = (self._read_unknown(r, node, fdef, instr_off=off)
                           if fdef.ftype == "Unknown"
                           else self._read_value(r, fdef.ftype))
                    if not self.parse_only:
                        self._linsert(cont, index, val)
                else:
                    self._linsert(cont, index, r.i32())
            elif action == PUSH_CREATE_INSERT:
                field = r.u8()
                model_type = r.u8()
                index = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cont = self._field_container(node, fdef, True)
                nid = self._new_node(model_type)
                self._linsert(cont, index, nid)
                self.stack.append(nid)
            elif action == REMOVE:
                field = r.u8()
                index = r.i32()
                node = self.top()
                self._resolve(node, field, action)
                cont = node[1].get(field)
                if isinstance(cont, list) and 0 <= index < len(cont):
                    old = cont.pop(index)
                    self._undo.append(("linsert", cont, index, old))
                elif isinstance(cont, dict):
                    old = cont.pop(index, _MISSING)
                    if old is not _MISSING:
                        self._undo.append(("dset", cont, index, old))
            elif action == SWAP:
                field = r.u8()
                a = r.i32()
                b = r.i32()
                node = self.top()
                self._resolve(node, field, action)
                cont = node[1].get(field)
                if isinstance(cont, list):
                    if max(a, b) < len(cont):
                        self._undo.append(("lswap", cont, a, b))
                        cont[a], cont[b] = cont[b], cont[a]
                elif isinstance(cont, dict):
                    self._undo.append(("lswap", cont, a, b))
                    cont[a], cont[b] = cont[b], cont[a]
            elif action == RESIZE:
                field = r.u8()
                new_len = r.i32()
                node = self.top()
                fdef = self._resolve(node, field, action)
                cont = node[1].get(field)
                if isinstance(cont, dict):
                    if not fdef.name.startswith("?"):
                        raise ValueError(
                            f"RESIZE on registry map {fdef.name} of "
                            f"{self._model_name(node)}")
                    # learned as map but resized => it is a list
                    self._choices.append((fdef, len(self._undo),
                                          len(self.stack)))
                    self._undo.append(("fset", node, field, cont))
                    fdef.cands = ("list_model", "list_value")
                    fdef.kind = "list_model"
                    if fdef.pend is None:
                        fdef.pend = 0
                        self._pending.append(fdef)
                    cont = []
                    node[1][field] = cont
                elif cont is None:
                    cont = self._field_container(node, fdef, True)
                self._undo.append(("lresize", cont, len(cont)))
                if not self.parse_only:
                    if fdef.kind == "list_value":
                        fill = "" if fdef.ftype == "String" else 0
                        while len(cont) < new_len:
                            cont.append(fill)
                    else:
                        while len(cont) < new_len:
                            cont.append(None)
                if isinstance(cont, list):
                    del cont[new_len:]
            else:
                raise ValueError(
                    f"unknown patch action {action} at offset {r.pos - 1}")
        if r.pos != len(r.buf):
            raise ValueError(f"patch trailing bytes: {len(r.buf) - r.pos}")

    def _list_assign(self, cont: list, index: int, value) -> None:
        self._lset(cont, index, value)

    # -- query helpers -----------------------------------------------------
    def dig(self, *path):
        """Follow (field, key-or-None) pairs from the root.

        key=None descends a model_ref field; an int key indexes a
        map/list entry."""
        node = self._node(self.root_id)
        for i in range(0, len(path), 2):
            field, key = path[i], path[i + 1]
            fdef = self.registry[node[0]].fields[field]
            v = node[1].get(field)
            if key is None:
                if fdef.kind == "model_ref":
                    node = self._node(v)
                    continue
                raise ValueError(f"dig into {fdef.kind} needs a key")
            if not isinstance(v, (dict, list)):
                raise ValueError(f"{fdef.name} is {fdef.kind}, not container")
            node = self._node(v[key])
        return node

    def world(self) -> list:
        w = self._node(self.root_id)[1].get(0)
        if w is None:
            raise KeyError("Root.world not set yet")
        return self._node(w)
