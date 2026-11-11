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
            ("zwp_tablet_manager_v2", 1), ("zwp_input_method_v1", 1), ("zwp_input_panel_v1", 1), ("zwlr_layer_shell_v1", 4), ("org_kde_kwin_fake_input", 4)]
if NOFRAC: globals_ = [g_ for g_ in globals_ if g_[0] not in ("wp_viewporter", "wp_fractional_scale_manager_v1")]
a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
tmp = tempfile.mkdtemp(prefix="kritzel-pen.")
env = dict(os.environ, WAYLAND_SOCKET=str(b.fileno()), PYTHONPATH=ROOT, HOME=tmp, XDG_RUNTIME_DIR=tmp, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=tmp + "/c", XDG_STATE_HOME=tmp + "/s", XDG_CACHE_HOME=tmp + "/k", LANG="en_US.UTF-8")
env.pop("WAYLAND_DISPLAY", None)
CONF = "[recognition]\ndelay_ms=500\nlanguages=en_US, de_DE\n"
RESTORE = bool(os.environ.get("IT_RESTORE"))        # variant: the panel was left floating and has to come back that way
def write_conf(text): open(tmp + "/c/kritzel/config.ini", "w").write(text)
os.makedirs(tmp + "/c/kritzel"); write_conf(CONF + ("[panel]\nremember_floating=true\n" if RESTORE else ""))
if RESTORE:
    os.makedirs(tmp + "/s/kritzel"); open(tmp + "/s/kritzel/state.json", "w").write('{"floating": true, "float_pos": [40, 50]}')
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
        if old and old != st["cur_buf"] and m.objects.get(old) == "wl_buffer": m.send(old, "release")
    for cb in frames: m.send(cb, "done", 0); m.send(1, "delete_id", cb); m.objects.pop(cb, None)
    frames.clear()
def on_buffer_destroy(o, a_):
    if st.get("cur_buf") == o: del st["cur_buf"]       # its id may be reused by any other object
m.hooks[("wl_buffer", "destroy")] = on_buffer_destroy
m.hooks.update({("bind", "wl_shm"): bind_shm, ("bind", "wl_output"): bind_output, ("bind", "wl_seat"): bind_seat,
    ("zxdg_output_manager_v1", "get_xdg_output"): xdg_out, ("wp_fractional_scale_manager_v1", "get_fractional_scale"): frac,
    ("zwp_tablet_manager_v2", "get_tablet_seat"): tablet_seat, ("wl_surface", "frame"): on_frame, ("wl_surface", "attach"): on_attach, ("wl_surface", "commit"): on_commit})
results = []
def check(name, cond, detail=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name, detail, flush=True)

# where things are on the panel: ask the program's own layout code
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from kritzel import ui
from kritzel.config import Config, Theme
def view(conf=CONF, w=LOG_W, floating=False, page=ui.WRITE, tiles=()):
    open(tmp + "/geometry.ini", "w").write(conf); cfg = Config(tmp + "/geometry.ini")
    v = ui.View(Theme(), cfg); v.floating = floating; v.layout(w, cfg.height); v.show_page(page, tiles); return v
def centre(rect): return rect.center().x(), rect.center().y()
def btn(name, v=None): return centre((v or view()).buttons[name])
def page_key(v, text): return centre(next(k.rect for k in v.page_keys if k.text == text))

m.pump(2.5)
if RESTORE:
    im = m.bound["zwp_input_method_v1"][0]; ctx = m.new_object("zwp_input_method_context_v1")
    m.send(im, "activate", ctx); m.send(ctx, "commit_state", 7); m.pump(1.0)
    ls = m.requests("zwlr_layer_shell_v1", "get_layer_surface")
    mg = [a_ for o, a_ in m.requests("zwlr_layer_surface_v1", "set_margin")]
    check("remembered floating: the panel starts as a floating one, where it was left",
          len(ls) == 1 and not m.requests("zwp_input_panel_v1", "get_input_panel_surface") and mg and (mg[-1]["left"], mg[-1]["top"]) == (40, 50), str(mg[-1:]))
    m.send(ls[0][1]["id"], "configure", 5, 760, 280); m.pump(0.5)
    check("... and is drawn", [1 for o, a_ in m.requests("wl_surface", "attach") if o == ls[0][1]["surface"] and a_["buffer"]])
    alive = proc.poll() is None; proc.terminate(); proc.wait(5); errlog.seek(0); err = errlog.read()
    check("no exceptions in the client", alive and "Traceback" not in err, err[-1500:] if "Traceback" in err else "")
    sys.exit(0 if all(results) else 1)
