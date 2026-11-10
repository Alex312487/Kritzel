"""Stylus end-to-end test against the mock compositor (see mockcomp.py)."""
import os, socket, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
from mockcomp import Mock, load_protocols
from synth import strokes_for
P = os.path.join(ROOT, "protocols")
protos = load_protocols([os.path.join(P, f) for f in os.listdir(P) if f.endswith(".xml")])
SCALE = float(os.environ.get("IT_SCALE", "1.5")); LOG_W, LOG_H = 1504, 1003
NOFRAC = bool(os.environ.get("IT_NOFRAC"))
globals_ = [("wl_compositor", 6), ("wl_shm", 1), ("wl_seat", 8), ("wl_output", 4), ("zxdg_output_manager_v1", 3),
            ("wp_viewporter", 1), ("wp_fractional_scale_manager_v1", 1), ("wp_cursor_shape_manager_v1", 1),
            ("zwp_tablet_manager_v2", 1), ("zwp_input_method_v1", 1), ("zwp_input_panel_v1", 1), ("zwlr_layer_shell_v1", 4)]
if NOFRAC: globals_ = [g_ for g_ in globals_ if g_[0] not in ("wp_viewporter", "wp_fractional_scale_manager_v1")]
a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
tmp = tempfile.mkdtemp(prefix="kritzel-pen.")
env = dict(os.environ, WAYLAND_SOCKET=str(b.fileno()), PYTHONPATH=ROOT, HOME=tmp, XDG_CONFIG_HOME=tmp + "/c", XDG_STATE_HOME=tmp + "/s", XDG_CACHE_HOME=tmp + "/k", LANG="en_US.UTF-8")
env.pop("WAYLAND_DISPLAY", None)
os.makedirs(tmp + "/c/kritzel"); open(tmp + "/c/kritzel/config.ini", "w").write("[recognition]\ndelay_ms=500\n")
errlog = open(tmp + "/stderr.log", "w+")
proc = subprocess.Popen([sys.executable, "-m", "kritzel", "-v", "run"], env=env, pass_fds=[b.fileno()], stderr=errlog)
b.close()
m = Mock(a, protos, globals_)
st = {}
def bind_shm(i, a_): m.send(i, "format", 0); m.send(i, "format", 1)
def bind_output(i, a_):
    m.send(i, "geometry", 0, 0, 300, 200, 0, "mock", "mock", 0); m.send(i, "mode", 3, int(LOG_W * SCALE), int(LOG_H * SCALE), 60000); m.send(i, "scale", 2); m.send(i, "done")
def bind_seat(i, a_): m.send(i, "capabilities", 1 | 4); st["seat"] = i
def xdg_out(o, a_): m.send(a_["id"], "logical_position", 0, 0); m.send(a_["id"], "logical_size", LOG_W, LOG_H); m.send(a_["id"], "done")
def frac(o, a_): m.send(a_["id"], "preferred_scale", int(SCALE * 120))
def tablet_seat(o, a_):
    ts = a_["tablet_seat"]; st["tablet_seat"] = ts
    t = m.new_object("zwp_tablet_v2"); m.send(ts, "tablet_added", t); m.send(t, "name", "Mock Pen"); m.send(t, "id", 0x56A, 1); m.send(t, "done"); st["tablet"] = t
    for key, typ in (("pen", 0x140), ("eraser", 0x141)):
        tool = m.new_object("zwp_tablet_tool_v2"); m.send(ts, "tool_added", tool); m.send(tool, "type", typ); m.send(tool, "hardware_serial", 0, 42); m.send(tool, "capability", 2); m.send(tool, "done"); st[key] = tool
frames = []
def on_frame(o, a_): frames.append(a_["callback"])
def on_attach(o, a_):
    st["pending_buf"] = a_["buffer"]
def on_commit(o, a_):
    st["commits"] = st.get("commits", 0) + 1
    if "pending_buf" in st:
        old = st.get("cur_buf"); st["cur_buf"] = st.pop("pending_buf")
        if old and old != st["cur_buf"] and old in m.objects: m.send(old, "release")
    for cb in frames: m.send(cb, "done", 0); m.send(1, "delete_id", cb); m.objects.pop(cb, None)
    frames.clear()
