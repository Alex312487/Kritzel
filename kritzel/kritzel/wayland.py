"""A small Wayland client in pure Python.

Wayland's wire format is simple: every message is an object id, a size and an
opcode followed by 32-bit arguments, and the protocol XML files say what the
arguments are.  Speaking it directly keeps Kritzel free of binding libraries
(the pywayland version packaged for Fedora cannot receive events that create
objects, such as "a text field became active").

Usage mirrors the protocol:

    conn = Connection()
    registry = conn.display.get_registry()
    registry.dispatcher["global"] = on_global          # handler(obj, *args)
    compositor = conn.bind(registry, name, "wl_compositor", 4)
    surface = compositor.create_surface()               # requests are methods
"""

from __future__ import annotations

import array
import logging
import os
import socket
import struct
import xml.etree.ElementTree as ET

log = logging.getLogger("kritzel.wayland")

PROTOCOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "protocols")
SERVER_ID_BASE = 0xFF000000


class Message:
    __slots__ = ("name", "args", "destructor", "new_id_iface")

    def __init__(self, el):
        self.name = el.get("name")
        self.destructor = el.get("type") == "destructor"
        # (type, interface, nullable)
        self.args = [(a.get("type"), a.get("interface"), a.get("allow-null") == "true") for a in el.findall("arg")]
        self.new_id_iface = next((i for t, i, _ in self.args if t == "new_id"), None)


class Interface:
    def __init__(self, el):
        self.name = el.get("name")
        self.version = int(el.get("version", "1"))
        self.requests = [Message(m) for m in el.findall("request")]
        self.events = [Message(m) for m in el.findall("event")]
        self.request_index = {m.name: i for i, m in enumerate(self.requests)}


_interfaces: dict[str, Interface] = {}


def interfaces() -> dict[str, Interface]:
    if not _interfaces:
        for name in sorted(os.listdir(PROTOCOL_DIR)):
            if name.endswith(".xml"):
                for el in ET.parse(os.path.join(PROTOCOL_DIR, name)).getroot().findall("interface"):
                    _interfaces[el.get("name")] = Interface(el)
    return _interfaces


class WlObject:
    """Client-side handle of a protocol object.  Requests are methods;
    events are delivered to `dispatcher[event_name](obj, *args)`."""

    def __init__(self, conn: "Connection", oid: int, iface: Interface, version: int):
        self.conn = conn
        self.id = oid
        self.iface = iface
        self.version = version
        self.dispatcher: dict = {}
        self.alive = True

    def __getattr__(self, name):
        iface = self.__dict__.get("iface")
        if iface is None or name not in iface.request_index:
            raise AttributeError(name)
        opcode = iface.request_index[name]

        def request(*args):
            return self.conn._request(self, opcode, args)

        request.__name__ = name
        self.__dict__[name] = request       # cache
        return request

    def __repr__(self):
        return f"<{self.iface.name}@{self.id}>"


class ProtocolError(RuntimeError):
    pass