check("client bound the input method + panel", "zwp_input_method_v1" in m.bound and "zwp_input_panel_v1" in m.bound)
check("client asked for tablet tools", "tablet_seat" in st)
def dock_surface(): return m.requests("zwp_input_panel_v1", "get_input_panel_surface")[-1][1]["surface"]
def docks(): return len(m.requests("zwp_input_panel_v1", "get_input_panel_surface"))    # ids are reused, so count
def is_mapped(s_):
    att_ = [a_ for o, a_ in m.requests("wl_surface", "attach") if o == s_]
    return s_ in m.objects and bool(att_) and att_[-1]["buffer"] != 0
check("no surface before a text field is active", not m.requests("wl_compositor", "create_surface"))
out = m.bound["wl_output"][0]
im = m.bound["zwp_input_method_v1"][0]
ctx = m.new_object("zwp_input_method_context_v1")
m.send(im, "activate", ctx); m.send(ctx, "surrounding_text", "", 0, 0); m.send(ctx, "content_type", 0, 0); m.send(ctx, "commit_state", 7)
m.pump(1.0)
surf = dock_surface(); m.send(surf, "enter", out); m.pump(0.3)
check("panel surface created with its role when the text field got the focus", len(m.requests("zwp_input_panel_surface_v1", "set_toplevel")) == 1)
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
    from PyQt6.QtGui import QImage
    QImage(shot, binfo["width"], binfo["height"], binfo["stride"], QImage.Format.Format_RGB32).save(os.environ["IT_SHOT"])
dark = sum(1 for i in range(0, len(shot), 4 * 37) if shot[i] < 90 and shot[i + 1] < 90 and shot[i + 2] < 90)
check("the buffer on screen contains the ink", dark > 20, f"{dark} dark samples")
check("ink is drawn while writing (frame-paced redraws)", st.get("commits", 0) - c0 >= 5, f"{st.get('commits', 0) - c0} surface commits")
m.pump(1.6)
check("cursive pen strokes recognised and typed with a space after them", commits() == ["hello world "], repr(commits()))
check("commit used the compositor's serial", m.requests("zwp_input_method_context_v1", "commit_string")[0][1]["serial"] == 7)

# palm: finger scribble in the canvas while the pen hovers must not become ink
touch = m.requests("wl_seat", "get_touch")[0][1]["id"]
m.send(touch, "down", m.next_serial(), 10, surf, 0, 300.0, 150.0); m.send(touch, "frame")
for i in range(20): m.send(touch, "motion", 11, 0, 300.0 + 8 * i, 150.0 + (i % 5) * 9); m.send(touch, "frame")
m.send(touch, "up", m.next_serial(), 12, 0); m.send(touch, "frame"); m.pump(1.5)
check("palm touch ignored while pen is near", commits() == ["hello world "], repr(commits()))

# eraser end of the pen removes ink before it is recognised
pen_write("oops", font="futural"); pen_out(st["pen"]); pen_in(st["eraser"])
pen_stroke(st["eraser"], [(40.0 + 6 * i, 44 + 165 - 40 + (i % 9) * 6) for i in range(70)])
pen_out(st["eraser"]); m.pump(1.6)
check("eraser removed the ink", commits() == ["hello world "], repr(commits()))

pen_in(st["pen"]); pen_write("again", font="futural"); m.pump(1.6)
check("the next word follows without a space in front", commits() == ["hello world ", "again "], repr(commits()))

