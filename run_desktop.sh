#!/bin/bash
# ElectroGalindez - Iniciar App de Escritorio
# ============================================

echo "====================================="
echo "  ElectroGalindez - App de Escritorio"
echo "====================================="
echo ""

# Verificar Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python3 no encontrado"
    exit 1
fi

# Verificar dependencias
echo "Verificando dependencias..."
pip3 install -q flask pywebview 2>/dev/null

# Verificar PostgreSQL
if ! pg_isready -q 2>/dev/null; then
    echo "Advertencia: PostgreSQL no parece estar corriendo"
    echo "Intenta: brew services start postgresql"
fi

echo ""
echo "Iniciando aplicación..."
echo ""

# Ejecutar app de escritorio
python3 desktop.py
