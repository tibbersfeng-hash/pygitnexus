"""Core data models for PyGitNexus."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JavaFile:
    """A Java source file ready for parsing."""
    path: Path
    relative: str  # relative to project root
    content: bytes

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


@dataclass
class ParamDef:
    """A method parameter."""
    name: str
    type_name: str
    raw_type: str = ""  # full type with generics, e.g. "List<User>"


@dataclass
class ClassDef:
    """A Java class or interface definition."""
    name: str  # fully qualified name
    file_path: str
    start_line: int
    end_line: int
    is_public: bool
    is_abstract: bool
    is_interface: bool
    extends: str | None
    implements: list[str] = field(default_factory=list)
    content: str = ""


@dataclass
class MethodDef:
    """A Java method definition."""
    name: str
    class_name: str | None  # None for top-level (doesn't exist in Java)
    file_path: str
    start_line: int
    end_line: int
    return_type: str
    parameters: list[ParamDef] = field(default_factory=list)
    is_static: bool = False
    is_public: bool = True
    is_constructor: bool = False
    content: str = ""


@dataclass
class FieldDef:
    """A Java field definition."""
    name: str
    type_name: str
    class_name: str
    file_path: str
    start_line: int = 0
    end_line: int = 0
    is_static: bool = False
    is_public: bool = True


@dataclass
class CallSite:
    """A method invocation site."""
    caller_method: str  # fully qualified: ClassName.methodName
    caller_class: str = ""  # class containing the call
    target_name: str = ""  # invoked method name
    line: int = 0
    receiver: str | None = None  # object/class name or None for static/free
    receiver_type: str | None = None  # resolved type of receiver


@dataclass
class ImportDecl:
    """A Java import statement."""
    qualified_name: str  # e.g. "com.example.Foo"
    is_wildcard: bool
    file_path: str


@dataclass
class VariableDef:
    """A local variable declaration within a method body."""
    name: str
    type_name: str
    method_name: str  # enclosing method
    class_name: str  # enclosing class
    file_path: str
    line: int = 0
    is_final: bool = False


@dataclass
class ConstructorDef:
    """A Java constructor definition (separate from methods)."""
    name: str
    class_name: str
    file_path: str
    start_line: int
    end_line: int
    parameters: list[ParamDef] = field(default_factory=list)
    is_public: bool = True
    content: str = ""


@dataclass
class FieldAccess:
    """A field read or write access within a method body."""
    field_name: str
    method_name: str  # enclosing method (FQN: ClassName.methodName)
    class_name: str  # enclosing class
    file_path: str
    line: int = 0
    is_write: bool = False  # True for assignment, False for read


@dataclass
class AnnotationDef:
    """A Java annotation on a class, method, field, or parameter."""
    name: str  # e.g. "Override", "Autowired"
    target_type: str  # "class", "method", "field", "constructor", "parameter"
    target_name: str  # name of the annotated element
    file_path: str
    line: int = 0
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedFile:
    """Complete parse result for a single Java file."""
    file_path: str  # relative path
    classes: list[ClassDef] = field(default_factory=list)
    methods: list[MethodDef] = field(default_factory=list)
    constructors: list[ConstructorDef] = field(default_factory=list)
    fields: list[FieldDef] = field(default_factory=list)
    variables: list[VariableDef] = field(default_factory=list)
    annotations: list[AnnotationDef] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    field_accesses: list[FieldAccess] = field(default_factory=list)
    imports: list[ImportDecl] = field(default_factory=list)
