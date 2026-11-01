
#!/bin/bash
set -e

echo "=================================="
echo "          ANGEOS BUILDER"
echo "=================================="

if [ "$EUID" -ne 0 ]; then
    echo "Erreur : lance ce script avec sudo."
    exit 1
fi

if ! command -v lb >/dev/null 2>&1; then
    echo "Erreur : live-build n'est pas installe."
    exit 1
fi

echo "[1/2] Configuration de ANGEOS..."
lb config

echo "[2/2] Construction de l'ISO..."
lb build

echo "=================================="
echo "       CONSTRUCTION TERMINEE"
echo "=================================="
