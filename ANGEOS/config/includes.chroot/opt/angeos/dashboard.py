
import tkinter as tk
from tkinter import messagebox
import subprocess
import os

# ==================================
#             ANGEOS
#          TABLEAU DE BORD
# ==================================

NOIR = "#101010"
GRIS = "#202020"
BLANC = "#eeeeee"
BLEU = "#777777"

class AngeOS:
    def __init__(self, root):
        self.root = root
        self.root.title("ANGEOS - Dashboard")
        self.root.geometry("1000x650")
        self.root.configure(bg=NOIR)

        self.creer_interface()
        self.actualiser_infos()

    def creer_interface(self):
        titre = tk.Label(
            self.root,
            text="ANGEOS",
            font=("Arial", 28, "bold"),
            fg=BLANC,
            bg=NOIR
        )
        titre.pack(pady=15)

        sous_titre = tk.Label(
            self.root,
            text="SYSTEM CONTROL CENTER",
            font=("Arial", 10),
            fg=BLEU,
            bg=NOIR
        )
        sous_titre.pack()

        cadre = tk.Frame(self.root, bg=GRIS)
        cadre.pack(fill="x", padx=20, pady=20)

        self.infos = tk.Label(
            cadre,
            text="Chargement des informations...",
            font=("Consolas", 11),
            fg=BLANC,
            bg=GRIS,
            justify="left"
        )
        self.infos.pack(padx=15, pady=15, anchor="w")

        outils = tk.Frame(self.root, bg=NOIR)
        outils.pack(fill="both", expand=True, padx=20)

        tk.Label(
            outils,
            text="OUTILS DISPONIBLES",
            font=("Arial", 14, "bold"),
            fg=BLANC,
            bg=NOIR
        ).pack(anchor="w", pady=10)

        self.liste = tk.Listbox(
            outils,
            bg=GRIS,
            fg=BLANC,
            selectbackground=BLEU,
            font=("Consolas", 11),
            height=10
        )
        self.liste.pack(fill="both", expand=True)

        self.charger_outils()

        tk.Button(
            self.root,
            text="ACTUALISER",
            command=self.actualiser_infos,
            bg=GRIS,
            fg=BLANC,
            activebackground=BLEU
        ).pack(pady=8)

    def charger_outils(self):
        dossier = "/opt/angeos/tool"
        self.liste.delete(0, tk.END)

        if not os.path.isdir(dossier):
            self.liste.insert(tk.END, "Aucun outil installé")
            return

        fichiers = sorted(
            f for f in os.listdir(dossier)
            if f.endswith(".py")
        )

        for fichier in fichiers:
            self.liste.insert(tk.END, fichier)

        if not fichiers:
            self.liste.insert(tk.END, "Aucun outil installé")

    def actualiser_infos(self):
        try:
            systeme = subprocess.getoutput("uname -sr")
            reseau = subprocess.getoutput("hostname")
            adresse = subprocess.getoutput("ip -brief address")

            texte = (
                f"SYSTEME : {systeme}\n"
                f"NOM PC  : {reseau}\n\n"
                f"RESEAU :\n{adresse}"
            )

            self.infos.config(text=texte)

        except Exception as erreur:
            self.infos.config(text=f"Erreur : {erreur}")


if __name__ == "__main__":
    fenetre = tk.Tk()
    application = AngeOS(fenetre)
    fenetre.mainloop()
