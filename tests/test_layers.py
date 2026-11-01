"""The subpackages are layers (``lalein/__init__.py``): each imports only the ones above it."""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "src" / "lalein"
LAYERS = ["core", "voice", "assistant", "system", "ui", "setup"]


def imported_layers(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        for name in names:
            parts = name.split(".")
            if parts[0] == "lalein" and len(parts) > 1 and parts[1] in LAYERS:
                found.add(parts[1])
    return found


def test_every_subpackage_is_a_layer():
    assert sorted(p.name for p in PKG.iterdir() if (p / "__init__.py").is_file()) == sorted(LAYERS)


def test_each_layer_imports_only_the_ones_above_it():
    wrong = {}
    for i, layer in enumerate(LAYERS):
        for path in sorted((PKG / layer).glob("*.py")):
            below = imported_layers(path) - set(LAYERS[: i + 1])
            if below:
                wrong[f"{layer}/{path.name}"] = sorted(below)
    assert wrong == {}