# word key: first the last insertion, then the word before it
pen_tap(*btn("delword"))
check("word key removes the last insertion with its space", keys() == [(0xFF08, 1), (0xFF08, 0)] * len("again "), f"{len(keys())} key events")
pen_tap(*btn("delword"))
check("word key removes the previous word", len(keys()) == 2 * (len("again ") + len("world ")), f"{len(keys())} key events")
check("no raw key codes are sent (KWin ignores them)", not m.requests("zwp_input_method_context_v1", "key"))
pen_tap(*btn("enter"))
check("Enter key via pen", keys()[-2:] == [(0xFF0D, 1), (0xFF0D, 0)], repr(keys()[-2:]))
for name, keysym in (("left", 0xFF51), ("up", 0xFF52), ("right", 0xFF53), ("down", 0xFF54)):
    pen_tap(*btn(name))
    check(f"arrow key {name}", keys()[-2:] == [(keysym, 1), (keysym, 0)], repr(keys()[-2:]))
n0 = len(keys()); x_, y_ = btn("left")
m.send(st["pen"], "motion", x_, y_); m.send(st["pen"], "down", m.next_serial()); m.send(st["pen"], "frame", 6); m.pump(0.9)
m.send(st["pen"], "up"); m.send(st["pen"], "frame", 7); m.pump(0.3)
held = keys()[n0:]
check("an arrow key repeats while held", len(held) >= 8 and set(held) == {(0xFF51, 1), (0xFF51, 0)}, f"{len(held)} key events")

# copy / paste go through the fake-input interface as key combinations
def fake_keys(): return [(a_["button"], a_["state"]) for o, a_ in m.requests("org_kde_kwin_fake_input", "keyboard_key")]
pen_tap(*btn("copy")); pen_tap(*btn("paste"))
check("copy and paste keys press Ctrl+Insert and Shift+Insert",
      fake_keys() == [(29, 1), (110, 1), (110, 0), (29, 0), (42, 1), (110, 1), (110, 0), (42, 0)]
      and len(m.requests("org_kde_kwin_fake_input", "authenticate")) == 1, repr(fake_keys()))

# the symbol keyboard takes the place of the writing area
def ctx_requests(c=None): return [(n, a_) for (i, o, n, a_) in m.log if i == "zwp_input_method_context_v1" and o == (c or ctx) and n in ("keysym", "commit_string", "delete_surrounding_text")]
SYM = view(page=ui.SYMBOLS)
check("symbol keyboard: 44 keys in the place of the writing area, same panel size",
      len(SYM.page_keys) == 44 and all(view().body.contains(k.rect) for k in SYM.page_keys) and (SYM.w, SYM.h) == (view().w, view().h))
pen_write("ok", font="futural"); m.pump(0.1)
base = len(ctx_requests()); n_att = len(m.requests("wl_surface", "attach"))
pen_tap(*btn("symbols")); m.pump(1.2)
check("symbols key first types the ink that was waiting", [a_["text"] for n, a_ in ctx_requests()[base:]] == ["ok "], repr(ctx_requests()[base:]))
base = len(ctx_requests()); pen_tap(*page_key(SYM, ".")); m.pump(0.5)
new = ctx_requests()[base:]
check("a full stop takes the place of the automatic space", [n for n, _ in new] == ["keysym", "keysym", "commit_string"] and new[-1][1]["text"] == ". ", repr(new))
base = len(ctx_requests()); pen_tap(*page_key(SYM, "5")); pen_tap(*page_key(SYM, "€")); pen_tap(*page_key(SYM, "(")); m.pump(0.3)
check("digits and other symbols are typed as they are", [a_.get("text") for n, a_ in ctx_requests()[base:]] == ["5", "€", "("], repr(ctx_requests()[base:]))
n0 = len(commits())
m.send(st["pen"], "motion", 300.0, 150.0); m.send(st["pen"], "down", m.next_serial()); m.send(st["pen"], "frame", 6)
m.send(st["pen"], "motion", 700.0, 200.0); m.send(st["pen"], "frame", 6); m.send(st["pen"], "up"); m.send(st["pen"], "frame", 7); m.pump(1.2)
check("no ink can be drawn while the symbol keyboard is shown", len(commits()) <= n0 + 1 and all(len(t) <= 1 for t in commits()[n0:]), repr(commits()[n0:]))
pen_tap(*btn("symbols")); m.pump(0.2)
n0 = len(commits()); pen_write("hello", font="futural"); m.pump(1.6)
check("back to writing with the same key", commits()[n0:] == ["hello "], repr(commits()[n0:]))

