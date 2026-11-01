
import os
import time
import psutil
import shutil
from datetime import datetime

def titre():
    os.system("clear")
    print("=" * 48)
    print("          ANGEOS - MONITEUR SYSTEME")
    print("=" * 48)

def afficher():
    while True:
        titre()

        processeur = psutil.cpu_percent(interval=1)
        memoire = psutil.virtual_memory()
        disque = shutil.disk_usage("/")

        print("\n[ PROCESSEUR ]")
        print(f"Utilisation : {processeur}%")

        print("\n[ MEMOIRE RAM ]")
        print(f"Utilisee : {memorie_utilisee(memoire)} Go")
        print(f"Totale   : {round(memoire.total / (1024**3), 2)} Go")
        print(f"Pourcentage : {memoire.percent}%")

        print("\n[ STOCKAGE ]")
        print(f"Utilise : {round(disque.used / (1024**3), 2)} Go")
        print(f"Libre   : {round(disque.free / (1024**3), 2)} Go")

        print("\n[ SYSTEME ]")
        print("Date :", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

        print("\nActualisation dans 2 secondes...")
        print("Appuie sur CTRL+C pour quitter.")

        time.sleep(2)

def memorie_utilisee(memoire):
    return round(memoire.used / (1024**3), 2)

if __name__ == "__main__":
    try:
        afficher()
    except KeyboardInterrupt:
        print("\nMoniteur ferme.")
