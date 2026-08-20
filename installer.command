#!/bin/bash
# One-time setup on macOS. Double-click once, then use lancer-cardif.command.
cd "$(dirname "$0")" || exit 1

echo
echo "  Installation de Cardif"
echo "  ======================"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "  [X] Python n'est pas installé sur cet ordinateur."
    echo "      Téléchargez-le sur https://www.python.org/downloads/"
    read -r -p "  Appuyez sur Entrée pour fermer."
    exit 1
fi

echo "  Installation des composants nécessaires..."
echo "  (quelques minutes la première fois)"
echo

python3 -m pip install --upgrade pip --quiet
if python3 -m pip install -e . --quiet; then
    echo
    echo "  [OK] Installation terminée."
    echo
    echo "  Vous pouvez maintenant double-cliquer sur « lancer-cardif.command »."
else
    echo
    echo "  [X] L'installation a échoué. Prévenez la personne qui gère l'outil."
fi
echo
read -r -p "  Appuyez sur Entrée pour fermer."