m.hooks.update({("bind", "wl_shm"): bind_shm, ("bind", "wl_output"): bind_output, ("bind", "wl_seat"): bind_seat,
    ("zxdg_output_manager_v1", "get_xdg_output"): xdg_out, ("wp_fractional_scale_manager_v1", "get_fractional_scale"): frac,
    ("zwp_tablet_manager_v2", "get_tablet_seat"): tablet_seat, ("wl_surface", "frame"): on_frame, ("wl_surface", "attach"): on_attach, ("wl_surface", "commit"): on_commit})
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name, detail, flush=True)

m.pump(2.5)
check("client bound the input method + panel", "zwp_input_method_v1" in m.bound and "zwp_input_panel_v1" in m.bound)
check("client asked for tablet tools", "tablet_seat" in st)
surf = m.requests("wl_compositor", "create_surface")[0][1]["id"]
check("panel surface role set", len(m.requests("zwp_input_panel_surface_v1", "set_toplevel")) == 1)
check("nothing mapped before a text field is active", not m.requests("wl_surface", "attach"))
out = m.bound["wl_output"][0]; m.send(surf, "enter", out)
im = m.bound["zwp_input_method_v1"][0]
ctx = m.new_object("zwp_input_method_context_v1")
m.send(im, "activate", ctx); m.send(ctx, "surrounding_text", "", 0, 0); m.send(ctx, "content_type", 0, 0); m.send(ctx, "commit_state", 7)
m.pump(1.0)
att = m.requests("wl_surface", "attach")
bufs = {a_["id"]: a_ for o, a_ in m.requests("wl_shm_pool", "create_buffer")}
W = LOG_W; H = 280                 # the docked panel spans the whole screen width
BS = 2 if NOFRAC else SCALE      # without fractional-scale the integer wl_output scale applies
ok = att and att[-1][1]["buffer"] in bufs and (bufs[att[-1][1]["buffer"]]["width"], bufs[att[-1][1]["buffer"]]["height"]) == (round(W * BS), round(H * BS))
check("panel mapped at device-pixel size", ok, str([(v["width"], v["height"]) for v in bufs.values()]))
if NOFRAC:
    bs = m.requests("wl_surface", "set_buffer_scale")
    check("integer buffer scale set", bs and bs[-1][1]["scale"] == 2, str(bs[-1:]))
else:
    dest = m.requests("wp_viewport", "set_destination")
    check("viewport = logical size", dest and (dest[-1][1]["width"], dest[-1][1]["height"]) == (W, H), str(dest[-1][1] if dest else None))

def pen_in(tool):
    s = m.next_serial(); m.send(tool, "proximity_in", s, st["tablet"], surf); m.send(tool, "motion", 100.0, 150.0); m.send(tool, "frame", 1); return s
def pen_out(tool): m.send(tool, "proximity_out"); m.send(tool, "frame", 2)
def pen_stroke(tool, pts):
    m.send(tool, "motion", *pts[0]); m.send(tool, "down", m.next_serial()); m.send(tool, "pressure", 30000); m.send(tool, "frame", 3)
    for n, p in enumerate(pts[1:]):
        m.send(tool, "motion", *p); m.send(tool, "pressure", 30000); m.send(tool, "frame", 4)
        if n % 4 == 0: m.pump(0.004)
    m.send(tool, "up"); m.send(tool, "frame", 5); m.pump(0.02)
def pen_write(word, tool=None, font="cursive"):
    for s_ in strokes_for(word, font=font, height=80, x0=60.0, baseline=44 + 165, jitter=0.4, seed=5): pen_stroke(tool or st["pen"], s_.points)
def commits(): return [a_["text"] for o, a_ in m.requests("zwp_input_method_context_v1", "commit_string")]
def keys(): return [(a_["sym"], a_["state"]) for o, a_ in m.requests("zwp_input_method_context_v1", "keysym")]
def btn(name, w=None):
    r, c = {"backspace": (0, 0), "enter": (0, 1), "delword": (2, 0), "keyboard": (2, 1), "float": (3, 0), "hide": (3, 1)}[name]
    return float((w or W) - 142 + 6 + 31 + 68 * c), 6 + r * 68.5 + 31
