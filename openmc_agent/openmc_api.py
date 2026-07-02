import importlib
import inspect
from dataclasses import dataclass
from typing import Any


KNOWN_OPENMC_SYMBOLS = (
    "openmc.Material",
    "openmc.Cell",
    "openmc.Universe",
    "openmc.Geometry",
    "openmc.Settings",
    "openmc.Plot",
    "openmc.RectLattice",
    "openmc.HexLattice",
    "openmc.Sphere",
    "openmc.ZCylinder",
    "openmc.XPlane",
    "openmc.YPlane",
    "openmc.ZPlane",
    "openmc.model.TRISO",
    "openmc.model.pack_spheres",
    "openmc.model.create_triso_lattice",
)

OPENMC_API_RELATIONSHIPS = (
    ("openmc.Cell", "fills", "openmc.Material"),
    ("openmc.Cell", "fills", "openmc.Universe"),
    ("openmc.Cell", "fills", "openmc.RectLattice"),
    ("openmc.Cell", "fills", "openmc.HexLattice"),
    ("openmc.Universe", "contains", "openmc.Cell"),
    ("openmc.Geometry", "uses_root", "openmc.Universe"),
    ("openmc.RectLattice", "contains", "openmc.Universe"),
    ("openmc.HexLattice", "contains", "openmc.Universe"),
    ("openmc.model.TRISO", "uses_fill", "openmc.Universe"),
    ("openmc.model.pack_spheres", "uses_container", "openmc.Region"),
    ("openmc.model.create_triso_lattice", "contains", "openmc.model.TRISO"),
    ("openmc.Sphere", "bounds", "openmc.model.TRISO"),
    ("openmc.ZCylinder", "bounds", "pin_cell_or_control_rod"),
)

OFFICIAL_DOC_URLS = {
    symbol: "https://docs.openmc.org/en/latest/pythonapi/generated/"
    + symbol
    + ".html"
    for symbol in KNOWN_OPENMC_SYMBOLS
}


@dataclass(frozen=True)
class OpenMCApiDoc:
    symbol: str
    signature: str
    doc_summary: str
    module: str
    official_url: str

    def model_dump(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "signature": self.signature,
            "doc_summary": self.doc_summary,
            "module": self.module,
            "official_url": self.official_url,
        }


def inspect_openmc_api(symbol: str) -> OpenMCApiDoc:
    """Return local OpenMC interface information for a public symbol."""
    normalized = _normalize_symbol(symbol)
    obj = _resolve_symbol(normalized)
    try:
        signature = str(inspect.signature(obj))
    except (TypeError, ValueError):
        signature = ""
    doc_summary = _doc_summary(inspect.getdoc(obj) or "")
    module = getattr(obj, "__module__", normalized.rsplit(".", 1)[0])
    return OpenMCApiDoc(
        symbol=normalized,
        signature=signature,
        doc_summary=doc_summary,
        module=module,
        official_url=OFFICIAL_DOC_URLS.get(normalized, _official_url_for_symbol(normalized)),
    )


def search_openmc_api(query: str, *, limit: int = 5) -> list[OpenMCApiDoc]:
    """Simple deterministic RAG over local OpenMC signatures and docstrings."""
    query_terms = _tokenize(query)
    scored: list[tuple[int, OpenMCApiDoc]] = []
    for symbol in KNOWN_OPENMC_SYMBOLS:
        try:
            doc = inspect_openmc_api(symbol)
        except Exception:
            continue
        haystack = _tokenize(
            " ".join(
                [
                    doc.symbol,
                    doc.signature,
                    doc.doc_summary,
                    _symbol_alias_text(doc.symbol),
                ]
            )
        )
        score = sum(1 for term in query_terms if term in haystack)
        if score:
            scored.append((score, doc))

    scored.sort(key=lambda item: (-item[0], item[1].symbol))
    return [doc for _, doc in scored[:limit]]


def explain_openmc_interface(symbol: str) -> dict[str, Any]:
    doc = inspect_openmc_api(symbol)
    return {
        **doc.model_dump(),
        "usage_note": _usage_note(doc.symbol),
    }


def retrieve_openmc_context(requirement: str, *, limit: int = 6) -> list[dict[str, str]]:
    docs = search_openmc_api(requirement, limit=limit)
    if docs:
        payload = [doc.model_dump() for doc in docs]
        edge_summary = _relationship_summary({doc.symbol for doc in docs})
        if edge_summary:
            payload.append(
                {
                    "symbol": "openmc_api_knowledge_graph",
                    "signature": "",
                    "doc_summary": edge_summary,
                    "module": "openmc_agent.openmc_api",
                    "official_url": "https://docs.openmc.org/en/latest/pythonapi/index.html",
                }
            )
        return payload
    fallback = [
        "openmc.Material",
        "openmc.Cell",
        "openmc.Universe",
        "openmc.Geometry",
        "openmc.Settings",
    ]
    return [inspect_openmc_api(symbol).model_dump() for symbol in fallback[:limit]]


