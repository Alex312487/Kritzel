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
HEADER_SIZE = 8             # object id, then size and opcode sharing a word
MAX_FDS_PER_READ = 28       # what libwayland allows in one message

_INT = struct.Struct("=i")
_UINT = struct.Struct("=I")
_HEADER = struct.Struct("=II")


class Message:
    __slots__ = ("name", "args", "destructor", "new_id_iface", "has_fds")

    def __init__(self, el):
        self.name = el.get("name")
        self.destructor = el.get("type") == "destructor"
        # (type, interface, nullable)
        self.args = [(a.get("type"), a.get("interface"), a.get("allow-null") == "true") for a in el.findall("arg")]
        self.new_id_iface = next((i for t, i, _ in self.args if t == "new_id"), None)
        self.has_fds = any(t == "fd" for t, _, _ in self.args)


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
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM, fileno=int(fd))
            except (ValueError, OSError) as e:
                raise RuntimeError(f"WAYLAND_SOCKET={fd!r} is not a connection to the compositor: {e}") from e
            os.set_inheritable(sock.fileno(), False)
            return sock
        name = os.environ.get("WAYLAND_DISPLAY") or "wayland-0"
        if os.path.isabs(name):
            path = name
        else:
            # Like libwayland: without the user's private runtime directory
            # there is no compositor to find.  (A relative path would mean
            # whatever socket lies in the directory we were started in.)
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
            if not os.path.isabs(runtime_dir):
                raise RuntimeError("cannot connect to the Wayland compositor: XDG_RUNTIME_DIR is not set")
            path = os.path.join(runtime_dir, name)
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
        body = _UINT.pack(name) + _string(iface_name) + _HEADER.pack(version, obj.id)
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
                    body += _UINT.pack(created.id)
                    continue
                v = next(it)
                if typ == "int":
                    body += _INT.pack(int(v))
                elif typ == "uint":
                    body += _UINT.pack(int(v) & 0xFFFFFFFF)
                elif typ == "fixed":
                    body += _INT.pack(int(round(v * 256.0)))
                elif typ == "object":
                    body += _UINT.pack(v.id if v is not None else 0)
                elif typ == "string":
                    body += _string(v)
                elif typ == "array":
                    body += _UINT.pack(len(v)) + bytes(v) + b"\0" * (-len(v) % 4)
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
        size = HEADER_SIZE + len(body)
        if size > 0xFFFF:
            # the size field has 16 bits; anything longer would garble the stream
            raise ValueError(f"request of {size} bytes does not fit into a Wayland message")
        data = _HEADER.pack(oid, (size << 16) | opcode) + body
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
            fd_space = socket.CMSG_SPACE(MAX_FDS_PER_READ * array.array("i").itemsize)
            # file descriptors that arrive are not passed on to programs we start
            data, anc, _flags, _addr = self.sock.recvmsg(65536, fd_space, socket.MSG_CMSG_CLOEXEC)
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
            self._close_fds(self.in_fds)
            return False
        self.inbuf += data
        return True

    @staticmethod
    def _close_fds(fds: list):
        """Closes file descriptors nobody is going to use and empties the list."""
        for fd in fds:
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        fds.clear()

    def dispatch(self):
        """Delivers all complete buffered events to their handlers."""
        buf = self.inbuf
        while len(buf) >= HEADER_SIZE:
            oid, word = _HEADER.unpack_from(buf, 0)
            size, opcode = word >> 16, word & 0xFFFF
            if size < HEADER_SIZE:
                raise ProtocolError("malformed message from the compositor")
            if len(buf) < size:
                break
            body = bytes(buf[HEADER_SIZE:size])
            # taken out of the buffer first: a handler may call roundtrip(),
            # which dispatches again
            del buf[:size]
            self._deliver(oid, opcode, body)

    def _deliver(self, oid: int, opcode: int, body: bytes):
        """Hands one event to its handler."""
        obj = self.objects.get(oid)
        if obj is None:
            if oid not in self.zombies:
                log.debug("event for unknown object %d dropped", oid)
            return
        if opcode >= len(obj.iface.events):
            log.debug("unknown event %d for %r", opcode, obj)
            return
        msg = obj.iface.events[opcode]
        try:
            args = self._decode(obj, msg, body)
        except (struct.error, UnicodeDecodeError, KeyError, ValueError):
            log.exception("could not decode %s.%s", obj.iface.name, msg.name)
            return
        handler = obj.dispatcher.get(msg.name)
        if handler is not None:
            try:
                handler(obj, *args)
            except ProtocolError:
                raise
            except Exception:
                log.exception("handler for %s.%s failed", obj.iface.name, msg.name)
        elif msg.has_fds:
            # a file descriptor nobody asked for (a keymap, say) must not pile up
            self._close_fds([arg for (typ, _, _), arg in zip(msg.args, args) if typ == "fd"])
        if msg.destructor:
            obj.alive = False
            if self.objects.get(oid) is obj and oid >= SERVER_ID_BASE:
                del self.objects[oid]

    def _decode(self, obj: WlObject, msg: Message, body: bytes):
        """The arguments of an event.  Raises struct.error or ValueError if
        the body is shorter than its own length fields claim."""
        args = []
        off = 0
        for typ, iface, _nullable in msg.args:
            if typ == "fd":             # travels beside the message, not in it
                args.append(self.in_fds.pop(0) if self.in_fds else -1)
                continue
            if typ == "int":
                args.append(_INT.unpack_from(body, off)[0])
            elif typ == "fixed":
                args.append(_INT.unpack_from(body, off)[0] / 256.0)
            else:
                value = _UINT.unpack_from(body, off)[0]
                if typ == "uint":
                    args.append(value)
                elif typ == "object":
                    args.append(self.objects.get(value))
                elif typ == "new_id":
                    new = WlObject(self, value, self.ifaces[iface], obj.version)
                    self.objects[value] = new
                    args.append(new)
                elif typ in ("string", "array"):
                    start, end = off + 4, off + 4 + value
                    if end > len(body):
                        raise ValueError(f"{typ} of {value} bytes in a message of {len(body)}")
                    if typ == "array":
                        args.append(body[start:end])
                    else:               # the length counts the terminating zero; 0 means "no string"
                        args.append(body[start:end - 1].decode("utf-8", "replace") if value else None)
                    off += value + (-value % 4)
                else:
                    raise ValueError(f"unknown argument type {typ!r}")
            off += 4
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
        return _UINT.pack(0)
    b = s.encode("utf-8") + b"\0"
    return _UINT.pack(len(b)) + b + b"\0" * (-len(b) % 4)