def pen_tap(x, y):
    m.send(st["pen"], "motion", float(x), float(y)); m.send(st["pen"], "down", m.next_serial()); m.send(st["pen"], "frame", 6); m.pump(0.05)
    m.send(st["pen"], "up"); m.send(st["pen"], "frame", 7); m.pump(0.4)

serial = pen_in(st["pen"]); m.pump(0.3)
shapes = m.requests("wp_cursor_shape_device_v1", "set_shape")
check("pen cursor shape requested with the proximity serial", shapes and shapes[-1][1]["serial"] == serial and shapes[-1][1]["shape"] == 8, str(shapes[-1:] ))
c0 = st.get("commits", 0)
pen_write("hello world"); m.pump(0.25)
# what the compositor would put on screen: read the attached shm buffer
import mmap as _mmap
pools = {a_["id"]: a_ for o, a_ in m.requests("wl_shm", "create_pool")}
cur = {a_["id"]: (o, a_) for o, a_ in m.requests("wl_shm_pool", "create_buffer")}
pool_id, binfo = cur[st["cur_buf"]]
mm = _mmap.mmap(pools[pool_id]["fd"], pools[pool_id]["size"], prot=_mmap.PROT_READ)
shot = bytes(mm[binfo["offset"]: binfo["offset"] + binfo["stride"] * binfo["height"]])
if os.environ.get("IT_SHOT"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtGui import QImage
    QImage(shot, binfo["width"], binfo["height"], binfo["stride"], QImage.Format.Format_RGB32).save(os.environ["IT_SHOT"])
dark = sum(1 for i in range(0, len(shot), 4 * 37) if shot[i] < 90 and shot[i + 1] < 90 and shot[i + 2] < 90)
check("the buffer on screen contains the ink", dark > 20, f"{dark} dark samples")
check("ink is drawn while writing (frame-paced redraws)", st.get("commits", 0) - c0 >= 5, f"{st.get('commits', 0) - c0} surface commits")
m.pump(1.6)
check("cursive pen strokes recognised and committed", commits() == ["hello world"], repr(commits()))
check("commit used the compositor's serial", m.requests("zwp_input_method_context_v1", "commit_string")[0][1]["serial"] == 7)

# palm: finger scribble in the canvas while the pen hovers must not become ink
touch = m.requests("wl_seat", "get_touch")[0][1]["id"]
m.send(touch, "down", m.next_serial(), 10, surf, 0, 300.0, 150.0); m.send(touch, "frame")
for i in range(20): m.send(touch, "motion", 11, 0, 300.0 + 8 * i, 150.0 + (i % 5) * 9); m.send(touch, "frame")
m.send(touch, "up", m.next_serial(), 12, 0); m.send(touch, "frame"); m.pump(1.5)
check("palm touch ignored while pen is near", commits() == ["hello world"], repr(commits()))

# eraser end of the pen removes ink before it is recognised
pen_write("oops", font="futural"); pen_out(st["pen"]); pen_in(st["eraser"])
pen_stroke(st["eraser"], [(40.0 + 6 * i, 44 + 165 - 40 + (i % 9) * 6) for i in range(70)])
pen_out(st["eraser"]); m.pump(1.6)
check("eraser removed the ink", commits() == ["hello world"], repr(commits()))

pen_in(st["pen"]); pen_write("again", font="futural"); m.pump(1.6)
check("second chunk auto-spaced without app feedback", commits() == ["hello world", " again"], repr(commits()))

# word button: first the last insertion, then the word before it
pen_tap(*btn("delword"))
check("word button removes the last insertion", keys() == [(0xFF08, 1), (0xFF08, 0)] * len(" again"), f"{len(keys())} key events")
pen_tap(*btn("delword"))
check("word button removes the previous word", len(keys()) == 2 * (len(" again") + len("world")), f"{len(keys())} key events")
check("no raw key codes are sent (KWin 6.8 ignores them)", not m.requests("zwp_input_method_context_v1", "key"))
pen_tap(*btn("enter"))
check("Enter button via pen", keys()[-2:] == [(0xFF0D, 1), (0xFF0D, 0)], repr(keys()[-2:]))

# floating mode
FW = 760; fx, fy = (LOG_W - FW) // 2, LOG_H - H - 48
pen_tap(*btn("float")); m.pump(0.3)
ls = m.requests("zwlr_layer_shell_v1", "get_layer_surface")
check("float button: docked surface unmapped, layer surface requested",
      len(ls) == 1 and ls[0][1]["layer"] == 3 and [a_ for o, a_ in m.requests("wl_surface", "attach") if o == surf][-1]["buffer"] == 0, str(ls))
layer, fsurf = ls[0][1]["id"], ls[0][1]["surface"]
def last(req, obj): return [a_ for o, a_ in m.requests("zwlr_layer_surface_v1", req) if o == obj][-1]
mg = last("set_margin", layer)
check("floating panel: size, position, never takes keyboard focus",
      (last("set_size", layer)["width"], last("set_size", layer)["height"]) == (FW, H) and (mg["left"], mg["top"]) == (fx, fy)
      and last("set_keyboard_interactivity", layer)["keyboard_interactivity"] == 0 and last("set_anchor", layer)["anchor"] == 5, str(mg))
check("nothing drawn before the compositor configures it", not [1 for o, a_ in m.requests("wl_surface", "attach") if o == fsurf])
m.send(layer, "configure", 77, FW, H); m.pump(0.5)
fatt = [a_ for o, a_ in m.requests("wl_surface", "attach") if o == fsurf]
bufs = {a_["id"]: a_ for o, a_ in m.requests("wl_shm_pool", "create_buffer")}
check("floating panel drawn after configure", last("ack_configure", layer)["serial"] == 77 and fatt and (bufs[fatt[-1]["buffer"]]["width"], bufs[fatt[-1]["buffer"]]["height"]) == (round(FW * BS), round(H * BS)), str(fatt[-1:]))
surf_dock, surf = surf, fsurf
pen_out(st["pen"]); pen_in(st["pen"])
n0 = len(commits()); pen_write("hello", font="futural"); m.pump(1.6)
check("writing on the floating panel", commits()[n0:] == ["hello"], repr(commits()[n0:]))
# pen drag by the grip: coordinates are relative to the moving panel
m.send(st["pen"], "motion", 16.0, 22.0); m.send(st["pen"], "down", m.next_serial()); m.send(st["pen"], "frame", 6); m.pump(0.05)
m.send(st["pen"], "motion", 56.0, 52.0); m.send(st["pen"], "frame", 6); m.pump(0.1)        # +40, +30
m.send(st["pen"], "motion", 16.0, 22.0); m.send(st["pen"], "frame", 6); m.pump(0.1)        # panel caught up
m.send(st["pen"], "motion", 6.0, 22.0); m.send(st["pen"], "frame", 6); m.pump(0.1)         # -10
m.send(st["pen"], "up"); m.send(st["pen"], "frame", 7); m.pump(0.2)
mg = last("set_margin", layer)
check("pen drag moves the panel", (mg["left"], mg["top"]) == (fx + 30, fy + 30), str(mg))
# finger drag: KWin 6 keeps touch coordinates relative to where the panel was at touch-down
pen_out(st["pen"]); m.pump(0.7)
m.send(touch, "down", m.next_serial(), 20, surf, 1, 16.0, 22.0); m.send(touch, "frame"); m.pump(0.05)
for i in range(1, 11):
    m.send(touch, "motion", 21, 1, 16.0 - 20 * i, 22.0 - 10 * i); m.send(touch, "frame"); m.pump(0.02)
m.send(touch, "up", m.next_serial(), 22, 1); m.send(touch, "frame"); m.pump(0.2)
mg = last("set_margin", layer)
check("finger drag moves the panel", (mg["left"], mg["top"]) == (fx + 30 - 200, fy + 30 - 100), str(mg))
m.send(touch, "down", m.next_serial(), 23, surf, 1, 16.0, 22.0); m.send(touch, "frame"); m.pump(0.05)
m.send(touch, "motion", 24, 1, -3000.0, -3000.0); m.send(touch, "frame"); m.pump(0.05)
m.send(touch, "up", m.next_serial(), 25, 1); m.send(touch, "frame"); m.pump(0.2)
mg = last("set_margin", layer)
check("the panel cannot be dragged off the screen", (mg["left"], mg["top"]) == (0, 0), str(mg))
# a new text field: floating panel goes away and comes back at the same place
m.send(im, "deactivate", ctx); m.pump(0.4)
check("floating panel removed when the text field loses focus", layer not in m.objects)
ctx = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx); m.send(ctx, "commit_state", 8); m.pump(0.8)
ls = m.requests("zwlr_layer_shell_v1", "get_layer_surface")
check("floating panel back on the next text field", len(ls) == 2 and (last("set_margin", ls[1][1]["id"])["left"], last("set_margin", ls[1][1]["id"])["top"]) == (0, 0))
layer, surf = ls[1][1]["id"], ls[1][1]["surface"]
m.send(layer, "configure", 78, FW, H); m.pump(0.5)
pen_in(st["pen"]); pen_tap(*btn("float", FW)); m.pump(0.4)
datt = [a_ for o, a_ in m.requests("wl_surface", "attach") if o == surf_dock]
check("dock button: layer surface destroyed, docked panel mapped again", layer not in m.objects and datt[-1]["buffer"] != 0)
surf = surf_dock
n0 = len(commits()); pen_write("again", font="futural"); m.pump(1.6)
check("writing after docking again", commits()[n0:] == ["again"], repr(commits()[n0:]))

