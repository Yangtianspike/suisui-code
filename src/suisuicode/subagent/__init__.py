# subagent 包：Agent 角色定义、加载、Catalog

from suisuicode.subagent.definition import Definition, Source
from suisuicode.subagent.parser import parse_definition, parse_file
from suisuicode.subagent.catalog import Catalog, load_catalog
from suisuicode.subagent.embed import builtin_definitions

__all__ = [
    "Catalog",
    "Definition",
    "Source",
    "builtin_definitions",
    "load_catalog",
    "parse_definition",
    "parse_file",
]