class Connection:
    def __init__(self):
        self.ifaces = interfaces()
        self.sock = self._connect()
        self.objects: dict[int, WlObject] = {}
        self.zombies: set[int] = set()
        self.free_ids: list[int] = []
        self.next_id = 2
        self.out = bytearray()
        self.inbuf = bytearray()
        self.in_fds: list[int] = []
        self.closed = False
        self.display = WlObject(self, 1, self.ifaces["wl_display"], 1)
        self.objects[1] = self.display
        self.display.dispatcher["error"] = self._on_error
        self.display.dispatcher["delete_id"] = self._on_delete_id

    @staticmethod
    def _connect() -> socket.socket:
        fd = os.environ.get("WAYLAND_SOCKET")
        if fd:
            # KWin hands its input method an already connected socket
            os.environ.pop("WAYLAND_SOCKET", None)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=int(fd))
            os.set_inheritable(sock.fileno(), False)
            return sock
        name = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        path = name if os.path.isabs(name) else os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), name)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            sock.connect(path)
        except OSError as e:
            sock.close()
            raise RuntimeError(f"cannot connect to the Wayland compositor at {path}: {e}") from e
        return sock

    def fileno(self) -> int:
        return self.sock.fileno()

    # -- objects -----------------------------------------------------------

    def _new_object(self, iface_name: str, version: int) -> WlObject:
        if self.free_ids:
            oid = self.free_ids.pop()
        else:
            oid = self.next_id
            self.next_id += 1
        obj = WlObject(self, oid, self.ifaces[iface_name], version)
        self.objects[oid] = obj
        return obj

    def bind(self, registry: WlObject, name: int, iface_name: str, version: int) -> WlObject:
        """wl_registry.bind has an untyped new_id, so it gets its own method."""
        obj = self._new_object(iface_name, version)
        body = struct.pack("=I", name) + _string(iface_name) + struct.pack("=II", version, obj.id)
        self._queue(registry.id, registry.iface.request_index["bind"], body)
        return obj

    def _retire(self, obj: WlObject):
        obj.alive = False
        if self.objects.get(obj.id) is obj:
            del self.objects[obj.id]
            if obj.id < SERVER_ID_BASE:
                self.zombies.add(obj.id)        # the id is ours again after delete_id

    def _on_delete_id(self, _display, oid):
        obj = self.objects.pop(oid, None)
        if obj is not None:
            obj.alive = False
        self.zombies.discard(oid)
        if oid < SERVER_ID_BASE:
            self.free_ids.append(oid)

    def _on_error(self, _display, obj, code, message):
        raise ProtocolError(f"compositor reported a protocol error on {obj!r}: {message} (code {code})")

    # -- sending -----------------------------------------------------------

    def _request(self, obj: WlObject, opcode: int, args):
        if not obj.alive:
            log.debug("request %s on dead object %r ignored", obj.iface.requests[opcode].name, obj)
            return None
        msg = obj.iface.requests[opcode]
        body = bytearray()
        fds = []
        created = None
        it = iter(args)
        try:
            for typ, iface, _nullable in msg.args:
                if typ == "new_id":
                    created = self._new_object(iface, obj.version)
                    body += struct.pack("=I", created.id)
                    continue
                v = next(it)
                if typ == "int":
                    body += struct.pack("=i", int(v))
                elif typ == "uint":
                    body += struct.pack("=I", int(v) & 0xFFFFFFFF)
                elif typ == "fixed":
                    body += struct.pack("=i", int(round(v * 256.0)))
                elif typ == "object":
                    body += struct.pack("=I", v.id if v is not None else 0)
                elif typ == "string":
                    body += _string(v)
                elif typ == "array":
                    body += struct.pack("=I", len(v)) + bytes(v) + b"\0" * (-len(v) % 4)
                elif typ == "fd":
                    fds.append(int(v))
                else:
                    raise TypeError(typ)
        except StopIteration:
            raise TypeError(f"{obj.iface.name}.{msg.name}: too few arguments") from None
        if next(it, _END) is not _END:
            raise TypeError(f"{obj.iface.name}.{msg.name}: too many arguments")
        self._queue(obj.id, opcode, bytes(body), fds)
        if msg.destructor:
            self._retire(obj)
        return created

    def _queue(self, oid: int, opcode: int, body: bytes, fds=()):
        data = struct.pack("=II", oid, ((8 + len(body)) << 16) | opcode) + body
        if fds:
            # file descriptors travel as ancillary data with their message
            self.flush()
            self._send(data, fds)
        else:
            self.out += data
            if len(self.out) > 32768:
                self.flush()

    def flush(self):
        if self.out:
            data, self.out = bytes(self.out), bytearray()
            self._send(data, ())

    def _send(self, data: bytes, fds):
        if self.closed:
            return
        try:
            self.sock.setblocking(True)
            if fds:
                anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", fds))]
                sent = self.sock.sendmsg([data], anc)
                data = data[sent:]
            if data:
                self.sock.sendall(data)
        except OSError as e:
            self.closed = True
            raise ConnectionError(f"lost connection to the compositor: {e}") from e

    # -- receiving ---------------------------------------------------------

    def read(self, block: bool = False) -> bool:
        """Reads what the compositor sent.  Returns False once the connection
        is closed."""
        if self.closed:
            return False
        try:
            self.sock.setblocking(block)
            data, anc, _flags, _addr = self.sock.recvmsg(65536, socket.CMSG_SPACE(28 * 4))
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            self.closed = True
            return False
        for level, typ, cdata in anc:
            if level == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(cdata[: len(cdata) - (len(cdata) % fds.itemsize)])
                self.in_fds.extend(fds)
        if not data:
            self.closed = True
            return False
        self.inbuf += data
        return True

    def dispatch(self):
        """Delivers all complete buffered events to their handlers."""
        buf = self.inbuf
        while len(buf) >= 8:
            oid, word = struct.unpack_from("=II", buf, 0)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8:
                raise ProtocolError("malformed message from the compositor")
            if len(buf) < size:
                break
            body = bytes(buf[8:size])
            del buf[:size]
            obj = self.objects.get(oid)
            if obj is None:
                if oid not in self.zombies:
                    log.debug("event for unknown object %d dropped", oid)
                continue
            if opcode >= len(obj.iface.events):
                log.debug("unknown event %d for %r", opcode, obj)
                continue
            msg = obj.iface.events[opcode]
            try:
                args = self._decode(obj, msg, body)
            except (struct.error, UnicodeDecodeError, KeyError):
                log.exception("could not decode %s.%s", obj.iface.name, msg.name)
                continue
            handler = obj.dispatcher.get(msg.name)
            if handler is not None:
                try:
                    handler(obj, *args)
                except ProtocolError:
                    raise
                except Exception:
                    log.exception("handler for %s.%s failed", obj.iface.name, msg.name)
            if msg.destructor:
                obj.alive = False
                if self.objects.get(oid) is obj and oid >= SERVER_ID_BASE:
                    del self.objects[oid]

    def _decode(self, obj: WlObject, msg: Message, body: bytes):
        args = []
        off = 0
        for typ, iface, _nullable in msg.args:
            if typ == "int":
                args.append(struct.unpack_from("=i", body, off)[0])
                off += 4
            elif typ == "uint":
                args.append(struct.unpack_from("=I", body, off)[0])
                off += 4
            elif typ == "fixed":
                args.append(struct.unpack_from("=i", body, off)[0] / 256.0)
                off += 4
            elif typ == "object":
                args.append(self.objects.get(struct.unpack_from("=I", body, off)[0]))
                off += 4
            elif typ == "new_id":
                nid = struct.unpack_from("=I", body, off)[0]
                off += 4
                new = WlObject(self, nid, self.ifaces[iface], obj.version)
                self.objects[nid] = new
                args.append(new)
            elif typ == "string":
                n = struct.unpack_from("=I", body, off)[0]
                off += 4
                args.append(body[off:off + n - 1].decode("utf-8", "replace") if n else None)
                off += n + (-n % 4)
            elif typ == "array":
                n = struct.unpack_from("=I", body, off)[0]
                off += 4
                args.append(body[off:off + n])
                off += n + (-n % 4)
            elif typ == "fd":
                args.append(self.in_fds.pop(0) if self.in_fds else -1)
        return args

    def roundtrip(self):
        """Blocks until the compositor has processed everything sent so far."""
        done = []
        cb = self.display.sync()
        cb.dispatcher["done"] = lambda *_: done.append(True)
        self.flush()
        while not done:
            if not self.read(block=True):
                raise ConnectionError("compositor closed the connection")
            self.dispatch()


_END = object()


def _string(s) -> bytes:
    if s is None:
        return struct.pack("=I", 0)
    b = s.encode("utf-8") + b"\0"
    return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)