# language key: pick the language from a list
import json
state_file = tmp + "/s/kritzel/state.json"
TILES = [("lang:en_US", "English (US)", True), ("lang:de_DE", "Deutsch (DE)", False), ("lang:+", "More languages…", False)]
LANG = view(page=ui.LANGUAGES, tiles=TILES)
pen_tap(*btn("lang")); m.pump(0.2); pen_tap(*page_key(LANG, "Deutsch (DE)")); m.pump(0.3)
check("language key + tile: German is in use and remembered", os.path.isfile(state_file) and json.load(open(state_file)).get("language") == "de_DE")
n0 = len(commits()); pen_write("morgen", font="futural"); m.pump(1.8)
check("writing works in the chosen language", len(commits()) == n0 + 1 and commits()[-1].lower() == "morgen ", repr(commits()[n0:]))
pen_tap(*btn("lang")); m.pump(0.2); pen_tap(*btn("lang")); m.pump(0.2)
check("a second tap on the language key closes the list", json.load(open(state_file)).get("language") == "de_DE")
pen_tap(*btn("lang")); m.pump(0.2); pen_tap(*page_key(LANG, "English (US)")); m.pump(0.3)
check("... and English again", json.load(open(state_file)).get("language") == "en_US")

# settings: the key starts the settings program; SIGHUP applies a changed file
import signal
pen_tap(*btn("settings")); t_end = time.monotonic() + 8
while time.monotonic() < t_end and not os.path.isfile(tmp + "/kritzel-settings.pid"): m.pump(0.2)
check("settings key opens the settings window", os.path.isfile(tmp + "/kritzel-settings.pid"))
try: os.kill(int(open(tmp + "/kritzel-settings.pid").read()), signal.SIGTERM)
except (OSError, ValueError): pass
CONF2 = "[panel]\nheight=320\n[keys]\nkeys=space hide\nkeys_side=left\nsymbol_row=true\nsymbols=a b\n" + CONF
write_conf(CONF2); open(tmp + "/c/kritzel/words.txt", "w").write("Kritzel\n")
proc.send_signal(signal.SIGHUP); m.pump(0.8)
bufs = {a_["id"]: a_ for o, a_ in m.requests("wl_shm_pool", "create_buffer")}
last_buf = [a_ for o, a_ in m.requests("wl_surface", "attach") if o == surf][-1]["buffer"]
check("reload: new panel height applied", bufs[last_buf]["height"] == round(320 * BS), str(bufs[last_buf]))
V2 = view(CONF2)
check("reload: only the chosen keys remain, on the left", sorted(V2.buttons) == ["backspace", "hide", "settings", "space"] and V2.buttons["space"].right() < V2.canvas.left())
n0 = len(commits()); pen_tap(*page_key(V2, "b")); pen_tap(*btn("space", V2))
check("reload: symbol row and moved keys work where they are now", commits()[n0:] == ["b", " "], repr(commits()[n0:]))
n0 = len(keys()); pen_tap(*btn("enter"))        # where Enter used to be there is writing area now
check("reload: a removed key is gone", len(keys()) == n0)
m.pump(1.0); write_conf(CONF); proc.send_signal(signal.SIGHUP); m.pump(0.8)

