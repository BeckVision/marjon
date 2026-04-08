SHELL := /bin/bash

.PHONY: bootstrap db-up migrate test doctor install-hooks runserver

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

install-hooks:
	./scripts/install-hooks.sh

runserver:
	./scripts/manage.sh runserver
