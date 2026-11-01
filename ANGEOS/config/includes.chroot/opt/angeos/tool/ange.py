#!/usr/bin/env python3
"""Lanceur ANGEOS : catalogue CLI et interface graphique des scripts locaux."""
from __future__ import annotations
import argparse
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
IGNORED_DIRS = {"__pycache__", "archives", ".git", ".venv", "venv", "node_modules"}


def scan_tools():
    tools = []
    for current, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS and not d.startswith("."))
        for filename in sorted(files):
            if not filename.endswith(".py") or filename == Path(__file__).name:
                continue
            path = (Path(current) / filename).resolve()
            try:
                path.relative_to(ROOT)
            except ValueError:
                continue
            rel = path.relative_to(ROOT)
            # A script is treated as graphical if it imports tkinter.
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
                graphical = any(
                    (isinstance(n, ast.Import) and any(a.name == "tkinter" or a.name.startswith("tkinter.") for a in n.names))
                    or (isinstance(n, ast.ImportFrom) and (n.module == "tkinter" or (n.module or "").startswith("tkinter.")))
                    for n in ast.walk(tree)
                )
                syntax_ok = True
            except (OSError, SyntaxError, UnicodeError):
                graphical, syntax_ok = False, False
            tools.append({"name": path.stem, "path": path, "relative": rel.as_posix(),
                          "category": rel.parent.as_posix() if rel.parent != Path('.') else "general",
                          "graphical": graphical, "syntax_ok": syntax_ok})
    return sorted(tools, key=lambda x: (x["category"].lower(), x["name"].lower()))


def find_tool(query, tools):
    q = query.strip().lower().removesuffix(".py")
    exact = [t for t in tools if t["name"].lower() == q or t["relative"].lower() == q]
    if len(exact) == 1:
        return exact[0]
    partial = [t for t in tools if q in t["name"].lower() or q in t["relative"].lower()]
    if len(partial) == 1:
        return partial[0]
    if len(exact) > 1 or len(partial) > 1:
        print("Plusieurs outils correspondent :")
        for t in (exact or partial):
            print(" -", t["relative"])
        return None
    print(f"Outil introuvable : {query}")
    return None


def launch(tool, graphical_terminal=False):
    if not tool["syntax_ok"]:
        print("Refus : le script contient une erreur de syntaxe ou ne peut pas être lu.")
        return 2
    print(f"Outil : {tool['relative']}")
    print("Vérifie que tu connais et fais confiance à ce script avant de l'exécuter.")
    answer = input("Lancer ce script ? [o/N] ").strip().lower()
    if answer not in ("o", "oui", "y", "yes"):
        print("Lancement annulé.")
        return 0
    if tool["graphical"] and not graphical_terminal:
        try:
            subprocess.Popen([sys.executable, str(tool["path"]),], cwd=str(tool["path"].parent))
            return 0
        except OSError as e:
            print("Impossible de démarrer l'interface :", e)
            return 1
    terminal = shutil.which("x-terminal-emulator")
    if terminal and not graphical_terminal:
        try:
            subprocess.Popen([terminal, "-e", sys.executable, str(tool["path"])], cwd=str(tool["path"].parent))
            return 0
        except OSError:
            pass
    return subprocess.call([sys.executable, str(tool["path"])], cwd=str(tool["path"].parent))


def show_list(tools, category=None):
    chosen = [t for t in tools if category is None or category.lower() in t["category"].lower()]
    if not chosen:
        print("Aucun outil trouvé.")
        return
    print("\n=== ANGEOS : OUTILS DISPONIBLES ===")
    for t in chosen:
        status = "OK" if t["syntax_ok"] else "ERREUR SYNTAXE"
        mode = "GUI" if t["graphical"] else "Terminal"
        print(f"[{status}] [{mode}] {t['relative']}")
    print(f"\n{len(chosen)} outil(s).")


