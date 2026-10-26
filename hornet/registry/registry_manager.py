"""Catalog is authoritative; SQLite is a rebuildable compatibility index.

No arbitrary database row is executable. New assets must be declared with an
input schema in the catalog, then reviewed and checked before restarting.
"""
import ast
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from .schema_validation import check_schema, validate

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = HERE

class RegistryManager:
    def __init__(self, db_path=None, catalog_path=None, root=None, allowed_uris=None):
        self.root = Path(root or DEFAULT_ROOT).expanduser().resolve()
        self.db_path = Path(db_path or os.environ.get("HORNET_REGISTRY_DB", Path.home() / ".hornet" / "registry" / "index.sqlite")).resolve()
        self.catalog_path = Path(catalog_path or os.environ.get("HORNET_REGISTRY_CATALOG", HERE / "asset_catalog.json")).resolve()
        document = json.loads(self.catalog_path.read_text(encoding="utf-8-sig"))
        if document.get("version") != 2:
            raise ValueError("Unsupported registry catalog version")
        self.assets = {}
        self.aliases = {}
        for entry in document["assets"]:
            name = entry["uri"]
            if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", name):
                raise ValueError(f"Invalid canonical URI: {name}")
            if name in self.assets or name in self.aliases:
                raise ValueError(f"Duplicate registry name: {name}")
            if entry["type"] not in ("TOOL", "RECIPE"):
                raise ValueError(f"Invalid asset type: {name}")
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Asset paths must be root-relative: {name}")
            resolved = (self.root / relative).resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError:
                raise ValueError(f"Asset escapes Hornet root: {name}")
            if entry["type"] == "TOOL":
                schema = entry["inputSchema"]
                if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                    raise ValueError(f"Tool schema must be a closed object: {name}")
                check_schema(schema)
                for example in entry.get("examples", []):
                    validate(example, schema)
            self.assets[name] = dict(entry, resolved_path=str(resolved))
            for alias in entry.get("aliases", []):
                if alias in self.assets or alias in self.aliases:
                    raise ValueError(f"Duplicate registry alias: {alias}")
                self.aliases[alias] = name
        if allowed_uris is not None:
            allowed = {self.aliases.get(name, name) for name in allowed_uris}
            unknown = allowed - self.assets.keys()
            if unknown:
                raise ValueError(f"Unknown allowlist URI(s): {sorted(unknown)}")
            self.assets = {name: asset for name, asset in self.assets.items() if name in allowed}
            self.aliases = {alias: name for alias, name in self.aliases.items() if name in allowed}

    def details(self, uri):
        name = self.aliases.get(uri, uri)
        if name not in self.assets:
            raise ValueError(f"Unknown URI {uri!r}; call find_asset first")
        return dict(self.assets[name], requested_uri=uri)

    def resolve(self, uri):
        try:
            asset = self.details(uri)
        except ValueError:
            return None
        return (asset["resolved_path"], asset["type"], asset["description"], asset["version"])

    def list_assets(self, asset_type=None):
        return [(a["uri"], a["resolved_path"], a["type"], a["description"], a["version"], None)
                for a in self.assets.values() if not asset_type or a["type"] == asset_type]

    def find(self, query, limit=10):
        # Capability labels are often typed with underscores, hyphens or dots.
        # Treat separators alike; method_body must find method/body operations.
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        ranked = []
        for asset in self.assets.values():
            haystack = " ".join([asset["uri"], asset["description"], *asset.get("aliases", [])]).lower()
            score = sum(word in haystack for word in words)
            if query.strip().lower() in [asset["uri"], *asset.get("aliases", [])]:
                score += len(words) + 1
            if not words or score:
                ranked.append((score, asset))
        ranked.sort(key=lambda row: (-row[0], row[1]["uri"]))
        return [{"uri": a["uri"], "type": a["type"], "description": a["description"],
                 "available": Path(a["resolved_path"]).is_file()}
                for _, a in ranked[:limit]]

    def doctor(self):
        issues = []
        for asset in self.assets.values():
            try:
                path = Path(asset["resolved_path"])
                source = path.read_text(encoding="utf-8-sig")
                if asset["type"] == "TOOL":
                    tree = ast.parse(source, str(path))
                    compile(source, str(path), "exec")
                    if not any(isinstance(n, ast.FunctionDef) and n.name == "execute" for n in tree.body):
                        raise ValueError("execute(arguments) is missing")
                elif path.suffix == ".json":
                    json.loads(source)
            except Exception as exc:
                issues.append({"uri": asset["uri"], "error": str(exc)})
        return {"status": "ERROR" if issues else "SUCCESS", "root": str(self.root),
                "catalog": str(self.catalog_path), "tools": sum(a["type"] == "TOOL" for a in self.assets.values()),
                "recipes": sum(a["type"] == "RECIPE" for a in self.assets.values()),
                "aliases": len(self.aliases), "issues": issues,
                "scope": "Catalog, schemas, paths and source syntax; not a behavioral test"}

    def sync_index(self):
        """Explicit, transactional index rebuild. Preserve original DB via sqlite backup."""
        report = self.doctor()
        if report["issues"]:
            raise ValueError(json.dumps(report["issues"]))
        if self.db_path.exists():
            backup = self.db_path.with_name(self.db_path.name + ".pre-v2.bak")
            if not backup.exists():
                with closing(sqlite3.connect(str(self.db_path))) as source, closing(sqlite3.connect(str(backup))) as destination:
                    source.backup(destination)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(self.db_path))) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS assets (uri TEXT PRIMARY KEY, path TEXT NOT NULL, type TEXT NOT NULL, description TEXT, version TEXT, last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, uri TEXT NOT NULL)")
            # Preserve unknown legacy rows for diagnosis, never expose them for execution.
            for alias, uri in self.aliases.items():
                conn.execute("DELETE FROM assets WHERE uri = ?", (alias,))
                conn.execute("INSERT OR REPLACE INTO aliases VALUES (?, ?)", (alias, uri))
            for a in self.assets.values():
                conn.execute("INSERT OR REPLACE INTO assets (uri,path,type,description,version) VALUES (?,?,?,?,?)",
                             (a["uri"], a["path"], a["type"], a["description"], a["version"]))
        return report
