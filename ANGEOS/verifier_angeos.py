
from pathlib import Path

racine = Path(__file__).parent

fichiers = [
    "auto/config",
    "config/package-lists/angeos.list.chroot",
    "config/includes.chroot/opt/angeos/dashboard.py",
    "config/includes.chroot/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-desktop.xml",
    "config/includes.chroot/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xsettings.xml",
    "config/includes.chroot/etc/skel/.config/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml",
    "config/includes.chroot/etc/skel/.config/xfce4-terminal/terminalrc",
    "config/includes.chroot/etc/skel/.config/conky/conky.conf",
    "config/includes.chroot/etc/xdg/autostart/angeos-dashboard.desktop",
    "config/includes.chroot/etc/xdg/autostart/angeos-conky.desktop",
    "build.sh",
    "README_FR.txt",
]

print("=" * 40)
print("       VERIFICATION ANGEOS")
print("=" * 40)

erreurs = []

for nom in fichiers:
    chemin = racine / nom
    if chemin.is_file():
        print("[OK]       " + nom)
    else:
        print("[MANQUANT] " + nom)
        erreurs.append(nom)

print("=" * 40)

if erreurs:
    print("Des fichiers sont manquants :")
    for erreur in erreurs:
        print(" - " + erreur)
else:
    print("Tous les fichiers attendus sont presents.")

input("\nAppuie sur Entree pour fermer...")
