# Mosemo

## Development

Install the project and development tools:

```sh
uv sync
uv run pre-commit install
```

Lint and automatically fix safe violations:

```sh
uv run ruff check --fix .
```

Format the code:

```sh
uv run ruff format .
```

Type-check the project:

```sh
uv run ty check
```

Verify all checks without changing files:

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

Run every pre-commit check manually:

```sh
uv run pre-commit run --all-files
```
