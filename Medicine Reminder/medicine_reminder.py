import tkinter as tk
from tkinter import messagebox
import os
import time
import threading
from datetime import datetime
import sys

# voice
import pyttsx3

# tray
import pystray
from PIL import Image, ImageDraw

# ---------------- Voice setup ----------------
engine = pyttsx3.init()
engine.setProperty('rate', 170)
engine.setProperty('volume', 1.0)

def speak_blocking(text):
    """Blocking speak call (used inside a thread)."""
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print("TTS error:", e)

def speak(text):
    """Non-blocking speak (spawns a thread so UI doesn't freeze)."""
    t = threading.Thread(target=speak_blocking, args=(text,), daemon=True)
    t.start()

# ---------------- Ensure folders ----------------
os.makedirs("reminders", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# ---------------- Globals & root ----------------
root = tk.Tk()
root.title("Medicine Reminder PRO")
root.geometry("460x560")

current_user = None
reminders = []  # {"name":..., "time":"HH:MM", "triggered":False, "active":False, "repeat_min": N}

# Default repeat minutes (can be changed per reminder later if desired)
DEFAULT_REPEAT_MIN = 2

# Keep reference to tray icon
tray_icon = None
is_in_tray = False

# ---------------- File helpers ----------------
def reminder_file():
    if not current_user:
        raise RuntimeError("No user logged in")
    return os.path.join("reminders", f"{current_user}_reminders.txt")

def log_file():
    if not current_user:
        raise RuntimeError("No user logged in")
    return os.path.join("logs", f"{current_user}_log.txt")

# ---------------- Tray icon creation ----------------
def create_image(width=64, height=64, color1=(20, 120, 220), color2=(255, 255, 255)):
    img = Image.new('RGBA', (width, height), color1)
    d = ImageDraw.Draw(img)
    # simple pill icon
    d.ellipse((10, 18, 38, 46), fill=color2)
    d.rectangle((24, 10, 54, 54), fill=color2)
    return img

def on_tray_quit(icon, item):
    # save and quit
    try:
        save_reminders()
    except Exception:
        pass
    icon.stop()
    root.after(0, root.destroy)
    sys.exit(0)

def on_tray_restore(icon, item):
    global is_in_tray
    is_in_tray = False
    root.after(0, restore_from_tray)

def start_tray():
    global tray_icon
    image = create_image()
    menu = pystray.Menu(
        pystray.MenuItem("Restore", on_tray_restore),
        pystray.MenuItem("Exit", on_tray_quit),
    )
    tray_icon = pystray.Icon("MedicineReminder", image, "Medicine Reminder PRO", menu)
    tray_icon.run()

def minimize_to_tray():
    global is_in_tray, tray_thread
    if is_in_tray:
        return
    is_in_tray = True
    root.withdraw()
    # start tray thread if not running
    if not tray_icon or not any(t.name == "TrayThread" for t in threading.enumerate()):
        tray_thread = threading.Thread(target=start_tray, daemon=True, name="TrayThread")
        tray_thread.start()

def restore_from_tray():
    global is_in_tray, tray_icon
    is_in_tray = False
    try:
        # stop tray icon if running
        if tray_icon:
            try:
                tray_icon.stop()
            except Exception:
                pass
            # re-create icon on next minimize
            tray_icon = None
    except Exception:
        pass
    root.deiconify()
    root.lift()

# ---------------- Reminder popup with repeating ----------------
def show_reminder_repeating(med):
    """
    med is a dict from reminders list.
    This function shows a popup (on main thread) that repeats speak() every med['repeat_min'] minutes
    until the user clicks Taken or Skip. We use med['active'] to track active popups.
    """
    # if already active, do nothing
    if med.get("active", False):
        return

    med["active"] = True
    med_name = med["name"]
    repeat_ms = int(med.get("repeat_min", DEFAULT_REPEAT_MIN) * 60 * 1000)

    popup = tk.Toplevel(root)
    popup.title("Medicine Reminder")
    popup.geometry("380x170")
    popup.resizable(False, False)

    tk.Label(popup, text=f"Time to take: {med_name}", font=('Arial', 13, 'bold')).pack(pady=(12,8))
    tk.Label(popup, text=f"Reminder will repeat every {med.get('repeat_min', DEFAULT_REPEAT_MIN)} minutes until confirmed.", font=('Arial', 9)).pack()

    btn_frame = tk.Frame(popup)
    btn_frame.pack(pady=12)

    def mark_taken():
        with open(log_file(), "a") as f:
            f.write(f"{datetime.now()} - {med_name} - Taken\n")
        med["active"] = False
        popup.destroy()

    def mark_skipped():
        with open(log_file(), "a") as f:
            f.write(f"{datetime.now()} - {med_name} - Skipped\n")
        med["active"] = False
        popup.destroy()

    def snooze_min():
        # snooze for repeat_min (same as repeating behavior)
        med["active"] = False
        popup.destroy()

    tk.Button(btn_frame, text="Taken", width=12, bg="green", fg="white", command=mark_taken).grid(row=0, column=0, padx=6)
    tk.Button(btn_frame, text="Skip", width=12, bg="red", fg="white", command=mark_skipped).grid(row=0, column=1, padx=6)
    tk.Button(btn_frame, text="Snooze", width=12, bg="orange", fg="white", command=snooze_min).grid(row=0, column=2, padx=6)

    # Bring popup to front even if app is minimized
    popup.attributes("-topmost", True)
    popup.after(0, lambda: popup.attributes("-topmost", False))

    # speak immediately and then schedule repeating speaks while popup exists
    speak(f"Time to take {med_name}")

    def repeat_speaks():
        # this runs in main thread via after; if popup still exists and med active, speak again and reschedule
        if med.get("active", False):
            speak(f"Reminder: Take {med_name}")
            popup.after(repeat_ms, repeat_speaks)

    # schedule first repeat after repeat_ms
    popup.after(repeat_ms, repeat_speaks)

# ---------------- Reminder checker (background) ----------------
def reminder_checker():
    while True:
        now = datetime.now().strftime("%H:%M")
        for med in reminders:
            # if scheduled time matches and not triggered today or currently active
            if med["time"] == now and not med.get("triggered", False):
                med["triggered"] = True
                # ensure popup shows in main thread
                root.after(0, lambda m=med: show_reminder_repeating(m))
        time.sleep(20)

# ---------------- Reset triggers at midnight ----------------
def reset_triggers():
    last_reset_date = datetime.now().date()
    while True:
        now_dt = datetime.now()
        if now_dt.date() != last_reset_date:
            for med in reminders:
                med["triggered"] = False
            last_reset_date = now_dt.date()
        time.sleep(30)

# ---------------- Save & Load ----------------
def save_reminders():
    try:
        path = reminder_file()
    except RuntimeError:
        return
    with open(path, "w") as f:
        for med in reminders:
            name = med['name'].replace("|", " ")
            repeat_min = int(med.get("repeat_min", DEFAULT_REPEAT_MIN))
            f.write(f"{name}|{med['time']}|{repeat_min}\n")

def load_reminders():
    reminders.clear()
    med_list.delete(0, tk.END)
    try:
        path = reminder_file()
    except RuntimeError:
        return
    if not os.path.exists(path):
        return
    with open(path, "r") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) >= 2:
                name = parts[0]
                time_str = parts[1]
                repeat_min = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else DEFAULT_REPEAT_MIN
                try:
                    datetime.strptime(time_str, "%H:%M")
                except ValueError:
                    continue
                reminders.append({"name": name, "time": time_str, "triggered": False, "active": False, "repeat_min": repeat_min})
                med_list.insert(tk.END, f"{name} at {time_str} (repeat {repeat_min}m)")