# floating mode
FW = 760; fx, fy = (LOG_W - FW) // 2, LOG_H - H - 48
FV = view(w=FW, floating=True)
pen_tap(*btn("float")); m.pump(0.3)
ls = m.requests("zwlr_layer_shell_v1", "get_layer_surface")
check("float key: docked surface removed, layer surface requested",
      len(ls) == 1 and ls[0][1]["layer"] == 3 and surf not in m.objects, str(ls))
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
surf = fsurf
pen_out(st["pen"]); pen_in(st["pen"])
n0 = len(commits()); pen_write("hello", font="futural"); m.pump(1.6)
check("writing on the floating panel", commits()[n0:] == ["hello "], repr(commits()[n0:]))
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
n_docks = docks(); pen_in(st["pen"]); pen_tap(*btn("float", FV)); m.pump(0.4)
surf = dock_surface()
check("dock key: layer surface destroyed, a new docked surface mapped", layer not in m.objects and docks() == n_docks + 1 and is_mapped(surf))
# switching the float key off while floating must not leave the panel stuck in the air
pen_in(st["pen"]); pen_tap(*btn("float")); m.pump(0.3)
ls = m.requests("zwlr_layer_shell_v1", "get_layer_surface"); layer = ls[-1][1]["id"]
m.send(layer, "configure", 79, FW, H); m.pump(0.4)
n_docks = docks(); write_conf("[keys]\nkeys=enter space\n" + CONF); proc.send_signal(signal.SIGHUP); m.pump(0.8)
surf = dock_surface()
check("float key switched off while floating: the panel docks", len(ls) == 3 and layer not in m.objects and docks() == n_docks + 1 and is_mapped(surf))
write_conf(CONF); proc.send_signal(signal.SIGHUP); m.pump(0.8)
pen_in(st["pen"])
n0 = len(commits()); pen_write("again", font="futural"); m.pump(1.6)
check("writing after docking again", commits()[n0:] == ["again "], repr(commits()[n0:]))

# picking another suggestion
base = len(ctx_requests())
for x in range(80, 300, 12):                    # find the second chip
    pen_tap(x, 22)
    if len(ctx_requests()) > base: break
m.pump(0.5)
new = ctx_requests()[base:]
names = [n for n, _ in new]
check("suggestion in an app that reports no text: Backspaces, then the new word",
      names == ["keysym"] * (2 * len("again ")) + ["commit_string"] and new[-1][1]["text"] not in ("", "again ") and new[-1][1]["text"].endswith(" "), str(names[-3:]) + repr(new[-1][1]))
word = new[-1][1]["text"]
m.send(ctx, "surrounding_text", word, len(word.encode()), len(word.encode())); m.send(ctx, "commit_state", 9); m.pump(0.2)
base = len(ctx_requests()); pen_tap(20, 22); m.pump(0.4)
new = ctx_requests()[base:]
nb = len(word.encode())
check("suggestion in an app that reports its text: delete + insert, no keys",
      [n for n, _ in new] == ["delete_surrounding_text", "commit_string"] and (new[0][1]["index"], new[0][1]["length"]) == (-nb, nb)
      and new[1][1]["text"] == "again " and new[1][1]["serial"] == 9, str(new))
m.send(ctx, "surrounding_text", "again ", 6, 6); m.send(ctx, "commit_state", 10); m.pump(0.1)
# a comma written next closes up to the word: with reported text that is one delete + insert
base = len(ctx_requests()); pen_tap(*btn("symbols")); pen_tap(*page_key(SYM, ",")); pen_tap(*btn("symbols")); m.pump(0.3)
new = ctx_requests()[base:]
check("comma after a word, app reports its text: the space is replaced, no keys",
      [n for n, _ in new] == ["delete_surrounding_text", "commit_string"] and (new[0][1]["index"], new[0][1]["length"]) == (-1, 1) and new[1][1]["text"] == ", ", str(new))
m.send(ctx, "surrounding_text", "again, ", 7, 7); m.send(ctx, "commit_state", 11); m.pump(0.1)
n0 = len(commits()); pen_write("hello", font="futural"); m.pump(1.6)
m.send(ctx, "surrounding_text", "again, hello ", 13, 13); m.send(ctx, "commit_state", 12); m.pump(0.1)
m.send(ctx, "surrounding_text", "again, hello ", 2, 2); m.send(ctx, "commit_state", 13); m.pump(0.2)   # the user moved the cursor
base = len(ctx_requests())
for x in range(20, 300, 24): pen_tap(x, 22)
check("suggestions disappear once the user edits the text", commits()[n0:] == ["hello "] and len(ctx_requests()) == base, str(ctx_requests()[base:]))