# picking another suggestion
def ctx_requests(): return [(n, a_) for (i, o, n, a_) in m.log if i == "zwp_input_method_context_v1" and o == ctx and n in ("keysym", "commit_string", "delete_surrounding_text")]
base = len(ctx_requests())
for x in range(80, 300, 12):                    # find the second chip
    pen_tap(x, 22)
    if len(ctx_requests()) > base: break
m.pump(0.5)
new = ctx_requests()[base:]
names = [n for n, _ in new]
check("suggestion in an app that reports no text: Backspaces, then the new word",
      names == ["keysym"] * (2 * len("again")) + ["commit_string"] and new[-1][1]["text"] not in ("", "again"), str(names[-3:]) + repr(new[-1][1]))
word = new[-1][1]["text"]
m.send(ctx, "surrounding_text", word, len(word.encode()), len(word.encode())); m.send(ctx, "commit_state", 9); m.pump(0.2)
base = len(ctx_requests()); pen_tap(20, 22); m.pump(0.4)
new = ctx_requests()[base:]
nb = len(word.encode())
check("suggestion in an app that reports its text: delete + insert, no keys",
      [n for n, _ in new] == ["delete_surrounding_text", "commit_string"] and (new[0][1]["index"], new[0][1]["length"]) == (-nb, nb)
      and new[1][1]["text"] == "again" and new[1][1]["serial"] == 9, str(new))
