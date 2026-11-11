"""Integration test: real KWin (headless), Kritzel started by KWin as the
virtual keyboard, a text-input-v3 client, and fake touch input drawing
handwriting on the panel.  Run via tests/run_it.sh."""
import json, mmap, os, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE)); sys.path.insert(0, HERE)
# The generated protocol modules are imported below, so they must not lie in a
# place where another user of the computer could have put them first (a fixed
# name in /tmp): run_it.sh hands us its private directory, else we make one.
GEN = os.path.join(os.environ.get("T") or tempfile.mkdtemp(prefix="kritzel-itgen."), "itgen")
if not os.path.isdir(GEN + "/it_wl"):
    P = "/usr/share/wayland-protocols"
    subprocess.run([sys.executable, "-m", "pywayland.scanner", "-o", GEN + "/it_wl", "-i", "/usr/share/wayland/wayland.xml",
        P + "/stable/xdg-shell/xdg-shell.xml", P + "/unstable/text-input/text-input-unstable-v3.xml",
        "/usr/share/plasma-wayland-protocols/fake-input.xml"], check=True, capture_output=True)
sys.path.insert(0, GEN)
from pywayland.client import Display
from it_wl.wayland import WlCompositor, WlShm, WlSeat
from it_wl.xdg_shell import XdgWmBase
from it_wl.text_input_unstable_v3 import ZwpTextInputManagerV3
from it_wl.fake_input import OrgKdeKwinFakeInput
from synth import strokes_for

W, H = 1280, 800
d = Display(os.environ["WAYLAND_DISPLAY"]); d.connect()
g = {}
def on_global(reg, name, iface, ver):
    t = {"wl_compositor": (WlCompositor, 4), "wl_shm": (WlShm, 1), "wl_seat": (WlSeat, 5), "xdg_wm_base": (XdgWmBase, 1),
         "zwp_text_input_manager_v3": (ZwpTextInputManagerV3, 1), "org_kde_kwin_fake_input": (OrgKdeKwinFakeInput, 4)}
    if iface in t: g[iface] = reg.bind(name, t[iface][0], min(ver, t[iface][1]))
reg = d.get_registry(); reg.dispatcher["global"] = on_global; d.roundtrip()
missing = [k for k in ("wl_compositor", "wl_seat", "xdg_wm_base", "zwp_text_input_manager_v3", "org_kde_kwin_fake_input") if k not in g]
assert not missing, missing

log = []          # everything the "application" receives
text = ""         # the application's text buffer
pending = {"commit": None, "delete": 0}
state = {"configured": False, "ti_enter": False, "kbd": None, "touch": None}

def pump(seconds=0.0):
    end = time.monotonic() + seconds
    while True:
        d.roundtrip(); apply_keys()
        if time.monotonic() >= end: break
        time.sleep(0.01)

seat = g["wl_seat"]
keyq = []
def on_key(_k, serial, t, key, st):
    # Like Qt: key events are queued and handled after the current batch of
    # Wayland events, while input-method events (commit_string) apply at once.
    keyq.append((key, st))
def apply_keys():
    while keyq: handle_key(*keyq.pop(0))
def handle_key(key, st):
    global text
    log.append(("key", key, st))
    if st == 1 and key == 14: text = text[:-1]; sync_ti()
    if st == 1 and key == 28: text += "\n"; sync_ti()
def on_caps(s, caps):
    if caps & 2 and state["kbd"] is None:
        k = s.get_keyboard(); state["kbd"] = k
        k.dispatcher["keymap"] = lambda _k, fmt, fd, size: os.close(fd)
        k.dispatcher["key"] = on_key
    if caps & 4 and state["touch"] is None:
        t = s.get_touch(); state["touch"] = t
        def tdown(*a): log.append(("app-touch",)); sync_ti(True)
        t.dispatcher["down"] = tdown

seat.dispatcher["capabilities"] = on_caps
fake = g["org_kde_kwin_fake_input"]; fake.authenticate("kritzel-test", "integration test"); pump(0.3)
ti = g["zwp_text_input_manager_v3"].get_text_input(seat)
def sync_ti(enable=False):
    # like Qt/GTK: "enable" only on focus-in and when the field is tapped
    if enable: ti.enable()
    if state.get("mute"):       # an application that does not report its text (a terminal, say)
        ti.set_content_type(0, 0); ti.set_cursor_rectangle(100, 100, 2, 20); ti.commit(); return
    ti.set_surrounding_text(text[-2000:], len(text[-2000:].encode()), len(text[-2000:].encode())); ti.set_content_type(0, 0); ti.set_cursor_rectangle(100, 100, 2, 20); ti.commit()
