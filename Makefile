SHELL := /bin/bash

.PHONY: bootstrap db-up migrate test doctor runserver

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

runserver:
	./scripts/manage.sh runserver
