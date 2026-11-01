
import socket
from datetime import datetime

def titre():
    print("=" * 45)
    print("          ANGEOS - ANALYSEUR DNS")
    print("=" * 45)

def analyser_domaine(domaine):
    print(f"\n[ ANALYSE ] Domaine : {domaine}")

    try:
        resultat = socket.getaddrinfo(domaine, None)

        adresses = sorted({
            element[4][0]
            for element in resultat
        })

        print("\nAdresses IP trouvees :")

        for adresse in adresses:
            print(" -", adresse)

        print("\nNombre d'adresses :", len(adresses))

    except socket.gaierror:
        print("\n[ERREUR] Impossible de resoudre ce domaine.")

def lancer():
    titre()
    print("Date :", datetime.now().strftime("%d/%m/%Y %H:%M:%S"))

    domaine = input("\nEntre un domaine (exemple : example.com) : ").strip()

    if not domaine:
        print("Aucun domaine indique.")
    else:
        analyser_domaine(domaine)

    print("\n" + "=" * 45)
    print("          ANALYSE TERMINEE")
    print("=" * 45)

if __name__ == "__main__":
    lancer()
    input("\nAppuie sur Entree pour fermer...")
