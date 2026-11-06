// CrossDoc: CONTACT_GUARDIAN의 문서별 SchoolAction·Chunk·Document 경로를 보여준다.
MATCH path =
  (doc:Document)-[:HAS_CHUNK]->(chunk:Chunk)<-[:SUPPORTED_BY]-(action:SchoolAction)
  -[:INSTANCE_OF]->(concept:ActionConcept {concept_id: 'CONTACT_GUARDIAN'})
WITH path, doc, action
ORDER BY doc.document_id, action.action_id
LIMIT 50
RETURN path

// 질병 중심: 성홍열 ABOUT 청크·문서와 그 질병을 대상으로 하는 SchoolAction을 보여준다.
MATCH path =
  (action:SchoolAction)-[:FOR_DISEASE]->(disease:Disease {disease_id: 'SCARLET_FEVER'})
  <-[:ABOUT]-(chunk:Chunk)<-[:HAS_CHUNK]-(doc:Document)
WITH path, doc, action, chunk
ORDER BY doc.document_id, action.action_id, chunk.chunk_id
LIMIT 50
RETURN path

// 인플루엔자 시설 지침의 Document·Chunk·SchoolAction·ActionConcept·Disease 구조를 보여준다.
MATCH document_path =
  (doc:Document {document_id: 'kdca_flu_facility_2026'})-[:HAS_CHUNK]->(chunk:Chunk)
OPTIONAL MATCH action_path = (action:SchoolAction)-[:SUPPORTED_BY]->(chunk)
OPTIONAL MATCH concept_path = (action)-[:INSTANCE_OF]->(concept:ActionConcept)
OPTIONAL MATCH for_disease_path = (action)-[:FOR_DISEASE]->(action_disease:Disease)
OPTIONAL MATCH about_path = (chunk)-[:ABOUT]->(about_disease:Disease)
WITH document_path, action_path, concept_path, for_disease_path, about_path, doc, chunk
ORDER BY doc.document_id, chunk.chunk_id
LIMIT 80
RETURN document_path, action_path, concept_path, for_disease_path, about_path

// Document별 Chunk·SchoolAction 노드 수를 표로 보여준다.
MATCH (doc:Document)
OPTIONAL MATCH (doc)-[:HAS_CHUNK]->(chunk:Chunk)
OPTIONAL MATCH (action:SchoolAction)
WHERE action.action_id STARTS WITH doc.document_id + ':'
RETURN doc.document_id AS document_id, doc.title AS title,
       count(DISTINCT chunk) AS chunk_count,
       count(DISTINCT action) AS school_action_count
ORDER BY document_id
