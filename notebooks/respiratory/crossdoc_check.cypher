// Neo4j Browser 예시: :param cid=>'CONTACT_GUARDIAN'
MATCH path =
  (d:Document)-[:HAS_CHUNK]->(c:Chunk)<-[:SUPPORTED_BY]-(a:SchoolAction)
  -[:INSTANCE_OF]->(k:ActionConcept {concept_id: $cid})
WITH path, d, a
ORDER BY d.document_id, a.action_id
LIMIT 50
RETURN path