# ---------------- Add / Remove reminders ----------------
def add_reminder():
    name = med_name_entry.get().strip()
    time_str = time_entry.get().strip()
    repeat_min_str = repeat_entry.get().strip()
    if not name or not time_str:
        messagebox.showerror("Missing Data", "Please enter medicine name and time.")
        return
    try:
        datetime.strptime(time_str, "%H:%M")
    except ValueError:
        messagebox.showerror("Invalid Time", "Time format must be HH:MM (24-hour).")
        return
    try:
        repeat_min = int(repeat_min_str) if repeat_min_str else DEFAULT_REPEAT_MIN
        if repeat_min <= 0:
            raise ValueError
    except ValueError:
        messagebox.showerror("Invalid Repeat", "Repeat minutes must be a positive integer.")
        return

    reminders.append({"name": name, "time": time_str, "triggered": False, "active": False, "repeat_min": repeat_min})
    med_list.insert(tk.END, f"{name} at {time_str} (repeat {repeat_min}m)")
    save_reminders()
    med_name_entry.delete(0, tk.END)
    time_entry.delete(0, tk.END)
    repeat_entry.delete(0, tk.END)

def remove_selected():
    sel = med_list.curselection()
    if not sel:
        messagebox.showinfo("Select", "Select a reminder to remove.")
        return
    idx = sel[0]
    med_list.delete(idx)
    # rebuild reminders from listbox entries
    new_reminders = []
    for i in range(med_list.size()):
        txt = med_list.get(i)
        # parse "name at HH:MM (repeat Nm)"
        if " at " in txt:
            try:
                left, right = txt.split(" at ", 1)
                time_part, repeat_part = right.rsplit(" (repeat ", 1)
                repeat_min = int(repeat_part.rstrip("m)"))
                new_reminders.append({"name": left, "time": time_part, "triggered": False, "active": False, "repeat_min": repeat_min})
            except Exception:
                # fallback simple parse
                if " at " in txt:
                    nm, tm = txt.rsplit(" at ", 1)
                    new_reminders.append({"name": nm, "time": tm, "triggered": False, "active": False, "repeat_min": DEFAULT_REPEAT_MIN})
    reminders[:] = new_reminders
    save_reminders()

