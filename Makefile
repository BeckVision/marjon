SHELL := /bin/bash

.PHONY: bootstrap db-up migrate test doctor u001-health u001-audit u001-audit-sources u001-rd001-chain-audit u001-rd001-solscan-audit u001-fl001-derived-audit u001-repair u001-holders u001-rd001-errors u001-rd001-partials u001-rd001-partials-historical u001-rd001-partials-guarded u001-rd001-recent-cycle u001-rd001-recent-continuous u001-automation u001-snapshot u001-recover-after-reboot install-hooks runserver

bootstrap:
	./scripts/bootstrap.sh

db-up:
	docker compose up -d db

migrate:
	./scripts/manage.sh migrate

test:
	./scripts/test.sh

doctor:
	./scripts/doctor.sh

u001-health:
	./scripts/manage.sh u001_ingestion_health

u001-audit:
	./scripts/manage.sh audit_u001

u001-audit-sources:
	./scripts/manage.sh audit_u001_sources

u001-rd001-chain-audit:
	./scripts/manage.sh audit_u001_rd001_chain

u001-rd001-solscan-audit:
	./scripts/manage.sh audit_u001_rd001_solscan

u001-fl001-derived-audit:
	./scripts/manage.sh audit_u001_fl001_derived

u001-repair:
	./scripts/manage.sh repair_u001_ingestion

u001-holders:
	./scripts/run_holders.sh

u001-rd001-errors:
	./scripts/run_batch_errors.sh

u001-rd001-partials:
	./scripts/run_batch_partials.sh

u001-rd001-partials-historical:
	./scripts/run_batch_partials_historical.sh

u001-rd001-partials-guarded:
	./scripts/run_batch_partials_guarded.sh

u001-rd001-recent-cycle:
	./scripts/manage.sh run_u001_rd001_recent_cycle

u001-rd001-recent-continuous:
	./scripts/run_u001_rd001_recent_continuous.sh

u001-automation:
	./scripts/run_u001_automation.sh

u001-snapshot:
	./scripts/manage.sh snapshot_u001_ops

u001-recover-after-reboot:
	./scripts/recover_after_reboot.sh

install-hooks:
	./scripts/install-hooks.sh

runserver:
	./scripts/manage.sh runserver