# fields that hold no prose get no automatic spaces
m.send(im, "deactivate", ctx); m.pump(0.4)
ctx = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx); m.send(ctx, "content_type", 0, 5); m.send(ctx, "commit_state", 3); m.pump(0.6)
surf = dock_surface(); pen_in(st["pen"])
n0 = len(commits()); pen_write("hello", font="futural"); m.pump(1.6)
check("no automatic space in an address field", commits()[n0:] == ["hello"], repr(commits()[n0:]))

n_docks = docks(); m.send(im, "deactivate", ctx); m.pump(0.6)
check("text field loses focus: the surface is destroyed", surf not in m.objects and docks() == n_docks)
ctx2 = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx2); m.send(ctx2, "commit_state", 9); m.pump(0.6)
surf = dock_surface()
check("the next text field gets a new surface", docks() == n_docks + 1 and is_mapped(surf))
m.send(im, "deactivate", ctx2); m.pump(0.3)
ctx3 = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx3); m.pump(0.4)
surf = dock_surface(); n_docks = docks(); pen_in(st["pen"]); pen_tap(*btn("hide")); m.pump(0.4)
check("hide key removes the panel", surf not in m.objects and docks() == n_docks)
proc.send_signal(signal.SIGUSR1); m.pump(0.6)
check("`kritzel show` signal brings it back", docks() == n_docks + 1 and is_mapped(dock_surface()))

# ---- version 1.5: gestures, walking back, number and password fields, learning ----
def new_field(hint=0, purpose=0, serial_=20):
    global ctx, surf
    m.send(im, "deactivate", ctx); m.pump(0.4)
    ctx = m.new_object("zwp_input_method_context_v1"); m.send(im, "activate", ctx)
    m.send(ctx, "content_type", hint, purpose); m.send(ctx, "commit_state", serial_); m.pump(0.6)
    surf = dock_surface(); pen_in(st["pen"])
def sent(base_): return [(n, a_.get("text", a_.get("sym"))) for n, a_ in ctx_requests()[base_:] if n != "keysym" or a_["state"]]
def straight(a_, b_, n=24): return [(a_[0] + (b_[0] - a_[0]) * i / n, a_[1] + (b_[1] - a_[1]) * i / n) for i in range(n + 1)]
ctx = ctx3; new_field()
CV = view().canvas; cy = CV.center().y()

base = len(ctx_requests()); pen_write("hello", font="futural"); m.pump(0.45)
check("the pause is still running: nothing typed yet", sent(base) == [], repr(sent(base)))
m.pump(1.2); pen_write("again", font="futural"); m.pump(1.6)
check("two words typed", sent(base) == [("commit_string", "hello "), ("commit_string", "again ")], repr(sent(base)))
base = len(ctx_requests()); pen_stroke(st["pen"], straight((560.0, cy), (260.0, cy + 6))); m.pump(0.3)
check("gesture: a long line to the left deletes the last word", sent(base) == [("keysym", 0xFF08)] * len("again "), repr(sent(base)))
base = len(ctx_requests()); pen_tap(20, 22); m.pump(0.4)
check("... and the suggestions of the word before it are back: tapping the chosen one changes nothing", sent(base) == [], repr(sent(base)))
for x in range(80, 300, 12):                    # another reading of "hello"
    pen_tap(x, 22)
    if len(ctx_requests()) > base: break
m.pump(0.5); swapped = sent(base)
check("... and another one replaces that word", swapped[:-1] == [("keysym", 0xFF08)] * len("hello ") and swapped[-1][0] == "commit_string"
      and swapped[-1][1] not in ("", "hello "), repr(swapped))
