"""KuzuDB schema definitions for PyGitNexus."""

# Node table schemas
NODE_TABLES = [
    """CREATE NODE TABLE File (
        id STRING,
        name STRING,
        filePath STRING,
        content STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Folder (
        id STRING,
        name STRING,
        filePath STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Class (
        id STRING,
        name STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        isPublic BOOLEAN,
        isAbstract BOOLEAN,
        isInterface BOOLEAN,
        content STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Interface (
        id STRING,
        name STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        isPublic BOOLEAN,
        content STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Method (
        id STRING,
        name STRING,
        className STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        returnType STRING,
        parameterCount INT64,
        isStatic BOOLEAN,
        isPublic BOOLEAN,
        isConstructor BOOLEAN,
        content STRING,
        httpMethod STRING,
        httpPath STRING,
        httpParams STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Field (
        id STRING,
        name STRING,
        typeName STRING,
        className STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        isStatic BOOLEAN,
        isPublic BOOLEAN,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Constructor (
        id STRING,
        name STRING,
        className STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        parameterCount INT64,
        isPublic BOOLEAN,
        content STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Variable (
        id STRING,
        name STRING,
        typeName STRING,
        methodName STRING,
        className STRING,
        filePath STRING,
        line INT64,
        isFinal BOOLEAN,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Annotation (
        id STRING,
        name STRING,
        targetType STRING,
        targetName STRING,
        filePath STRING,
        line INT64,
        attributes STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE TypeAlias (
        id STRING,
        name STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        content STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Enum (
        id STRING,
        name STRING,
        filePath STRING,
        startLine INT64,
        endLine INT64,
        isConst BOOLEAN,
        content STRING,
        PRIMARY KEY (id)
    )""",
]

# Relation types
REL_TYPES = [
    "CONTAINS", "DEFINES", "CALLS", "IMPORTS",
    "EXTENDS", "IMPLEMENTS", "HAS_METHOD", "HAS_PROPERTY",
    "HAS_CONSTRUCTOR", "HAS_VARIABLE", "HAS_ANNOTATION",
    "ACCESSES", "USES_ENDPOINT",
]

# Single CodeRelation table with type property
RELATION_SCHEMA = """
CREATE REL TABLE CodeRelation (
    FROM File TO File,
    FROM File TO Folder,
    FROM Folder TO Folder,
    FROM Folder TO File,
    FROM File TO Class,
    FROM File TO Interface,
    FROM File TO Method,
    FROM File TO Field,
    FROM File TO Constructor,
    FROM File TO Variable,
    FROM File TO Annotation,
    FROM File TO TypeAlias,
    FROM File TO Enum,
    FROM Class TO Method,
    FROM Class TO Field,
    FROM Class TO Constructor,
    FROM Class TO Annotation,
    FROM Interface TO Method,
    FROM Interface TO Field,
    FROM Interface TO Annotation,
    FROM Method TO Method,
    FROM Method TO Constructor,
    FROM Method TO Class,
    FROM Method TO Variable,
    FROM Method TO Annotation,
    FROM Method TO Field,
    FROM Constructor TO Method,
    FROM Constructor TO Constructor,
    FROM Constructor TO Variable,
    FROM Constructor TO Annotation,
    FROM Constructor TO Field,
    FROM Field TO Annotation,
    FROM Class TO Class,
    FROM Class TO Interface,
    FROM Interface TO Interface,
    type STRING,
    confidence DOUBLE,
    reason STRING,
    httpMethod STRING,
    httpPath STRING,
    httpParams STRING
)
"""

ALL_SCHEMA_QUERIES = NODE_TABLES + [RELATION_SCHEMA]
