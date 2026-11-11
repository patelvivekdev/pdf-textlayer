.PHONY: all test test-rust test-python test-node lint format build docs

all: lint test

test: test-rust test-python test-node

test-rust:
	cargo test --workspace

test-python:
	cd packages/python && uv sync && uv run pytest

test-node:
	cd packages/node && npm install && npm run build:rs && npm test

lint:
	cargo fmt --all --check
	cargo clippy --workspace --all-targets -- -D warnings
	cd packages/python && uv run ruff check . ../../benchmarks ../../scripts && uv run ruff format --check .
	cd packages/node && npx tsc --noEmit

format:
	cargo fmt --all
	cd packages/python && uv run ruff format . && uv run ruff check --fix .

build:
	cargo build --release -p pdf-textlayer
	cd packages/python && uv build
	cd packages/node && npm run build

docs:
	cd docs && npm install && npm run validate && npm run build
