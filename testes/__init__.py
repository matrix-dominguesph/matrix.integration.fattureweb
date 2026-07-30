"""Testes do pipeline.

Sem framework de teste (o repo não tem um): cada módulo é um script que imprime o
resultado de cada verificação e sai com código 1 se alguma falhar — o que serve tanto para
rodar na mão quanto em CI.

  python -m testes.test_unitario     # offline, não precisa de .env nem de rede
  python -m testes.test_integracao   # bate na API real, precisa do .env

Ver ``testes/README.md``.
"""

from __future__ import annotations

import sys


class Checador:
    """Acumula o resultado das verificações e devolve o código de saída."""

    def __init__(self, titulo: str) -> None:
        print(f"### {titulo}")
        self.falhas: list[str] = []

    def secao(self, titulo: str) -> None:
        print(f"\n=== {titulo} ===")

    def checa(self, rotulo: str, condicao: bool, extra: str = "") -> bool:
        marca = "OK " if condicao else "FALHA"
        print(f"  [{marca}] {rotulo}{(' :: ' + extra) if extra else ''}")
        if not condicao:
            self.falhas.append(rotulo)
        return bool(condicao)

    def encerrar(self) -> None:
        print("\n" + "=" * 62)
        if self.falhas:
            print(f"FALHAS ({len(self.falhas)}):")
            for f in self.falhas:
                print(f"  - {f}")
            sys.exit(1)
        print("Todos os testes passaram.")
        sys.exit(0)
