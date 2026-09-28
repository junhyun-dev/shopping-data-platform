# Shopping Data Platform Agent Guide

This repository owns the executable local shopping-data slice: schema, SQL, code, tests, generated result artifacts, and loopback UI.

## Current outcome

Load the explicitly selected rows from the preserved UCI workbook, calculate `sample quantity sum` and `observed row count` by the source-displayed date and product code, and let a user open each number to see its contributing Excel rows and applied SQL. In the separate Kafka lab, verify redelivery and offset recovery against an explicit synthetic-event fixture without changing the UCI result.

## Contract boundaries

- The current source scope is Excel rows 2–8 and 143 from sheet `Online Retail`; it is not a full-dataset result.
- Preserve signed `Quantity`. Do not call the result sales, cancellations, refunds, payments, or net revenue.
- A file fingerprint plus sheet and source row number identifies a source location, not a business duplicate key.
- Do not expose `CustomerID` in generated JSON or the UI.
- A failed or incomplete run must not replace the last successfully published result.
- Serve only the explicit web assets and generated result endpoints on loopback. Never serve the repository, source-data directory, or a parent directory.
- Kafka lab event identity and Kafka delivery position are separate. Its event-id collision rule applies only to the explicit synthetic fixture, not to UCI transaction deduplication.
- Bind the Kafka sink to one cluster/group/topic/partition and persist its next expected offset in the same transaction as each accepted event. Refuse to infer a reset, rebuild, or missing prefix when broker and sink progress disagree.
- Kafka sink state, broker logs, topics, groups, and ports must remain separate from the UCI DuckDB and published result.

## Working rules

- Keep one local implementation branch. Do not commit or push without explicit authorization.
- Preserve `data/source/Online Retail.xlsx` byte-for-byte. The expected SHA-256 is in `config/source.json`.
- Use project-local files, DuckDB, and loopback execution only. Do not add cloud services, containers, global packages, or external writes without approval.
- The accepted Kafka slice may download the checksum-verified Apache Kafka 4.1.2 binary into `var/kafka/`, run one project-owned KRaft broker with broker and controller listeners bound to loopback, and use the already installed Python client. Do not format or delete any path outside the exact new experiment directory.
- Keep Kafka heap and runtime duration small. Stop only PIDs started by the current experiment and preserve failed-run evidence instead of broadly clearing broker logs or sink files.
- `README.md` is the current execution entry. Do not add a second status ledger.
- Run `pytest -q`, the actual-source pipeline, and the real-broker Kafka experiment before reporting the corresponding slice as verified.
