
import subprocess
import shutil
import ipaddress

def titre():
    print("=" * 45)
    print("        ANGEOS - SCANNER RESEAU")
    print("=" * 45)

def obtenir_reseau():
    try:
        resultat = subprocess.run(
            ["ip", "-o", "-4", "addr", "show", "up"],
            capture_output=True,
            text=True,
            timeout=5
        )

        for ligne in resultat.stdout.splitlines():
            morceaux = ligne.split()
            for element in morceaux:
                if "/" in element:
                    try:
                        reseau = ipaddress.ip_interface(element)
                        if not reseau.ip.is_loopback:
                            return reseau.network
                    except ValueError:
                        continue

    except (subprocess.SubprocessError, OSError):
        pass

    return None

def scanner():
    reseau = obtenir_reseau()

    if reseau is None:
        print("\n[ERREUR] Aucun reseau IPv4 detecte.")
        return

    print("\nReseau local detecte :", reseau)
    print("Verification de la passerelle et de cette machine...")

    try:
        resultat = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5
        )
        print("\n[ PASSERELLE ]")
        print(resultat.stdout.strip() or "Non detectee")
    except (subprocess.SubprocessError, OSError):
        print("Passerelle indisponible.")

    print("\n[ APPAREILS CONNUS PAR LE PC ]")

    if shutil.which("ip"):
        try:
            resultat = subprocess.run(
                ["ip", "neigh", "show"],
                capture_output=True,
                text=True,
                timeout=5
            )
            print(resultat.stdout.strip() or "Aucun appareil connu.")
        except (subprocess.SubprocessError, OSError):
            print("Impossible de lire la table reseau.")

    print("\nAnalyse terminee.")
    print("Note : seuls les appareils deja connus du PC sont affiches.")

if __name__ == "__main__":
    titre()
    scanner()
    input("\nAppuie sur Entree pour fermer...")
