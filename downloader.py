#!/usr/bin/env python3
"""
RimWorld Workshop Mod Downloader
---------------------------------
A small GUI tool that downloads RimWorld Steam Workshop mods via SteamCMD.

Features:
- Paste one or more Steam Workshop links (or raw IDs), one per line.
- "Download All" runs SteamCMD (anonymous login) for every mod in the list.
- Optional automatic dependency resolution: for each mod, it queries Steam's
  public Web API for that item's "required items" and adds them to the
  download queue too (recursively, so dependencies-of-dependencies are
  caught as well).
- The link list is saved to disk (saved_links.json) and reloaded automatically
  next time you open the tool.
- The SteamCMD path is saved to config.json so you only set it up once.

Requirements:
- Python 3.8+
- SteamCMD installed somewhere on your machine
  (https://developer.valvesoftware.com/wiki/SteamCMD)
- No third-party packages needed (uses only the standard library).

RimWorld's Steam AppID (294100) is hardcoded below.
"""

import os
import re
import json
import shutil
import threading
import subprocess
import urllib.request
import urllib.parse
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

RIMWORLD_APPID = "294100"  # default preset only; active game now comes from games.json
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
LINKS_FILE = os.path.join(BASE_DIR, "saved_links.json")
GAMES_FILE = os.path.join(BASE_DIR, "games.json")

WORKSHOP_ID_RE = re.compile(r"[?&]id=(\d+)")


def load_games():
    """Load the list of {"name", "appid"} game presets, seeding a RimWorld default."""
    games = load_json(GAMES_FILE, None)
    if not games:
        games = [{"name": "RimWorld", "appid": RIMWORLD_APPID}]
        save_json(GAMES_FILE, games)
    return games


def save_games(games):
    save_json(GAMES_FILE, games)


# --------------------------------------------------------------------------
# Helpers: config / saved links persistence
# --------------------------------------------------------------------------

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# --------------------------------------------------------------------------
# Helpers: workshop id extraction + dependency resolution
# --------------------------------------------------------------------------

def extract_id(url_or_id):
    """Pull a numeric workshop id out of a full URL, or accept a bare id."""
    text = url_or_id.strip()
    if not text:
        return None
    match = WORKSHOP_ID_RE.search(text)
    if match:
        return match.group(1)
    if text.isdigit():
        return text
    return None


def get_item_details(published_file_id):
    """
    Query Steam's public Web API for a workshop item's title and its
    required items (dependencies). No API key is needed for this endpoint.
    Returns (title, [dependency_ids]).
    """
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
    data = urllib.parse.urlencode({
        "itemcount": 1,
        "publishedfileids[0]": published_file_id,
    }).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    details = result.get("response", {}).get("publishedfiledetails", [])
    if not details:
        return published_file_id, []

    item = details[0]
    title = item.get("title", published_file_id)
    children = item.get("children", []) or []
    dep_ids = [c.get("publishedfileid") for c in children if c.get("publishedfileid")]
    return title, dep_ids


def resolve_with_dependencies(root_ids, log):
    """
    Given a list of root workshop ids, recursively resolve required items.
    Returns (ordered_unique_id_list, {id: title}).
    """
    seen = set()
    order = []
    titles = {}
    queue = list(root_ids)

    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        order.append(current)
        try:
            title, deps = get_item_details(current)
        except Exception as e:
            log(f"  ! Couldn't fetch info for {current}: {e}\n")
            titles[current] = current
            continue
        titles[current] = title
        new_deps = [d for d in deps if d not in seen]
        if new_deps:
            log(f"  - {title} ({current}) requires {len(new_deps)} other item(s)\n")
        queue.extend(new_deps)

    return order, titles


# --------------------------------------------------------------------------
# SteamCMD runner
# --------------------------------------------------------------------------
#
# IMPORTANT: SteamCMD has a well-known quirk where, if you queue multiple
# +workshop_download_item commands in a single session, it can silently
# delete an earlier item's folder while processing a later one (its content
# cleanup pass treats already-downloaded items as orphaned under an
# anonymous login). To avoid mods "disappearing" right after download, we
# run SteamCMD once PER mod, and copy that mod out to the real Mods folder
# immediately afterward, before starting the next download.