m.send(ctx, "surrounding_text", "again", 5, 5); m.send(ctx, "commit_state", 10); m.pump(0.1)
m.send(ctx, "surrounding_text", "again", 2, 2); m.send(ctx, "commit_state", 11); m.pump(0.2)   # the user moved the cursor
base = len(ctx_requests())
for x in range(20, 300, 24): pen_tap(x, 22)
check("suggestions disappear once the user edits the text", len(ctx_requests()) == base, str(ctx_requests()[base:]))

m.send(im, "deactivate", ctx); m.pump(0.6)
last_att = m.requests("wl_surface", "attach")[-1][1]
check("panel unmapped when the text field loses focus", last_att["buffer"] == 0, str(last_att))
ctx2 = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx2); m.send(ctx2, "commit_state", 9); m.pump(0.6)
check("mapped again on the next text field", m.requests("wl_surface", "attach")[-1][1]["buffer"] != 0)
import signal
m.send(im, "deactivate", ctx2); m.pump(0.3)
ctx3 = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx3); m.pump(0.4)
pen_in(st["pen"]); pen_tap(*btn("hide")); m.pump(0.4)
check("hide button unmaps the panel", m.requests("wl_surface", "attach")[-1][1]["buffer"] == 0)
proc.send_signal(signal.SIGUSR1); m.pump(0.6)
check("`kritzel show` signal brings it back", m.requests("wl_surface", "attach")[-1][1]["buffer"] != 0)
alive = proc.poll() is None
proc.terminate(); proc.wait(5)
errlog.seek(0); err = errlog.read()
check("client still running at the end", alive)
check("no exceptions in the client", "Traceback" not in err and "Error" not in err, err[-1500:] if "Traceback" in err or "Error" in err else "")
sys.exit(0 if all(results) else 1)