base = len(ctx_requests()); pen_stroke(st["pen"], straight((560.0, cy), (260.0, cy - 5))); m.pump(0.3)
check("walking back further deletes that word as it is now", sent(base) == [("keysym", 0xFF08)] * len(swapped[-1][1]), repr(sent(base)))
base = len(ctx_requests()); pen_stroke(st["pen"], straight((260.0, cy), (560.0, cy + 4))); m.pump(0.3)
check("gesture: a long line to the right is a space", sent(base) == [("commit_string", " ")], repr(sent(base)))
base = len(ctx_requests()); pen_stroke(st["pen"], straight((600.0, cy - 40), (603.0, cy + 10)) + straight((603.0, cy + 10), (420.0, cy + 14))[1:]); m.pump(0.3)
check("gesture: down and left is Enter", sent(base) == [("keysym", 0xFF0D)], repr(sent(base)))
base = len(ctx_requests()); pen_stroke(st["pen"], straight((300.0, cy), (360.0, cy))); m.pump(1.6)
check("a short line is writing, not a gesture", [n for n, _ in sent(base)] == ["commit_string"], repr(sent(base)))

base = len(ctx_requests()); pen_write("oops", font="futural")
zig = []
for k in range(5): zig += straight((40.0, cy + 4 * k), (330.0, cy + 4 * k + 2), 12)[:: (1 if k % 2 == 0 else -1)]
pen_stroke(st["pen"], zig); m.pump(1.6)
check("gesture: a zigzag over the waiting ink rubs it out", sent(base) == [], repr(sent(base)))
pen_write("ok", font="futural"); m.pump(1.6)
check("... and writing goes on", sent(base) == [("commit_string", "ok ")], repr(sent(base)))

# the same strokes with gestures switched off are ink
write_conf(CONF + "[input]\ngestures=false\n"); proc.send_signal(signal.SIGHUP); m.pump(0.8)
base = len(ctx_requests()); pen_stroke(st["pen"], straight((560.0, cy), (260.0, cy + 6))); m.pump(1.6)
check("gestures switched off: the long line is read as writing", all(n == "commit_string" for n, _ in sent(base)), repr(sent(base)))
write_conf(CONF); proc.send_signal(signal.SIGHUP); m.pump(0.8)

# a field for digits
new_field(purpose=2, serial_=21)
base = len(ctx_requests()); pen_write("hello", font="futural"); m.pump(1.6)
got = sent(base)
check("number field: whatever is written, nothing but digits is typed",
      all(n == "commit_string" and text.isdigit() for n, text in got), repr(got))
base = len(ctx_requests()); pen_write("2017", font="futural"); m.pump(1.6)
check("number field: digits are read as digits", sent(base) == [("commit_string", "2017")], repr(sent(base)))

# learning: a reading picked twice in place of what was typed is kept - but not from a password field
learned_file = tmp + "/s/kritzel/learned.json"
def learned_counts(): return json.load(open(learned_file))["words"] if os.path.isfile(learned_file) else {}
def write_and_pick_another(word):
    base_ = len(ctx_requests()); pen_write(word, font="futural"); m.pump(1.6)
    for x in range(80, 300, 12):
        pen_tap(x, 22)
        if len(ctx_requests()) > base_ + 1: break
    m.pump(0.5)
    return [a_["text"] for n, a_ in ctx_requests()[base_:] if n == "commit_string"]
before = learned_counts()
check("a pick the user went back on has taught nothing; the one that stayed is noted once",
      len(before) == 1 and set(before.values()) == {1} and set(before) <= set(swapped[-1][1].split()), repr(before))
check("the file with the learnt words is private", os.stat(learned_file).st_mode & 0o777 == 0o600, oct(os.stat(learned_file).st_mode))
new_field(hint=0x40, purpose=8, serial_=22)
picked = write_and_pick_another("hello")
check("password field: another reading can be picked ...", len(picked) == 2 and picked[0] != picked[1], repr(picked))
check("... but nothing is learnt from it", learned_counts() == before, repr(learned_counts()))
new_field(serial_=23)
picked = write_and_pick_another("hello")
check("the same pick a second time: the word counts from now on",
      len(picked) == 2 and picked[1] == swapped[-1][1] and set(learned_counts().values()) == {2}, repr((picked, learned_counts())))
alive = proc.poll() is None
proc.terminate(); proc.wait(5)
errlog.seek(0); err = errlog.read()
check("client still running at the end", alive)
check("no exceptions in the client", "Traceback" not in err and "Error" not in err, err[-1500:] if "Traceback" in err or "Error" in err else "")
sys.exit(0 if all(results) else 1)