def download_one_item(steamcmd_path, mod_id, appid, log):
    cmd = [steamcmd_path, "+login", "anonymous",
           "+workshop_download_item", appid, mod_id, "+quit"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    for line in proc.stdout:
        log(line)
    proc.wait()
    return proc.returncode


def run_steamcmd(steamcmd_path, mod_ids, dest_dir, appid, log, on_done):
    if not mod_ids:
        log("Nothing to download.\n")
        on_done()
        return
    if not steamcmd_path or not os.path.exists(steamcmd_path):
        log("ERROR: SteamCMD path is not set or doesn't exist. "
            "Set it under Settings first.\n")
        on_done()
        return

    steamcmd_root = os.path.dirname(steamcmd_path)
    content_root = os.path.join(
        steamcmd_root, "steamapps", "workshop", "content", appid
    )

    if not dest_dir:
        log("NOTE: No destination Mods folder set - downloaded items will be left "
            "in SteamCMD's workshop content folder, where a later run may "
            "delete them. Set a Mods folder in Settings to avoid this.\n")

    total = len(mod_ids)
    succeeded, failed = [], []

    for i, mod_id in enumerate(mod_ids, start=1):
        log(f"\n--- [{i}/{total}] Downloading item {mod_id} (AppID {appid}) ---\n")
        try:
            code = download_one_item(steamcmd_path, mod_id, appid, log)
        except FileNotFoundError:
            log("ERROR: Couldn't launch SteamCMD executable. Check the path.\n")
            failed.append(mod_id)
            continue
        except Exception as e:
            log(f"ERROR: {e}\n")
            failed.append(mod_id)
            continue

        src = os.path.join(content_root, mod_id)
        if code != 0 or not os.path.isdir(src):
            log(f"  ! Item {mod_id} did not download successfully (exit code {code}).\n")
            failed.append(mod_id)
            continue

        if dest_dir:
            dest = os.path.join(dest_dir, mod_id)
            try:
                if os.path.isdir(dest):
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                log(f"  Copied to {dest}\n")
                succeeded.append(mod_id)
            except Exception as e:
                log(f"  ! Downloaded but failed to copy into Mods folder: {e}\n")
                failed.append(mod_id)
        else:
            log(f"  Downloaded to {src}\n")
            succeeded.append(mod_id)

    log(f"\n=== Done: {len(succeeded)} succeeded, {len(failed)} failed (of {total}) ===\n")
    if failed:
        log("Failed items: " + ", ".join(failed) + "\n")
    on_done()


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class ModDownloaderApp:
    def __init__(self, root):
        self.root = root
        root.title("RimWorld Workshop Mod Downloader")
        root.geometry("760x640")

        self.config_data = load_json(CONFIG_FILE, {"steamcmd_path": "", "mods_path": ""})
        self.saved_links = load_json(LINKS_FILE, [])
        self.games = load_games()

        active_appid = self.config_data.get("active_appid")
        if not any(g["appid"] == active_appid for g in self.games):
            active_appid = self.games[0]["appid"]
        self.active_appid = active_appid

        self.include_deps = tk.BooleanVar(value=True)
        self.busy = False

        self._build_widgets()
        self._update_active_game_label()
        self._refresh_list()

    # -- UI construction ---------------------------------------------------

    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # Active game bar
        game_frame = tk.Frame(self.root, bg="#eef")
        game_frame.pack(fill="x", padx=8, pady=(8, 0))
        self.active_game_label = tk.Label(
            game_frame, text="", bg="#eef", font=("TkDefaultFont", 10, "bold"))
        self.active_game_label.pack(side="left", padx=6, pady=6)
        tk.Button(game_frame, text="Change Game...", command=self._open_game_manager).pack(
            side="right", padx=6, pady=6)

        # SteamCMD path row
        path_frame = tk.Frame(self.root)
        path_frame.pack(fill="x", **pad)
        tk.Label(path_frame, text="SteamCMD path:").pack(side="left")
        self.path_var = tk.StringVar(value=self.config_data.get("steamcmd_path", ""))
        tk.Entry(path_frame, textvariable=self.path_var).pack(
            side="left", fill="x", expand=True, padx=6)
        tk.Button(path_frame, text="Browse...", command=self._browse_steamcmd).pack(side="left")
        tk.Button(path_frame, text="Save", command=self._save_steamcmd_path).pack(side="left", padx=(4, 0))

        # Mods folder row
        mods_frame = tk.Frame(self.root)
        mods_frame.pack(fill="x", **pad)
        tk.Label(mods_frame, text="Game Mods Folder:").pack(side="left")
        self.mods_path_var = tk.StringVar(value=self.config_data.get("mods_path", ""))
        tk.Entry(mods_frame, textvariable=self.mods_path_var).pack(
            side="left", fill="x", expand=True, padx=6)
        tk.Button(mods_frame, text="Browse...", command=self._browse_mods_folder).pack(side="left")
        tk.Button(mods_frame, text="Save", command=self._save_mods_path).pack(side="left", padx=(4, 0))
        tk.Label(
            self.root,
            text="(e.g. .../Steam/steamapps/common/<Game>/Mods - each mod is copied "
                 "here right after it downloads, so SteamCMD can't clean it up)",
            fg="#666", font=("TkDefaultFont", 8)
        ).pack(anchor="w", padx=8)

        # Input textbox for links
        tk.Label(self.root, text="Paste Workshop links or IDs (one per line):").pack(
            anchor="w", **pad)
        self.input_box = scrolledtext.ScrolledText(self.root, height=6)
        self.input_box.pack(fill="x", padx=8)

        entry_btns = tk.Frame(self.root)
        entry_btns.pack(fill="x", **pad)
        tk.Button(entry_btns, text="Add to List", command=self._add_links).pack(side="left")
        tk.Checkbutton(
            entry_btns, text="Resolve & include dependencies automatically",
            variable=self.include_deps
        ).pack(side="left", padx=12)

        # Saved list
        tk.Label(self.root, text="Saved mod list:").pack(anchor="w", **pad)
        list_frame = tk.Frame(self.root)
        list_frame.pack(fill="both", expand=False, padx=8)
        self.listbox = tk.Listbox(list_frame, height=8, selectmode=tk.EXTENDED)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.config(yscrollcommand=scrollbar.set)

        list_btns = tk.Frame(self.root)
        list_btns.pack(fill="x", **pad)
        tk.Button(list_btns, text="Remove Selected", command=self._remove_selected).pack(side="left")
        tk.Button(list_btns, text="Clear List", command=self._clear_list).pack(side="left", padx=6)
        self.download_btn = tk.Button(
            list_btns, text="Download All", command=self._start_download,
            bg="#2e7d32", fg="white")
        self.download_btn.pack(side="right")
        self.download_selected_btn = tk.Button(
            list_btns, text="Download Selected", command=self._start_download_selected,
            bg="#1565c0", fg="white")
        self.download_selected_btn.pack(side="right", padx=(0, 6))
        tk.Label(
            self.root,
            text="Tip: click a mod in the list, or Ctrl+click / Shift+click to pick several, "
                 "then use \"Download Selected\".",
            fg="#666", font=("TkDefaultFont", 8)
        ).pack(anchor="w", padx=8)

        # Log output
        tk.Label(self.root, text="Log:").pack(anchor="w", **pad)
        self.log_box = scrolledtext.ScrolledText(self.root, height=14, state="disabled", bg="#111", fg="#ddd")
        self.log_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    # -- Config / link persistence -----------------------------------------

    def _browse_steamcmd(self):
        filename = filedialog.askopenfilename(
            title="Locate steamcmd executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")]
        )
        if filename:
            self.path_var.set(filename)

    def _save_steamcmd_path(self):
        self.config_data["steamcmd_path"] = self.path_var.get().strip()
        save_json(CONFIG_FILE, self.config_data)
        self._log(f"Saved SteamCMD path: {self.config_data['steamcmd_path']}\n")

    def _browse_mods_folder(self):
        folder = filedialog.askdirectory(title="Locate your RimWorld Mods folder")
        if folder:
            self.mods_path_var.set(folder)

    def _save_mods_path(self):
        self.config_data["mods_path"] = self.mods_path_var.get().strip()
        save_json(CONFIG_FILE, self.config_data)
        self._log(f"Saved Game Mods Folder: {self.config_data['mods_path']}\n")

    # -- Game (AppID) management --------------------------------------------

    def _active_game_name(self):
        for g in self.games:
            if g["appid"] == self.active_appid:
                return g["name"]
        return "Unknown"

    def _update_active_game_label(self):
        self.active_game_label.config(
            text=f"Active Game: {self._active_game_name()}  (AppID {self.active_appid})"
        )

    def _open_game_manager(self):
        win = tk.Toplevel(self.root)
        win.title("Manage Games")
        win.geometry("380x320")
        win.transient(self.root)

        tk.Label(win, text="Games:").pack(anchor="w", padx=8, pady=(8, 0))
        listbox = tk.Listbox(win)
        listbox.pack(fill="both", expand=True, padx=8, pady=4)

        def refresh():
            listbox.delete(0, tk.END)
            for g in self.games:
                marker = " (active)" if g["appid"] == self.active_appid else ""
                listbox.insert(tk.END, f"{g['name']} - {g['appid']}{marker}")

        def do_activate():
            sel = listbox.curselection()
            if not sel:
                messagebox.showinfo("No selection", "Select a game first.", parent=win)
                return
            self.active_appid = self.games[sel[0]]["appid"]
            self.config_data["active_appid"] = self.active_appid
            save_json(CONFIG_FILE, self.config_data)
            self._update_active_game_label()
            refresh()

        def do_delete():
            sel = listbox.curselection()
            if not sel:
                return
            if len(self.games) == 1:
                messagebox.showwarning(
                    "Can't delete", "You need at least one game in the list.", parent=win)
                return
            idx = sel[0]
            removed = self.games.pop(idx)
            save_games(self.games)
            if removed["appid"] == self.active_appid:
                self.active_appid = self.games[0]["appid"]
                self.config_data["active_appid"] = self.active_appid
                save_json(CONFIG_FILE, self.config_data)
                self._update_active_game_label()
            refresh()

        def do_new():
            self._open_new_game_dialog(win, on_added=refresh)

        refresh()

        btns = tk.Frame(win)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(btns, text="New...", command=do_new).pack(side="left")
        tk.Button(btns, text="Activate", command=do_activate, bg="#1565c0", fg="white").pack(
            side="left", padx=6)
        tk.Button(btns, text="Delete", command=do_delete).pack(side="left")
        tk.Button(btns, text="Close", command=win.destroy).pack(side="right")

    def _open_new_game_dialog(self, parent, on_added):
        dlg = tk.Toplevel(parent)
        dlg.title("New Game")
        dlg.geometry("300x150")
        dlg.transient(parent)

        tk.Label(dlg, text="Game name:").pack(anchor="w", padx=10, pady=(10, 0))
        name_var = tk.StringVar()
        tk.Entry(dlg, textvariable=name_var).pack(fill="x", padx=10)

        tk.Label(dlg, text="Steam AppID:").pack(anchor="w", padx=10, pady=(8, 0))
        appid_var = tk.StringVar()
        tk.Entry(dlg, textvariable=appid_var).pack(fill="x", padx=10)

        def do_add():
            name = name_var.get().strip()
            appid = appid_var.get().strip()
            if not name or not appid:
                messagebox.showwarning("Missing info", "Enter both a name and an AppID.", parent=dlg)
                return
            if not appid.isdigit():
                messagebox.showwarning("Invalid AppID", "AppID should be numeric.", parent=dlg)
                return
            if any(g["appid"] == appid for g in self.games):
                messagebox.showwarning("Already exists", "A game with that AppID is already saved.", parent=dlg)
                return
            self.games.append({"name": name, "appid": appid})
            save_games(self.games)
            on_added()
            dlg.destroy()

        btns = tk.Frame(dlg)
        btns.pack(fill="x", padx=10, pady=10)
        tk.Button(btns, text="Add", command=do_add, bg="#2e7d32", fg="white").pack(side="left")
        tk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="left", padx=6)

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for entry in self.saved_links:
            label = f"{entry['id']}"
            if entry.get("title"):
                label += f"  -  {entry['title']}"
            self.listbox.insert(tk.END, label)

    def _persist_links(self):
        save_json(LINKS_FILE, self.saved_links)

    # -- List editing --------------------------------------------------------

    def _add_links(self):
        raw_text = self.input_box.get("1.0", tk.END)
        lines = [l for l in raw_text.splitlines() if l.strip()]
        if not lines:
            return

        ids_in_order = []
        for line in lines:
            mod_id = extract_id(line)
            if mod_id:
                ids_in_order.append(mod_id)
            else:
                self._log(f"Skipped (couldn't parse an id): {line}\n")

        if not ids_in_order:
            return

        if self.include_deps.get():
            self._log("Resolving dependencies, this may take a moment...\n")
            self._set_busy(True)
            threading.Thread(
                target=self._resolve_and_add_thread, args=(ids_in_order,), daemon=True
            ).start()
        else:
            self._add_ids_to_saved(ids_in_order, {})
            self.input_box.delete("1.0", tk.END)

    def _resolve_and_add_thread(self, ids_in_order):
        try:
            all_ids, titles = resolve_with_dependencies(ids_in_order, self._log)
        except Exception as e:
            self._log(f"ERROR resolving dependencies: {e}\n")
            all_ids, titles = ids_in_order, {}
        self.root.after(0, self._finish_add, all_ids, titles)

    def _finish_add(self, all_ids, titles):
        self._add_ids_to_saved(all_ids, titles)
        self.input_box.delete("1.0", tk.END)
        self._set_busy(False)
        self._log(f"Added {len(all_ids)} item(s) to the list.\n")

    def _add_ids_to_saved(self, ids, titles):
        existing_ids = {e["id"] for e in self.saved_links}
        for mod_id in ids:
            if mod_id in existing_ids:
                continue
            self.saved_links.append({"id": mod_id, "title": titles.get(mod_id, "")})
            existing_ids.add(mod_id)
        self._persist_links()
        self._refresh_list()

    def _remove_selected(self):
        selected = list(self.listbox.curselection())
        if not selected:
            return
        for index in reversed(selected):
            del self.saved_links[index]
        self._persist_links()
        self._refresh_list()

    def _clear_list(self):
        if not self.saved_links:
            return
        if messagebox.askyesno("Clear list", "Remove all saved mods from the list?"):
            self.saved_links = []
            self._persist_links()
            self._refresh_list()

    # -- Download ------------------------------------------------------------

    def _start_download(self):
        if not self.saved_links:
            messagebox.showinfo("Nothing to download", "Your mod list is empty.")
            return
        mod_ids = [e["id"] for e in self.saved_links]
        self._start_download_ids(mod_ids)

    def _start_download_selected(self):
        selected = list(self.listbox.curselection())
        if not selected:
            messagebox.showinfo(
                "Nothing selected",
                "Click one or more mods in the list first (Ctrl+click for multiple), "
                "then use Download Selected."
            )
            return
        mod_ids = [self.saved_links[i]["id"] for i in selected]
        self._start_download_ids(mod_ids)

    def _start_download_ids(self, mod_ids):
        if self.busy:
            return
        steamcmd_path = self.path_var.get().strip()
        if not steamcmd_path:
            messagebox.showwarning("SteamCMD path missing", "Set the SteamCMD path first.")
            return

        dest_dir = self.mods_path_var.get().strip()
        self._set_busy(True)
        self._log(f"\n=== Downloading {len(mod_ids)} mod(s) for {self._active_game_name()} "
                   f"(AppID {self.active_appid}) ===\n")
        threading.Thread(
            target=run_steamcmd,
            args=(steamcmd_path, mod_ids, dest_dir, self.active_appid, self._log, self._on_download_done),
            daemon=True,
        ).start()

    def _on_download_done(self):
        self.root.after(0, lambda: self._set_busy(False))

    # -- Small utilities -------------------------------------------------------

    def _set_busy(self, is_busy):
        self.busy = is_busy
        state = "disabled" if is_busy else "normal"
        self.download_btn.config(state=state)
        self.download_selected_btn.config(state=state)

    def _log(self, text):
        def append():
            self.log_box.config(state="normal")
            self.log_box.insert(tk.END, text)
            self.log_box.see(tk.END)
            self.log_box.config(state="disabled")
        # safe to call from worker threads
        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.root.after(0, append)


def main():
    root = tk.Tk()
    ModDownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