def build_openmc_knowledge_graph() -> dict[str, list[dict[str, str]]]:
    nodes = []
    for symbol in KNOWN_OPENMC_SYMBOLS:
        try:
            nodes.append(inspect_openmc_api(symbol).model_dump())
        except Exception:
            nodes.append(
                {
                    "symbol": symbol,
                    "signature": "",
                    "doc_summary": "",
                    "module": symbol.rsplit(".", 1)[0],
                    "official_url": OFFICIAL_DOC_URLS.get(symbol, _official_url_for_symbol(symbol)),
                }
            )
    edges = [
        {"source": source, "relation": relation, "target": target}
        for source, relation, target in OPENMC_API_RELATIONSHIPS
    ]
    return {"nodes": nodes, "edges": edges}


def _resolve_symbol(symbol: str) -> Any:
    parts = symbol.split(".")
    if len(parts) < 2 or parts[0] != "openmc":
        raise ValueError(f"Expected an openmc symbol, got {symbol!r}")
    module = importlib.import_module(parts[0])
    obj: Any = module
    module_parts = [parts[0]]
    for part in parts[1:]:
        if not hasattr(obj, part):
            module_name = ".".join([*module_parts, part])
            try:
                obj = importlib.import_module(module_name)
                module_parts.append(part)
                continue
            except ModuleNotFoundError:
                raise AttributeError(f"OpenMC symbol not found: {symbol}") from None
        obj = getattr(obj, part)
        module_parts.append(part)
    return obj


def _normalize_symbol(symbol: str) -> str:
    text = symbol.strip()
    if not text:
        raise ValueError("symbol is required")
    if text.startswith("openmc."):
        return text
    return f"openmc.{text}"


def _doc_summary(doc: str, *, max_lines: int = 8, max_chars: int = 900) -> str:
    lines = [line.strip() for line in doc.splitlines() if line.strip()]
    summary = " ".join(lines[:max_lines])
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars].rstrip() + "...[truncated]"


def _tokenize(text: str) -> set[str]:
    normalized = "".join(char.lower() if char.isalnum() else " " for char in text)
    tokens = set(normalized.split())
    tokens.update(_domain_aliases(tokens))
    return tokens


def _domain_aliases(tokens: set[str]) -> set[str]:
    aliases: set[str] = set()
    if {"triso"} & tokens:
        aliases.update({"pack", "spheres", "lattice", "particle"})
    if {"组件", "assembly", "lattice", "栅格", "栅元"} & tokens:
        aliases.update({"rectlattice", "hexlattice", "universe"})
    if {"全堆", "堆芯", "core"} & tokens:
        aliases.update({"lattice", "geometry", "universe"})
    if {"球", "pebble", "sphere"} & tokens:
        aliases.update({"sphere", "pack", "spheres"})
    if {"控制棒", "control"} & tokens:
        aliases.update({"cell", "zcylinder", "material"})
    return aliases


def _symbol_alias_text(symbol: str) -> str:
    aliases = {
        "openmc.RectLattice": "assembly lattice rectangular 组件 栅格",
        "openmc.HexLattice": "assembly lattice hexagonal 六角 组件 栅格",
        "openmc.model.TRISO": "TRISO particle coated fuel 颗粒",
        "openmc.model.pack_spheres": "TRISO pebble sphere packing 球床 燃料球",
        "openmc.model.create_triso_lattice": "TRISO lattice acceleration",
        "openmc.Sphere": "sphere pebble fuel ball 球",
        "openmc.ZCylinder": "pin rod control rod cylinder 控制棒 燃料棒",
    }
    return aliases.get(symbol, "")


def _usage_note(symbol: str) -> str:
    notes = {
        "openmc.RectLattice": "Use lower_left, pitch, universes, and outer for rectangular assemblies.",
        "openmc.HexLattice": "Use center, pitch, rings/universes, and outer for hexagonal assemblies.",
        "openmc.model.TRISO": "Represent a TRISO particle with an outer radius, filled universe, and center.",
        "openmc.model.pack_spheres": "Generate non-overlapping sphere centers inside an OpenMC region.",
        "openmc.model.create_triso_lattice": "Convert explicit TRISO particles into an optimized lattice.",
    }
    return notes.get(symbol, "Use the local signature and docstring as the source of truth.")


def _relationship_summary(symbols: set[str]) -> str:
    edges = [
        f"{source} -[{relation}]-> {target}"
        for source, relation, target in OPENMC_API_RELATIONSHIPS
        if source in symbols or target in symbols
    ]
    return "; ".join(edges[:12])


def _official_url_for_symbol(symbol: str) -> str:
    return f"https://docs.openmc.org/en/latest/pythonapi/generated/{symbol}.html"
