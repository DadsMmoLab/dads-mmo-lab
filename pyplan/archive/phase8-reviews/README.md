# The review rounds, and what can and cannot be audited from this directory

Two review rounds ran over the Phase 8 scoping documents on 2026-09-06. The reconciliation section
of `pyplan/phase8-parity-decisions.md` says what was applied and what was refuted. The second round
made a fair complaint about that section: the number it quotes cannot be audited, because the
review outputs themselves were not in the tree.

This directory is the partial remedy, and it is honest about being partial.

## Round one — three reviews, forty-nine findings

| Review | Model family | Findings | Committed here? |
|---|---|---|---|
| Adversarial | Codex, a different model family | 5 | **Assessed, not verbatim** — `round1-adversarial-assessment.md` is the orchestrator's finding-by-finding assessment written while the review was in hand, including two verifications that made its fixes cheaper. The raw output was a tool result and is not a file. |
| Superpowers | opus | 23 | **No.** Returned as an agent result, not written to a file. |
| Record and process | opus | 21 | **No.** Same. |

So "forty-nine findings, forty-six applied" is checkable against the documents — the second round
spot-checked six such claims and found three false, which were then corrected — but it is **not**
auditable line by line from this directory. A later session should treat the count as the
orchestrator's summary rather than as evidence, and should read the reconciliation section's
individual claims as the auditable part.

The lesson is the one `phase8-judges/panel-reads.md` records for reads: an assertion about work done
is worth what its artefact is worth. Reviews dispatched in a later round should be told to write
their output to a file under this directory, not only to return it.

## Round two — one review of what round one produced

`citation-pass-2.md` is the second citation pass. The round-two review itself (19 findings, most of
them regressions introduced by round one's own fixes) is likewise not committed verbatim; its
findings are visible as the changes in commit `5e274fb9` and in the reconciliation's later
paragraphs.