def ti_enter(_t, surf): state["ti_enter"] = True; log.append(("ti-enter",)); sync_ti(True)
ti.dispatcher["leave"] = lambda _t, surf: log.append(("ti-leave",)) and None
def ti_commit(_t, s): pending["commit"] = s
def ti_delete(_t, before, after): pending["delete"] = before
def ti_done(_t, serial):
    global text
    if pending["delete"]:
        text = text.encode()[: len(text.encode()) - pending["delete"]].decode(); log.append(("delete", pending["delete"]))
    if pending["commit"] is not None:
        text += pending["commit"]; log.append(("commit", pending["commit"]))
    changed = pending["commit"] is not None or pending["delete"]
    pending["commit"] = None; pending["delete"] = 0
    if changed: sync_ti()
ti.dispatcher["enter"] = ti_enter; ti.dispatcher["commit_string"] = ti_commit
ti.dispatcher["delete_surrounding_text"] = ti_delete; ti.dispatcher["done"] = ti_done

surf = g["wl_compositor"].create_surface()
xs = g["xdg_wm_base"].get_xdg_surface(surf); g["xdg_wm_base"].dispatcher["ping"] = lambda b, s: b.pong(s)
top = xs.get_toplevel(); top.set_title("it-app"); top.set_maximized()
size = {"w": W, "h": H}
def top_conf(_t, w, h, states):
    if w and h: size["w"], size["h"] = w, h
top.dispatcher["configure"] = top_conf
def xs_conf(x, serial):
    x.ack_configure(serial); state["configured"] = True
xs.dispatcher["configure"] = xs_conf
surf.commit(); pump(0.3)
assert state["configured"]
n = size["w"] * size["h"] * 4
fd = os.memfd_create("it"); os.ftruncate(fd, n); mm = mmap.mmap(fd, n); mm[:] = b"\xf0" * n
pool = g["wl_shm"].create_pool(fd, n); buf = pool.create_buffer(0, size["w"], size["h"], size["w"] * 4, 1)
surf.attach(buf, 0, 0); surf.damage_buffer(0, 0, size["w"], size["h"]); surf.commit(); pump(0.5)

results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond))); print(("PASS " if cond else "FAIL ") + name, detail, flush=True)

