"""A tiny scriptable Wayland compositor speaking the raw wire protocol.

It exists because headless KWin cannot inject stylus input: here the test
plays the compositor and sends exactly the tablet / fractional-scale /
input-method event sequences KWin sends, over a WAYLAND_SOCKET pair like
KWin uses for its virtual keyboard."""
import array, os, socket, struct, time
import xml.etree.ElementTree as ET

class Iface:
    def __init__(self, el):
        self.name = el.get("name"); self.version = int(el.get("version", "1"))
        self.requests = [self._msg(m) for m in el.findall("request")]
        self.events = [self._msg(m) for m in el.findall("event")]
    @staticmethod
    def _msg(m):
        return (m.get("name"), [(a.get("name"), a.get("type"), a.get("interface")) for a in m.findall("arg")])

def load_protocols(paths):
    out = {}
    for p in paths:
        for el in ET.parse(p).getroot().findall("interface"):
            out[el.get("name")] = Iface(el)
    return out

class Mock:
    def __init__(self, sock, protocols, globals_):
        self.sock = sock; self.p = protocols; self.globals = globals_   # [(interface, version)]
        self.objects = {1: "wl_display"}; self.next_id = 0xFF000000 - 1
        self.buf = b""; self.fds = []; self.log = []; self.hooks = {}; self.bound = {}
        self.serial = 100; self.closed = False
    # -- wire ---------------------------------------------------------
    def new_object(self, iface):
        self.next_id += 1; self.objects[self.next_id] = iface; return self.next_id
    def send(self, obj, event, *args):
        iface = self.p[self.objects[obj]]
        op = next(i for i, e in enumerate(iface.events) if e[0] == event)
        body = b""
        for (name, typ, _), v in zip(iface.events[op][1], args):
            if typ in ("int",): body += struct.pack("=i", v)
            elif typ in ("uint", "object", "new_id"): body += struct.pack("=I", v or 0)
            elif typ == "fixed": body += struct.pack("=i", int(round(v * 256)))
            elif typ == "string":
                b = v.encode() + b"\0"; body += struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)
            elif typ == "array": body += struct.pack("=I", len(v)) + v + b"\0" * (-len(v) % 4)
            else: raise ValueError(typ)
        assert len(args) == len(iface.events[op][1]), (event, args)
        self.sock.settimeout(5)
        self.sock.sendall(struct.pack("=II", obj, ((8 + len(body)) << 16) | op) + body)
    def pump(self, seconds=0.0):
        end = time.monotonic() + seconds
        while True:
            self.sock.settimeout(max(0.001, min(0.02, end - time.monotonic())))
            try:
                data, anc, _, _ = self.sock.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
                if not data: self.closed = True; return
                for level, typ, cdata in anc:
                    if level == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                        a = array.array("i"); a.frombytes(cdata[: len(cdata) - len(cdata) % 4]); self.fds += list(a)
                self.buf += data; self._parse()
            except (socket.timeout, BlockingIOError):
                pass
            if time.monotonic() >= end: return
    def _parse(self):
        while len(self.buf) >= 8:
            obj, word = struct.unpack_from("=II", self.buf); size, op = word >> 16, word & 0xFFFF
            if len(self.buf) < size: return
            body, self.buf = self.buf[8:size], self.buf[size:]
            iname = self.objects.get(obj)
            if iname is None: continue
            name, sig = self.p[iname].requests[op]
            args = {}; off = 0
            def u32():
                nonlocal off
                v = struct.unpack_from("=I", body, off)[0]; off += 4; return v
            def string():
                nonlocal off
                n = u32(); s = body[off:off + n - 1].decode() if n else None; off += n + (-n % 4); return s
            for aname, typ, aif in sig:
                if typ == "int": args[aname] = struct.unpack_from("=i", body, off)[0]; off += 4
                elif typ == "fixed": args[aname] = struct.unpack_from("=i", body, off)[0] / 256.0; off += 4
                elif typ in ("uint", "object"): args[aname] = u32()
                elif typ == "string": args[aname] = string()
                elif typ == "array":
                    n = u32(); args[aname] = body[off:off + n]; off += n + (-n % 4)
                elif typ == "fd": args[aname] = self.fds.pop(0)
                elif typ == "new_id":
                    if aif is None:
                        aif = string(); args["_version"] = u32()
                    nid = u32(); args[aname] = nid; self.objects[nid] = aif; args["_iface"] = aif
            self.log.append((iname, obj, name, args))
            self._builtin(iname, obj, name, args)
            hook = self.hooks.get((iname, name))
            if hook: hook(obj, args)
    def _builtin(self, iname, obj, name, a):
        if iname == "wl_display" and name == "sync":
            self.send(a["callback"], "done", 0); self.send(1, "delete_id", a["callback"]); del self.objects[a["callback"]]
        elif iname == "wl_display" and name == "get_registry":
            for i, (g, v) in enumerate(self.globals): self.send(a["registry"], "global", i + 1, g, v)
        elif iname == "wl_registry" and name == "bind":
            self.bound.setdefault(a["_iface"], []).append(a["id"])
            hook = self.hooks.get(("bind", a["_iface"]))
            if hook: hook(a["id"], a)
        elif name == "destroy" or (iname, name) in (("wl_buffer", "destroy"), ("wl_shm_pool", "destroy")):
            self.objects.pop(obj, None)
            if obj < 0xFF000000: self.send(1, "delete_id", obj)
    def requests(self, iname, name):
        return [(o, a) for (i, o, n, a) in self.log if i == iname and n == name]
    def next_serial(self):
        self.serial += 1; return self.serial