# ---------------- Login / Logout ----------------
def login():
    global current_user
    username = username_entry.get().strip()
    password = password_entry.get().strip()

    if not username or not password:
        messagebox.showerror("Login Failed", "Username and password required.")
        return

    users_path = "users.txt"
    if not os.path.exists(users_path):
        open(users_path, "w").close()

    found = False
    with open(users_path, "r") as f:
        for line in f:
            if "|" in line:
                u, p = line.strip().split("|", 1)
                if u == username and p == password:
                    found = True
                    break

    if not found:
        with open(users_path, "a") as f:
            f.write(f"{username}|{password}\n")

    current_user = username
    login_frame.pack_forget()
    app_frame.pack(fill="both", expand=True)
    welcome_label.config(text=f"Welcome, {current_user} — Medicine Reminder PRO")
    load_reminders()
    # start background threads once
    if not any(t.name == "ReminderChecker" for t in threading.enumerate()):
        threading.Thread(target=reminder_checker, daemon=True, name="ReminderChecker").start()
    if not any(t.name == "ResetTriggers" for t in threading.enumerate()):
        threading.Thread(target=reset_triggers, daemon=True, name="ResetTriggers").start()

def logout():
    global current_user
    if messagebox.askyesno("Logout", "Do you want to logout?"):
        save_reminders()
        current_user = None
        app_frame.pack_forget()
        login_frame.pack()

# ---------------- Closing & minimize handlers ----------------
def on_closing():
    if messagebox.askokcancel("Quit", "Do you want to quit?"):
        try:
            save_reminders()
        except Exception:
            pass
        try:
            if tray_icon:
                tray_icon.stop()
        except Exception:
            pass
        root.destroy()
        sys.exit(0)

def minimize_action():
    # minimize to tray
    minimize_to_tray()

root.protocol("WM_DELETE_WINDOW", on_closing)

# ---------------- GUI Layout ----------------
# Login Frame
login_frame = tk.Frame(root, padx=12, pady=12)
tk.Label(login_frame, text="Login / Register", font=('Arial', 16)).pack(pady=(0,10))
tk.Label(login_frame, text="Username").pack(anchor="w")
username_entry = tk.Entry(login_frame, width=36)
username_entry.pack(pady=4)
tk.Label(login_frame, text="Password").pack(anchor="w")
password_entry = tk.Entry(login_frame, show="*", width=36)
password_entry.pack(pady=4)
tk.Button(login_frame, text="Login / Register", command=login, bg="blue", fg="white", width=22).pack(pady=12)
login_frame.pack(pady=20)

# App Frame
app_frame = tk.Frame(root, padx=12, pady=12)
welcome_label = tk.Label(app_frame, text="Welcome to Medicine Reminder PRO", font=('Arial', 14))
welcome_label.pack(pady=(0,10))

# Input area (includes repeat minutes)
tk.Label(app_frame, text="Medicine Name").pack(anchor="w")
med_name_entry = tk.Entry(app_frame, width=44)
med_name_entry.pack(pady=4)

tk.Label(app_frame, text="Time (HH:MM 24-hour)").pack(anchor="w")
time_entry = tk.Entry(app_frame, width=20)
time_entry.pack(pady=4)

tk.Label(app_frame, text="Repeat (minutes) — will repeat voice & popup until confirmed").pack(anchor="w")
repeat_entry = tk.Entry(app_frame, width=8)
repeat_entry.pack(pady=4)

controls_frame = tk.Frame(app_frame)
controls_frame.pack(pady=10)
tk.Button(controls_frame, text="Add Reminder", command=add_reminder, bg="green", fg="white", width=14).grid(row=0, column=0, padx=6)
tk.Button(controls_frame, text="Remove Selected", command=remove_selected, bg="orange", fg="white", width=16).grid(row=0, column=1, padx=6)
tk.Button(controls_frame, text="Minimize to Tray", command=minimize_action, width=14).grid(row=0, column=2, padx=6)
tk.Button(controls_frame, text="Logout", command=logout, width=12).grid(row=0, column=3, padx=6)

tk.Label(app_frame, text="Your Reminders").pack(anchor="w", pady=(8,0))
med_list = tk.Listbox(app_frame, width=64, height=14)
med_list.pack(pady=6)

tk.Label(app_frame, text="Keep this app open or minimized to tray to receive alerts", fg="red").pack(pady=(6,0))

# Start Tkinter mainloop
root.mainloop()
