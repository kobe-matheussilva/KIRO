#!/usr/bin/env bash

# Este script depende de recursos do bash. Se for source no zsh,
# aborta de forma segura para nao poluir as opcoes do shell atual.
if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "[error] Este script precisa rodar em bash."
  echo "[hint] No zsh, use: bash kiro/setup_env.sh && source .venv/bin/activate"
  return 1 2>/dev/null || exit 1
fi

__SETUP_WAS_SOURCED=0
__SETUP_OLD_SET_OPTS=""
if [[ "${BASH_SOURCE[0]-}" != "$0" ]]; then
  __SETUP_WAS_SOURCED=1
  __SETUP_OLD_SET_OPTS="$(set +o)"
fi

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
REQUIREMENTS_FILE="${PROJECT_ROOT}/requirements.txt"
ENV_EXAMPLE_FILE="${PROJECT_ROOT}/.env.example"
ENV_FILE="${PROJECT_ROOT}/.env"

# Uso recomendado (para manter o venv ativo no terminal atual):
# source setup_env.sh
# Modo estrito opcional: STRICT_SETUP=1 source setup_env.sh

is_sourced() {
  [[ "${__SETUP_WAS_SOURCED}" == "1" ]]
}

finish() {
  local code="${1:-0}"
  if is_sourced; then
    if [[ -n "${__SETUP_OLD_SET_OPTS}" ]]; then
      eval "${__SETUP_OLD_SET_OPTS}"
    fi
    return "$code"
  fi
  exit "$code"
}

warn_non_strict() {
  local msg="$1"
  echo "[warn] $msg"
  if [[ "${STRICT_SETUP:-0}" == "1" ]]; then
    finish 1
  fi
}

pick_python_bin() {
  # Prioriza PYTHON_BIN se o usuário quiser forçar um interpretador.
  if [[ -n "${PYTHON_BIN:-}" ]] && command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "$PYTHON_BIN"
    return 0
  fi

  # Ordem genérica e portátil.
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      # Garante que o módulo venv exista neste interpretador.
      if "$candidate" -m venv --help >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done

  return 1
}

PY_BIN="$(pick_python_bin || true)"
if [[ -z "$PY_BIN" ]]; then
  echo "[error] Nenhum Python com suporte a venv foi encontrado (python3/python)."
  echo "[hint] Instale Python com venv habilitado e rode novamente."
  warn_non_strict "Setup finalizado sem venv (modo não estrito)."
  finish 0
fi

configure_macos_expat_workaround() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi

  local hb_expat_lib="/opt/homebrew/opt/expat/lib"
  if [[ ! -d "${hb_expat_lib}" ]]; then
    return 0
  fi

  if "$PY_BIN" -c "import pyexpat" >/dev/null 2>&1; then
    return 0
  fi

  export DYLD_LIBRARY_PATH="${hb_expat_lib}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
  if "$PY_BIN" -c "import pyexpat" >/dev/null 2>&1; then
    echo "[info] Aplicado workaround de expat (DYLD_LIBRARY_PATH) para Python/Homebrew no macOS."
  else
    warn_non_strict "Python não conseguiu importar pyexpat mesmo após workaround de expat."
  fi
}

configure_macos_expat_workaround

echo "[info] Python selecionado: $PY_BIN ($($PY_BIN --version 2>&1))"

# 1) cria o venv (só na primeira vez)
# Se a pasta existir mas estiver corrompida/incompleta, recria.
if [[ ! -f "${VENV_DIR}/bin/activate" ]]; then
  if [[ -d "${VENV_DIR}" ]]; then
    echo "[warn] ${VENV_DIR} existe, mas está incompleto. Recriando..."
    rm -rf "${VENV_DIR}"
  fi
  if ! "$PY_BIN" -m venv "${VENV_DIR}"; then
    echo "[warn] Falha ao criar .venv com '$PY_BIN -m venv'. Tentando sem pip..."
    if ! "$PY_BIN" -m venv --without-pip "${VENV_DIR}"; then
      echo "[error] Não foi possível criar .venv com '$PY_BIN'."
      echo "[hint] Verifique se o Python tem venv/ensurepip funcionando."
      warn_non_strict "Setup finalizado sem venv (modo não estrito)."
      finish 0
    fi
    echo "[warn] .venv criado sem pip. Tentando bootstrap do pip..."
    if ! "${VENV_DIR}/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
      warn_non_strict "pip não pôde ser instalado automaticamente no venv."
    fi
  fi
  echo "[ok] .venv criado"
else
  echo "[info] .venv já existe e está íntegro"
fi

# 2) ativa o venv — IMPRESCINDIVEL toda vez que abrir terminal novo
# Observação: para o ambiente permanecer ativo no terminal atual,
# rode este script com: source setup_env.sh
# shellcheck disable=SC1091
if [[ -f "${VENV_DIR}/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${VENV_DIR}/bin/activate"
else
  echo "[error] Arquivo de ativação não encontrado: ${VENV_DIR}/bin/activate"
  warn_non_strict "Setup finalizado sem ativar venv (modo não estrito)."
  finish 0
fi

echo "[ok] venv ativo: ${VIRTUAL_ENV}"

# 3) instala dependencias (só na primeira vez)
if [[ -f "${REQUIREMENTS_FILE}" ]]; then
  if command -v pip >/dev/null 2>&1; then
    if pip install -r "${REQUIREMENTS_FILE}"; then
      echo "[ok] dependencias instaladas"
    else
      warn_non_strict "Falha ao instalar dependências de ${REQUIREMENTS_FILE}."
    fi
  else
    warn_non_strict "pip indisponível no ambiente atual."
  fi
else
  echo "[warn] ${REQUIREMENTS_FILE} não encontrado. Pulei a instalação."
fi

# 4) configura credenciais
if [[ -f "${ENV_EXAMPLE_FILE}" ]]; then
  if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${ENV_EXAMPLE_FILE}" "${ENV_FILE}"
    echo "[ok] .env criado a partir de .env.example"
  else
    echo "[info] .env já existe"
  fi
else
  echo "[warn] .env.example não encontrado. Crie .env manualmente."
fi

echo "[next] Edite o .env com seus valores."
finish 0
