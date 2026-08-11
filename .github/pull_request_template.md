## Motivation

<!-- What producer or consumer need drives this change? A new registration needs a real producer and a real consumer; name both. -->

## Changes

<!-- Concrete schema, registry, validator, fixture, or documentation changes. -->

-

## Contract impact

<!-- Exactly one. COMPATIBILITY.md defines these and lists the five changes that count as additive. -->

- [ ] none
- [ ] additive
- [ ] semantic — not accepted into envelope v1
- [ ] breaking — not accepted into envelope v1

If anything on the validator's read path changed — `schemas/`, `registry.json`,
`scripts/validate.py`, or `requirements.txt` — say here how each producer and
consumer was checked against the new commit, and which readers must be updated
before any producer emits the new surface. The Hermes copy cannot detect that it
is stale; a release that registers new surface is not finished until it is
refreshed.

## Verification

<!-- Anything beyond CI: what you ran, against which products, and what it showed. -->

-
