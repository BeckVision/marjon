SHELL := /bin/bash

.PHONY: bootstrap db-up migrate test doctor u001-health u001-repair u001-holders u001-rd001-errors u001-rd001-partials u001-rd001-partials-historical install-hooks runserver

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

install-hooks:
	./scripts/install-hooks.sh

runserver:
	./scripts/manage.sh runserver