def open_gui(tools):
    try:
        import tkinter as tk
        from tkinter import messagebox
    except ImportError:
        print("Tkinter n'est pas installé. Installe le paquet Debian python3-tk.")
        return 1
    root = tk.Tk()
    root.title("ANGEOS — Centre des outils")
    root.geometry("820x540")
    root.configure(bg="#101010")
    fg, panel, accent = "#eeeeee", "#202020", "#8fbcbb"
    selected = {"tool": None}
    tk.Label(root, text="ANGEOS", bg="#101010", fg=fg, font=("DejaVu Sans", 24, "bold")).pack(pady=(14, 0))
    tk.Label(root, text="CENTRE DE CONTRÔLE DES OUTILS", bg="#101010", fg="#aaaaaa", font=("DejaVu Sans Mono", 10)).pack(pady=(0, 12))
    body = tk.Frame(root, bg="#101010")
    body.pack(fill="both", expand=True, padx=18, pady=8)
    left = tk.Frame(body, bg=panel, width=240)
    left.pack(side="left", fill="y", padx=(0, 12))
    right = tk.Frame(body, bg=panel)
    right.pack(side="left", fill="both", expand=True)
    tk.Label(left, text="CATÉGORIES", bg=panel, fg=accent, font=("DejaVu Sans", 10, "bold")).pack(anchor="w", padx=10, pady=10)
    categories = sorted({t["category"] for t in tools})
    cat_list = tk.Listbox(left, bg=panel, fg=fg, selectbackground="#40545a", relief="flat", font=("DejaVu Sans Mono", 10), exportselection=False)
    cat_list.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    cat_list.insert(tk.END, "Tous les outils")
    for c in categories: cat_list.insert(tk.END, c)
    tk.Label(right, text="OUTILS", bg=panel, fg=accent, font=("DejaVu Sans", 10, "bold")).pack(anchor="w", padx=12, pady=10)
    listing = tk.Listbox(right, bg="#151515", fg=fg, selectbackground="#40545a", relief="flat", font=("DejaVu Sans Mono", 10), exportselection=False)
    listing.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    details = tk.Label(right, text="Sélectionne un outil pour voir ses détails.", bg=panel, fg="#bbbbbb", anchor="w", justify="left")
    details.pack(fill="x", padx=12, pady=5)
    def refresh(_event=None):
        listing.delete(0, tk.END); selected["tool"] = None
        idx = cat_list.curselection()
        cat = cat_list.get(idx[0]) if idx else "Tous les outils"
        subset = tools if cat == "Tous les outils" else [t for t in tools if t["category"] == cat]
        for t in subset: listing.insert(tk.END, f"{'[GUI]' if t['graphical'] else '[CLI]'} {t['name']} — {t['relative']}")
        listing.tools = subset
        details.config(text=f"{len(subset)} outil(s) affiché(s).")
    def choose(_event=None):
        idx = listing.curselection()
        if not idx: return
        t = listing.tools[idx[0]]; selected["tool"] = t
        details.config(text=f"Fichier : {t['relative']}\nMode : {'Graphique' if t['graphical'] else 'Terminal'} | Syntaxe : {'OK' if t['syntax_ok'] else 'à vérifier'}")
    def run_selected():
        t = selected["tool"]
        if not t: messagebox.showinfo("ANGEOS", "Sélectionne d'abord un outil."); return
        if not t["syntax_ok"]: messagebox.showerror("ANGEOS", "Ce script présente une erreur de syntaxe."); return
        if not messagebox.askyesno("Confirmation", f"Lancer {t['relative']} ?\n\nNe lance que les scripts que tu connais et auxquels tu fais confiance."):
            return
        try:
            if t["graphical"]:
                subprocess.Popen([sys.executable, str(t["path"])], cwd=str(t["path"].parent))
            else:
                term = shutil.which("x-terminal-emulator")
                if term: subprocess.Popen([term, "-e", sys.executable, str(t["path"])], cwd=str(t["path"].parent))
                else: messagebox.showwarning("Terminal absent", "x-terminal-emulator est introuvable.")
        except OSError as e: messagebox.showerror("Erreur", str(e))
    cat_list.bind("<<ListboxSelect>>", refresh); listing.bind("<<ListboxSelect>>", choose); listing.bind("<Double-Button-1>", lambda _e: run_selected())
    cat_list.selection_set(0); refresh()
    tk.Button(right, text="LANCER L'OUTIL", command=run_selected, bg="#303030", fg=fg, activebackground="#40545a", activeforeground=fg, relief="flat", padx=18, pady=9).pack(pady=12)
    tk.Label(root, text="Les archives et dossiers techniques sont ignorés. Vérifie toujours un script avant de le lancer.", bg="#101010", fg="#888888", font=("DejaVu Sans", 8)).pack(pady=(0, 10))
    root.mainloop()
    return 0


def main():
    parser = argparse.ArgumentParser(prog="ange", description="Lanceur des outils ANGEOS")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("liste", help="Afficher les outils disponibles").add_argument("categorie", nargs="?")
    runp = sub.add_parser("lancer", help="Lancer un outil après confirmation")
    runp.add_argument("nom", help="Nom ou chemin relatif du script, sans .py facultatif")
    sub.add_parser("gui", help="Ouvrir l'interface graphique")
    args = parser.parse_args()
    tools = scan_tools()
    if args.command == "liste": show_list(tools, args.categorie)
    elif args.command == "lancer":
        tool = find_tool(args.nom, tools)
        return launch(tool) if tool else 1
    elif args.command == "gui": return open_gui(tools)
    else: parser.print_help()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
