// Optional Neo4j deployment of the legal knowledge graph (same vocabulary as kg/graph.py).
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Module)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Route)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Fact)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:LegalSource) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Block)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Evidence)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:Question)    REQUIRE n.id IS UNIQUE;

// Q1  Which facts are still missing to unlock the hinted routes?  (Question engine)
// MATCH (r:Route)<-[:BELONGS_TO]-(m:Module)-[:GATED_BY|REQUIRES]->(f:Fact)-[:ASKED_BY]->(q:Question)
// WHERE r.id IN $hinted_routes AND NOT f.id IN $known_facts
// RETURN f.id, q.id, sum(m.strength) AS value ORDER BY value DESC LIMIT 6;

// Q2  Conflicts inside a candidate set  (Reasoning engine)
// MATCH (a:Module)-[:CONFLICTS_WITH]->(b:Module) WHERE a.id IN $ids AND b.id IN $ids RETURN a.id, b.id;

// Q3  Legal sources behind a draft  (Validation engine)
// MATCH (m:Module)-[:CITES]->(s:LegalSource) WHERE m.id IN $module_ids RETURN m.id, collect(s.id);

// Q4  Blocks that assert facts not yet proven  (Reasoning engine R-08b)
// MATCH (m:Module)-[:EXPRESSED_BY]->(b:Block)-[:ASSERTS_FACT]->(f:Fact)
// WHERE m.id IN $module_ids AND NOT f.id IN $true_facts RETURN b.id, collect(f.id);