print("app has keyboard:", state["kbd"] is not None, "touch:", state["touch"] is not None)
def vk(prop):
    r = subprocess.run(["busctl", "--user", "get-property", "org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard", prop], capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()

if os.environ.get("IT_ENABLE"):
    r = subprocess.run(os.environ["IT_ENABLE"].split() + ["enable"], capture_output=True, text=True); print(r.stdout.strip(), r.stderr.strip()); pump(2.5)
    ti.disable(); ti.commit(); pump(0.3); sync_ti(True); pump(0.3)      # leave and re-enter the text field
    check("`kritzel enable` switched KWin's virtual keyboard live", "true" in vk("available"), vk("available"))
def tap(x, y):
    fake.touch_down(0, x, y); fake.touch_frame(); pump(0.06); fake.touch_up(0); fake.touch_frame(); pump(0.15)
origin = [0.0, H - 280.0]      # top-left corner of the panel (docked: bottom of the screen)
panel_w = [float(W)]
def write(word, font="futural", x0=60.0):
    for st in strokes_for(word, font=font, height=85, x0=origin[0] + x0, baseline=origin[1] + 44 + 165, jitter=0.4, seed=3):
        pts = st.points
        fake.touch_down(0, *pts[0]); fake.touch_frame(); pump()
        for p in pts[1:]:
            fake.touch_motion(0, *p); fake.touch_frame()
        pump(); fake.touch_up(0); fake.touch_frame(); pump(0.03)
# where the keys are: ask the program's own layout code
sys.path.insert(0, os.environ["ROOT"]); os.environ["QT_QPA_PLATFORM"] = "offscreen"
from kritzel import ui as kui
from kritzel.config import Config as KConfig, Theme as KTheme
def view(page=kui.WRITE):
    v = kui.View(KTheme(), KConfig(os.devnull)); v.layout(panel_w[0], 280); v.show_page(page); return v
def btn(name):
    c = view().buttons[name].center(); return origin[0] + c.x(), origin[1] + c.y()
def symbol(text):
    c = next(k.rect for k in view(kui.SYMBOLS).page_keys if k.text == text).center(); return origin[0] + c.x(), origin[1] + c.y()
def commits(): return [e[1] for e in log if e[0] == "commit"]

print("vk available:", vk("available"), "active:", vk("active"), "visible:", vk("visible"), flush=True)
tap(300, 200); pump(1.0)                      # touch the "text field"
check("text-input focus reached the app", state["ti_enter"])
check("panel shown after touching the text field", "true" in vk("visible"), vk("visible"))

# a text field reached with mouse or keyboard must not bring the panel up
ti.disable(); ti.commit(); pump(0.6)
fake.pointer_motion_absolute(400, 200); fake.button(0x110, 1); fake.button(0x110, 0); pump(0.2)
sync_ti(True); pump(1.2)
check("a text field clicked with the mouse does not show the panel", "false" in vk("visible"), vk("visible"))
tap(300, 200); pump(1.0)
check("tapping that same field with a finger shows it", "true" in vk("visible"), vk("visible"))
ti.disable(); ti.commit(); pump(0.6)
fake.keyboard_key(15, 1); fake.keyboard_key(15, 0); pump(0.2)
sync_ti(True); pump(1.2)
check("a text field reached with the keyboard does not show the panel", "false" in vk("visible"), vk("visible"))
tap(300, 200); pump(1.0)
check("and a tap shows it again", "true" in vk("visible"), vk("visible"))

write("hello world"); pump(4.0)
check("handwriting committed", ("commit", "hello world ") in log, repr(log[-3:]))
write("again"); pump(2.2)
check("each chunk is followed by a space, none is put in front", text == "hello world again ", repr(text))

# alternatives: tap the second suggestion chip (to the right of the first one)
before = text
tap(6 + 70 + 6 + 30, H - 280 + 22); pump(0.6)
check("suggestion replaces the word (delete + insert through text-input)", ("delete", len(" again ")) in log and text != before and text.endswith(" ") and text.startswith("hello world ") and not [e for e in log if e[0] == "key" and e[1] == 14], repr(text))

# the same in an application that does not report its text: Backspace keys, then the new text
state["mute"] = True; t0 = text
write("again"); pump(2.2)
check("second word typed", text == t0 + "again ", repr(text))
tap(6 + 70 + 6 + 30, H - 280 + 22); pump(0.8)
bs = [e for e in log if e[0] == "key" and e[1] == 14 and e[2] == 1]
check("suggestion replaces the word (keys first, then the text)", len(bs) in (len("again "), len(" again ")) and text.startswith(t0[:-1]) and text != t0 + "again " and text.endswith(" ") and len(text) > len(t0) + 2, repr(text))
state["mute"] = False; sync_ti(); pump(0.2)

nkeys = len([e for e in log if e[0] == "key"])
tap(*btn("backspace")); pump(0.4)
check("backspace button sends a key", len([e for e in log if e[0] == "key"]) == nkeys + 2 and log[-2][:2] == ("key", 14), repr(log[-2:]))
for name, code in (("left", 105), ("right", 106), ("up", 103), ("down", 108)):
    tap(*btn(name)); pump(0.4)
    check(f"arrow button {name} reaches the application as a key", log[-2:] == [("key", code, 1), ("key", code, 0)], repr(log[-2:]))
t_before = text
tap(*btn("symbols")); pump(0.4); tap(*symbol("?")); pump(0.5); tap(*symbol("7")); pump(0.5); tap(*btn("symbols")); pump(0.4)
check("symbol keyboard types its characters", text == t_before + "?7", repr(text))
nlog = len(log); tap(*btn("copy")); pump(0.5); tap(*btn("paste")); pump(0.5)
pressed = [e[1:] for e in log[nlog:] if e[0] == "key"]
check("copy and paste reach the application as Ctrl+Insert and Shift+Insert",
      pressed == [(29, 1), (110, 1), (110, 0), (29, 0), (42, 1), (110, 1), (110, 0), (42, 0)], repr(pressed))
t_before = text
write("ok"); tap(*btn("enter")); pump(2.0)                  # write, then tap Enter before the timer fires
check("enter flushes pending ink first", text.endswith("\n") and len(text) > len(t_before) + 1, repr(text))

tap(*btn("hide")); pump(0.8)
check("hide button deactivates KWin's virtual keyboard", "false" in vk("active"), vk("active"))
kwin6 = not subprocess.run([os.environ.get("KWIN", "kwin_wayland"), "--version"], capture_output=True, text=True).stdout.strip().split()[-1].startswith("5.")
if kwin6:   # KWin 5.27 re-activates on every text-input commit; KWin 6 only on focus/tap
    fake.keyboard_key(30, 1); fake.keyboard_key(30, 0); pump(0.2); text += "a"; sync_ti(); pump(0.8)
    check("typing on a keyboard keeps it hidden", "false" in vk("visible"), vk("visible"))
else:
    print("SKIP typing-keeps-it-hidden (needs KWin 6)")
tap(300, 200); pump(1.0)
check("touching the field shows it again", "true" in vk("visible"), vk("visible"))
# mouse: draw with the pointer (left button held)
n0 = len([e for e in log if e[0] == "commit"])
for st_ in strokes_for("mouse", font="futural", height=85, x0=60.0, baseline=H - 280 + 44 + 165, jitter=0.4, seed=4):
    fake.pointer_motion_absolute(*st_.points[0]); fake.button(0x110, 1); pump()
    for p_ in st_.points[1:]: fake.pointer_motion_absolute(*p_)
    pump(); fake.button(0x110, 0); pump(0.03)
pump(2.0)
cm = [e for e in log if e[0] == "commit"]
check("mouse drawing works too", len(cm) == n0 + 1 and cm[-1][1].strip() == "mouse", repr(cm[-1:]))

# delete-word button: first the insertion just made, then the word before the cursor
import re
t0 = text
tap(*btn("delword")); pump(0.6)
check("word button removes the last insertion", text == t0[:-len("mouse ")], repr(text))
t1 = text
tap(*btn("delword")); pump(0.6)
check("word button removes the previous word", text == re.sub(r"\w+\s*$", "", t1) and text != t1, repr(text))

# floating mode
if not os.environ.get("KRITZEL_LAYER_SCOPE"):
    # KWin 5 gives the focus to any touched layer surface: Kritzel must notice and dock again
    tap(*btn("float")); pump(1.2)
    origin[:] = [(W - 760) // 2, H - 280 - 48]; panel_w[0] = 760.0
    for _ in range(3):
        fake.touch_down(0, origin[0] + 200, origin[1] + 150); fake.touch_frame(); pump(0.05)
        fake.touch_motion(0, origin[0] + 230, origin[1] + 150); fake.touch_frame(); pump(0.05)
        fake.touch_up(0); fake.touch_frame(); pump(1.0)
    origin[:] = [0.0, H - 280.0]; panel_w[0] = float(W)
    check("focus-stealing compositor: falls back to the docked panel", "true" in vk("visible"), vk("visible"))
    n0 = len(commits()); write("docked"); pump(2.2)
    check("writing still works after the fallback", len(commits()) == n0 + 1 and commits()[-1].strip() == "docked", repr(commits()[-1:]))
    print("final text:", repr(text)); sys.stdout.flush(); os._exit(0 if all(ok for _, ok in results) else 1)
tap(*btn("float")); pump(1.2)
print("INFO KWin reports visible =", vk("visible"), "in floating mode")
origin[:] = [(W - 760) // 2, H - 280 - 48]; panel_w[0] = 760.0
n0 = len(commits()); write("float"); pump(2.2)
check("writing on the floating panel", len(commits()) == n0 + 1 and commits()[-1].strip() == "float", repr(commits()[-1:]))
gx, gy = origin[0] + 16, origin[1] + 22                       # drag by the bar with a finger
fake.touch_down(0, gx, gy); fake.touch_frame(); pump(0.05)
for i in range(1, 21):
    fake.touch_motion(0, gx - 5 * i, gy - 10 * i); fake.touch_frame(); pump(0.03)
for i in range(6):
    fake.touch_motion(0, gx - 100 + (i % 2) * 0.5, gy - 200); fake.touch_frame(); pump(0.05)
fake.touch_up(0); fake.touch_frame(); pump(0.3)
origin[0] -= 100; origin[1] -= 200
n0 = len(commits()); write("moved"); pump(2.2)
check("finger drag moved the floating panel", len(commits()) == n0 + 1 and commits()[-1].strip() == "moved", repr(commits()[-1:]))
gx, gy = origin[0] + 16, origin[1] + 22                       # and with the mouse
fake.pointer_motion_absolute(gx, gy); pump(0.05); fake.button(0x110, 1); pump(0.05)
for i in range(1, 31):
    fake.pointer_motion_absolute(gx + 5 * i, gy + 4 * i); pump(0.02)
for i in range(6):
    fake.pointer_motion_absolute(gx + 150 + (i % 2) * 0.5, gy + 120); pump(0.05)
fake.button(0x110, 0); pump(0.3)
origin[0] += 150; origin[1] += 120
n0 = len(commits()); write("again"); pump(2.2)
check("mouse drag moved the floating panel", len(commits()) == n0 + 1 and commits()[-1].strip() == "again", repr(commits()[-1:]))
tap(*btn("float")); pump(1.2)
origin[:] = [0.0, H - 280.0]; panel_w[0] = float(W)
check("docked again", "true" in vk("visible"), vk("visible"))
n0 = len(commits()); write("docked"); pump(2.2)
check("writing after docking again", len(commits()) == n0 + 1 and commits()[-1].strip() == "docked", repr(commits()[-1:]))

if os.environ.get("IT_ENABLE"):
    # `kritzel show` for a text field that was reached with the mouse: this KWin refuses
    # to display a docked panel then, so the panel has to come up floating
    ti.disable(); ti.commit(); pump(0.6)
    fake.pointer_motion_absolute(400, 200); fake.button(0x110, 1); fake.button(0x110, 0); pump(0.2)
    sync_ti(True); pump(1.2)
    check("mouse focus after docking: still no panel", "false" in vk("visible"), vk("visible"))
    r = subprocess.run(os.environ["IT_ENABLE"].split() + ["show"], capture_output=True, text=True); pump(2.0)
    if "true" in vk("visible"):
        print("INFO this KWin displays the docked panel on `kritzel show`")
    else:
        origin[:] = [(W - 760) // 2 - 100 + 150, H - 280 - 48 - 200 + 120]; panel_w[0] = 760.0     # where it floated before
        n0 = len(commits()); write("shown"); pump(2.2)
        check("`kritzel show` after mouse focus: the panel floats", len(commits()) == n0 + 1 and commits()[-1].strip() == "shown", repr(commits()[-1:]))
        tap(*btn("float")); pump(1.2)
        origin[:] = [0.0, H - 280.0]; panel_w[0] = float(W)
        check("docked again by a finger tap", "true" in vk("visible"), vk("visible"))

if os.environ.get("IT_ENABLE"):
    def kread():
        return subprocess.run(["kreadconfig5", "--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod"], capture_output=True, text=True).stdout.strip()
    pid = int(open(os.path.join(os.environ["XDG_RUNTIME_DIR"], "kritzel.pid")).read())
    tap(*btn("keyboard")); pump(2.5)
    def alive(p):
        try:
            os.kill(p, 0); return open(f"/proc/{p}/stat").read().split()[2] != "Z"
        except OSError: return False
    check("keyboard button switches to the normal keyboard", kread().endswith("org.kde.plasma.keyboard.desktop") and not alive(pid), kread())
    r = subprocess.run(os.environ["IT_ENABLE"].split() + ["switch"], capture_output=True, text=True); print(r.stdout.strip(), r.stderr.strip()); pump(2.5)
    check("`kritzel switch` switches back to handwriting", kread().endswith("kritzel.desktop"), kread())
    pidf = os.path.join(os.environ["XDG_RUNTIME_DIR"], "kritzel.pid")
    ti.disable(); ti.commit(); pump(0.3); sync_ti(True); pump(1.5); pid1 = int(open(pidf).read())
    r = subprocess.run(os.environ["IT_ENABLE"].split() + ["restart"], capture_output=True, text=True); print(r.stdout.strip(), r.stderr.strip()); pump(1.0)
    ti.disable(); ti.commit(); pump(0.3); sync_ti(True); pump(1.5); pid2 = int(open(pidf).read())
    check("`kritzel restart` starts a fresh panel", alive(pid1) and pid2 != pid1 or (not alive(pid1) and alive(pid2) and pid2 != pid1), f"{pid1} -> {pid2}")
    r = subprocess.run(os.environ["IT_ENABLE"].split() + ["disable"], capture_output=True, text=True); print(r.stdout.strip(), r.stderr.strip()); pump(1.0)
    check("`kritzel disable` gives the slot back", not kread().endswith("kritzel.desktop"), kread())
print("final text:", repr(text)); print("log:", log, flush=True)
sys.stdout.flush(); os._exit(0 if all(ok for _, ok in results) else 1)
