# Factory Machine Observation Contract

`factory/machine-observation` is the single public machine-readable envelope
used by Chemvas, orca_auto, ollama_bot, and LLMdocx. This repository is the
versioned source of truth for the envelope schema, registered routes, payload
schemas, semantic validator, and conformance fixtures. Each durable operation
generation or delivered package exposes exactly one public metadata file named
`machine.json`.

Product-owned queue databases, lock files, recovery journals, and mutable state
files are internal implementation details. They are not alternate handoff
contracts and consumers such as Hermes must not parse them.

## Envelope v1

The top level contains exactly these nine fields:

1. `contract` - envelope identity (`factory/machine-observation`, version `1`)
2. `producer` - product name and product version
3. `operation` - producer-unique operation id and registered operation kind
4. `lifecycle` - execution phase, outcome, and stable machine codes
5. `handoff` - whether a downstream consumer may act
6. `delivery` - whether required artifacts were published
7. `artifacts` - artifact receipts keyed by stable artifact id
8. `lineage` - immediate consumed upstream envelopes
9. `payload` - one registered domain payload, or `null`

The normative JSON Schema is
[`schemas/machine-observation-v1.schema.json`](schemas/machine-observation-v1.schema.json).
Registered producer/operation/payload routes, payload keys, artifact-reference
closure, and payload readiness predicates are listed in
[`registry.json`](registry.json). Registry additions do not require envelope
version 2; changing a v1 field or invariant does.

## Required Semantics

- `queued` and `running` observations use `pending` for lifecycle outcome,
  handoff, and delivery.
- `finished` observations use a terminal lifecycle outcome and non-pending
  handoff and delivery states.
- `handoff.status == "ready"` requires `finished`, `succeeded`, `complete`, a
  non-null payload, and the payload contract's own readiness predicate.
- Lifecycle outcome and delivery are independent. A successful calculation
  whose required publication failed is `succeeded / blocked / incomplete`.
- `uncertain` is reserved for an operation or commit result that cannot be
  established. It is not a synonym for incomplete delivery.
- An `available` artifact receipt carries a package-root-relative POSIX path,
  media type, byte count, and SHA-256 of the exact bytes.
- Payloads refer to files by artifact id. They do not repeat paths, sizes, or
  hashes.
- Lineage lists only immediate upstream `machine.json` envelopes actually
  consumed, identified by producer, operation id, and exact envelope SHA-256.
- A terminal `machine.json` is published after all artifacts and is immutable.
  A later hash mismatch means the package is corrupt; producers do not rewrite
  the observation to report that corruption.

Human HTML, XYZ, CSV, Markdown, logs, and document binaries may accompany
`machine.json`; they are not additional machine metadata files.

## Validation

Run the schema and semantic fixture checks with:

```bash
python3 -m unittest discover -s tests -v
```

Validate a producer generation, including the required basename and every
available artifact's exact bytes, with:

```bash
python3 scripts/validate.py --machine /absolute/generation/machine.json
```

The validator requires the development-only `jsonschema` package. Products do
not depend on this repository at runtime. Each producer CI checks out this
repository at an immutable commit, runs an actual emitter example, and passes
the resulting `machine.json` to this validator. Contract updates therefore land
here first, then each producer deliberately advances its pinned commit and
conformance test.

Hermes remains a third-party consumer. Its user-local validator bundles the
exact `registry.json` from the pinned contract commit and applies the same route,
artifact-reference, and readiness table without requiring `jsonschema` at
runtime; the Hermes core is not modified.
