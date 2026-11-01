import os
import platform
import socket
import subprocess
import tkinter as tk

NOIR = "#000000"
FOND = "#101010"
PANNEAU = "#202020"
GRIS = "#AAAAAA"
BLANC = "#FFFFFF"
OUTILS = "/opt/angeos/tool"

class Dashboard:
    def __init__(self, root):
        self.root = root
        root.title("ANGEOS | Dashboard 2.0")
        root.geometry("1100x720")
        root.minsize(850, 600)
        root.configure(bg=NOIR)
        self.valeurs = {}
        self.interface()
        self.actualiser()
        root.after(3000, self.automatique)

    def texte(self, parent, contenu, taille=10, couleur=BLANC, gras=False):
        return tk.Label(parent, text=contenu, bg=parent.cget("bg"), fg=couleur,
                        font=("DejaVu Sans Mono", taille,
                              "bold" if gras else "normal"))

    def interface(self):
        haut = tk.Frame(self.root, bg=NOIR)
        haut.pack(fill="x", padx=22, pady=18)

        # Ange stylise sans tete, forme avec des caracteres binaires.
        ange = (
            "000011110000000000000000000011110000\n"
            "000111111100000000000000001111111000\n"
            "001111111110000000000000011111111100\n"
            "011111111111000000000000111111111110\n"
            "111111111111100000000001111111111111\n"
            "000000000000000000000000000000000000\n"
            "000000000000000011110000000000000000\n"
            "000000000000000111111000000000000000\n"
            "000000000000001111111100000000000000\n"
            "000000000000000111111000000000000000\n"
            "000000000000000011110000000000000000\n"
            "000000000000000111111000000000000000\n"
            "000000000000001100001100000000000000"
        )
        tk.Label(haut, text=ange, bg=NOIR, fg=BLANC,
                 font=("DejaVu Sans Mono", 8, "bold"),
                 justify="center").pack(side="left", padx=(0, 24))
        bloc = tk.Frame(haut, bg=NOIR)
        bloc.pack(side="left", pady=15)
        self.texte(bloc, "ANGEOS", 27, BLANC, True).pack(anchor="w")
        self.texte(bloc, "SYSTEM CONTROL CENTER", 10, GRIS).pack(anchor="w", pady=5)
        self.texte(bloc, "BINARY ANGEL  |  MONOCHROME EDITION", 8, GRIS).pack(anchor="w")

        stats = tk.Frame(self.root, bg=NOIR)
        stats.pack(fill="x", padx=20, pady=12)
        for titre, cle in [("CPU", "cpu"), ("RAM", "ram"),
                           ("PROCESSUS", "proc"), ("TEMPERATURE", "temp")]:
            carte = tk.Frame(stats, bg=PANNEAU, highlightbackground=GRIS,
                             highlightthickness=1)
            carte.pack(side="left", expand=True, fill="both", padx=5)
            self.texte(carte, titre, 9, GRIS, True).pack(anchor="w", padx=12, pady=(12, 4))
            valeur = self.texte(carte, "--", 16, BLANC, True)
            valeur.pack(anchor="w", padx=12, pady=(0, 12))
            self.valeurs[cle] = valeur

        corps = tk.Frame(self.root, bg=NOIR)
        corps.pack(fill="both", expand=True, padx=25, pady=12)
        gauche = tk.Frame(corps, bg=PANNEAU, highlightbackground=GRIS,
                          highlightthickness=1)
        gauche.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.texte(gauche, "ETAT DU SYSTEME", 11, BLANC, True).pack(
            anchor="w", padx=15, pady=15)
        self.infos = tk.Label(gauche, text="Chargement...", bg=PANNEAU,
                              fg=BLANC, font=("DejaVu Sans Mono", 10),
                              justify="left", anchor="nw")
        self.infos.pack(fill="both", expand=True, padx=15, pady=10)

        droite = tk.Frame(corps, bg=PANNEAU, width=330,
                          highlightbackground=GRIS, highlightthickness=1)
        droite.pack(side="right", fill="both", padx=(8, 0))
        droite.pack_propagate(False)
        self.texte(droite, "OUTILS DISPONIBLES", 11, BLANC, True).pack(
            anchor="w", padx=14, pady=15)
        self.liste = tk.Listbox(droite, bg=FOND, fg=BLANC,
                                selectbackground=GRIS, selectforeground=NOIR,
                                font=("DejaVu Sans Mono", 9), relief="flat")
        self.liste.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        bas = tk.Frame(self.root, bg=NOIR)
        bas.pack(fill="x", padx=25, pady=(0, 18))
        tk.Button(bas, text="ACTUALISER", command=self.actualiser,
                  bg=BLANC, fg=NOIR, activebackground=GRIS,
                  relief="flat", font=("DejaVu Sans Mono", 9, "bold"),
                  padx=18, pady=8).pack(side="left")
        self.texte(bas, "ANGEOS LIVE  |  NOIR & BLANC", 8, GRIS).pack(
            side="right", pady=10)

    def temperature(self):
        try:
            for nom in os.listdir("/sys/class/thermal"):
                chemin = f"/sys/class/thermal/{nom}/temp"
                if os.path.isfile(chemin):
                    with open(chemin, encoding="utf-8") as f:
                        t = int(f.read().strip()) / 1000
                    if 0 < t < 150:
                        return f"{t:.1f} C"
        except (OSError, ValueError):
            pass
        return "Non disponible"

    def charger_outils(self):
        self.liste.delete(0, tk.END)
        trouves = []
        if os.path.isdir(OUTILS):
            for racine, dossiers, fichiers in os.walk(OUTILS):
                dossiers[:] = [d for d in dossiers if d not in ("__pycache__", ".git", "archives")]
                for nom in fichiers:
                    if nom.endswith(".py"):
                        trouves.append(os.path.relpath(os.path.join(racine, nom), OUTILS))
        for nom in sorted(trouves):
            self.liste.insert(tk.END, nom)
        if not trouves:
            self.liste.insert(tk.END, "Aucun script trouve")

    def actualiser(self):
        try:
            import psutil
            self.valeurs["cpu"].config(text=f"{psutil.cpu_percent(interval=0.2):.0f} %")
            mem = psutil.virtual_memory()
            self.valeurs["ram"].config(text=f"{mem.percent:.0f} %")
            self.valeurs["proc"].config(text=str(len(psutil.pids())))
        except ImportError:
            for cle in ("cpu", "ram", "proc"):
                self.valeurs[cle].config(text="psutil absent")
        self.valeurs["temp"].config(text=self.temperature())
        try:
            ip = subprocess.run(["ip", "-brief", "address"], capture_output=True,
                                text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            ip = "Commande reseau indisponible"
        self.infos.config(text=(
            f"SYSTEME : {platform.system()} {platform.release()}\n"
            f"NOM PC  : {socket.gethostname()}\n"
            f"SESSION : ANGEOS LIVE\n\nRESEAU :\n{ip}\n\n"
            f"TEMPERATURE : {self.temperature()}\n"
            "ACTUALISATION AUTOMATIQUE : ACTIVE"
        ))
        self.charger_outils()

    def automatique(self):
        self.actualiser()
        self.root.after(3000, self.automatique)

if __name__ == "__main__":
    fenetre = tk.Tk()
    app = Dashboard(fenetre)
    fenetre.mainloop()
