
import os
import platform
import shutil
import socket
import psutil
from datetime import datetime

def afficher_titre():
    print("=" * 45)
    print("          ANGEOS - DIAGNOSTIC")
    print("=" * 45)

def diagnostic_systeme():
    print("\n[ SYSTEME ]")
    print("Nom du PC :", socket.gethostname())
    print("Systeme :", platform.system())
    print("Version :", platform.version())
    print("Architecture :", platform.machine())
    print("Processeur :", platform.processor())

def diagnostic_memoire():
    print("\n[ MEMOIRE ]")
    memoire = psutil.virtual_memory()
    print("RAM totale :", round(memoire.total / (1024**3), 2), "Go")
    print("RAM utilisee :", round(memoire.used / (1024**3), 2), "Go")
    print("RAM disponible :", round(memoire.available / (1024**3), 2), "Go")

def diagnostic_disque():
    print("\n[ STOCKAGE ]")
    disque = shutil.disk_usage("/")
    print("Total :", round(disque.total / (1024**3), 2), "Go")
    print("Utilise :", round(disque.used / (1024**3), 2), "Go")
    print("Libre :", round(disque.free / (1024**3), 2), "Go")

def diagnostic_reseau():
    print("\n[ RESEAU ]")
    try:
        print("Nom reseau :", socket.gethostname())
        for interface, adresses in psutil.net_if_addrs().items():
            print("\nInterface :", interface)
            for adresse in adresses:
                if adresse.family == socket.AF_INET:
                    print("Adresse IPv4 :", adresse.address)
    except Exception as erreur:
        print("Erreur reseau :", erreur)

def lancer():
    afficher_titre()
    print("Date :", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    diagnostic_systeme()
    diagnostic_memoire()
    diagnostic_disque()
    diagnostic_reseau()
    print("\n" + "=" * 45)
    print("          DIAGNOSTIC TERMINE")
    print("=" * 45)

if __name__ == "__main__":
    lancer()
    input("\nAppuie sur Entree pour fermer...")
