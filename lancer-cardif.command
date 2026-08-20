#!/bin/bash
# Double-click to start Cardif on macOS. Closing the window stops the tool.
cd "$(dirname "$0")" || exit 1

echo
echo "  Cardif démarre..."
echo
echo "  Une page va s'ouvrir dans votre navigateur."
echo "  NE FERMEZ PAS cette fenêtre tant que vous utilisez l'outil."
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "  [X] Python n'est pas installé sur cet ordinateur."
    echo "      Téléchargez-le sur https://www.python.org/downloads/"
    read -r -p "  Appuyez sur Entrée pour fermer."
    exit 1
fi

python3 -m streamlit run app/Accueil.py || {
    echo
    echo "  [X] Cardif n'a pas pu démarrer. Lancez d'abord installer.command."
    read -r -p "  Appuyez sur Entrée pour fermer."
}
