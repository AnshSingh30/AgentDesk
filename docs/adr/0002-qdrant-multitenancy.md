# ADR 0002: Qdrant with payload-based multitenancy and hybrid search

Status: accepted · Date: 2026-09-16

## Context
Tenants must be isolated, retrieval must combine lexical and semantic matching, and embedding models may differ per knowledge base.

## Decision
One Qdrant collection per embedding model/dimension (`chunks_<model>_<dim>`), named vectors `dense` (cosine) and `bm25` (sparse with IDF modifier), payload indexes on `org_id` (`is_tenant=true`), `kb_id`, `document_id`. Every query goes through a single `search()` function that always applies the tenant filter. Hybrid retrieval uses the Query API with RRF fusion; the dense cosine similarity (recomputed from returned vectors) decides relevance against the configured threshold.

## Consequences
- No per-tenant collections to manage; tenant filtering is unit-tested in one place.
- Threshold semantics stay meaningful (cosine) even though RRF scores are rank-based.
- Changing a KB's embedding model means a full re-index into another collection.
