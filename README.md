# Defence Industry Knowledge Management System

A personal knowledge management system for tracking UK defence industry programmes,
technologies, hardware, companies, standards, and open tenders from open sources.

## What this is

An agentic ingestion and retrieval system: feed it documents and web pages, it
extracts structured knowledge into a hybrid knowledge graph + vector store
against a defined ontology, and you query it via natural language chat, a
node-centric graph explorer, and a card-based search/browse interface.

This is a personal side project, built substantially with Claude Code, not a
commercial product. It is not a CC-branded tool and does not reference or store
any real client or export-controlled material — all sources are open and
public.

## Why

Two problems this is meant to solve:

- **Retention** — knowledge about this space currently lives in people's heads
  and scattered documents; things get forgotten or take too long to
  rediscover.
- **Discovery** — new, relevant developments get found too late, after the
  fact, rather than surfaced proactively. (This is the harder, less-proven
  part of the system — see `ROADMAP.md`.)

## Status

Pre-build. See `ROADMAP.md` for the milestone plan, starting with M0
(parsing, chunking, and extraction proof of concept on a small real
document set).

## Document map

- **`CLAUDE.md`** — working instructions and conventions for Claude Code on
  this project. Read this first if you're an agent picking up work here.
- **`ARCHITECTURE.md`** — the technical design: ontology, storage, extraction
  pipeline, entity resolution, retrieval, interfaces.
- **`DECISIONS.md`** — log of key architectural decisions and why they were
  made, so later work doesn't re-litigate settled questions.
- **`ROADMAP.md`** — staged milestones, what's in v1 vs deferred.
- **`BACKLOG.md`** — one-line backlog items, expanded conversationally when
  briefing Claude Code on a specific piece of work.

## Confidentiality

All source material is open and public (government publications, press
releases, company websites, standards documents). No client-identifying or
classified/export-controlled material is to be ingested or stored in this
system. If that ever changes, infrastructure moves in-house — see
`DECISIONS.md`.
