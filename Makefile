SHELL := /bin/bash

.PHONY: bootstrap db-up migrate test doctor u001-health u001-repair install-hooks runserver

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

install-hooks:
	./scripts/install-hooks.sh

runserver:
	./scripts/manage.sh runserver
