"""KuzuDB schema definitions for TestNexus."""

NODE_TABLES = [
    """CREATE NODE TABLE Page (
        id STRING,
        name STRING,
        filePath STRING,
        url STRING,
        platform STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Component (
        id STRING,
        name STRING,
        filePath STRING,
        type STRING,
        platform STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Action (
        id STRING,
        name STRING,
        type STRING,
        selector STRING,
        componentName STRING,
        httpMethod STRING,
        apiPath STRING,
        lineNumber INT64,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Endpoint (
        id STRING,
        method STRING,
        path STRING,
        controllerName STRING,
        functionName STRING,
        parameters STRING,
        responseType STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE TestCase (
        id STRING,
        name STRING,
        type STRING,
        filePath STRING,
        targetId STRING,
        status STRING,
        actionType STRING,
        actionDef STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE TestRecord (
        id STRING,
        testCaseId STRING,
        request STRING,
        response STRING,
        statusCode INT64,
        timestamp STRING,
        baselineId STRING,
        duration INT64,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Module (
        id STRING,
        name STRING,
        description STRING,
        source STRING,
        pageCount INT64,
        componentCount INT64,
        actionCount INT64,
        endpointCount INT64,
        apiCount INT64,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE Baseline (
        id STRING,
        name STRING,
        createdAt STRING,
        testCount INT64,
        source STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE LLMAnalysis (
        id STRING,
        analysisType STRING,
        modelName STRING,
        promptHash STRING,
        rawOutput STRING,
        createdAt STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE DataSource (
        id STRING,
        sourceType STRING,
        name STRING,
        filePath STRING,
        description STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE ReplayComparison (
        id STRING,
        baseline STRING,
        compare STRING,
        passCount INT64,
        diffCount INT64,
        failCount INT64,
        newCount INT64,
        removedCount INT64,
        createdAt STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE ReplayDetail (
        id STRING,
        testCaseId STRING,
        status STRING,
        message STRING,
        PRIMARY KEY (id)
    )""",
    """CREATE NODE TABLE ApiParam (
        id STRING,
        name STRING,
        paramType STRING,
        valueType STRING,
        required BOOL,
        sampleValue STRING,
        apiMethod STRING,
        apiPath STRING,
        PRIMARY KEY (id)
    )""",
]

RELATION_SCHEMA = """
CREATE REL TABLE TestRelation (
    FROM Page TO Component,
    FROM Component TO Component,
    FROM Component TO Action,
    FROM Action TO Endpoint,
    FROM Endpoint TO TestCase,
    FROM TestCase TO Action,
    FROM TestCase TO TestRecord,
    FROM TestRecord TO Baseline,
    FROM Endpoint TO Endpoint,
    FROM Module TO Page,
    FROM Module TO Component,
    FROM Module TO Endpoint,
    FROM Module TO Module,
    FROM LLMAnalysis TO Module,
    FROM LLMAnalysis TO DataSource,
    FROM LLMAnalysis TO Endpoint,
    FROM DataSource TO Module,
    FROM DataSource TO Endpoint,
    FROM Action TO ApiParam,
    type STRING,
    confidence DOUBLE,
    reason STRING
)
"""

ALL_SCHEMA_QUERIES = NODE_TABLES + [RELATION_SCHEMA]
