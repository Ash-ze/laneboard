# Contributing to laneboard

Thanks for helping! laneboard is small on purpose. Two rules shape every change:

1. **Read-only.** No source, adapter or endpoint may modify anything: no writes to
   status files, repos, agent logs or remote machines, no agent control.
2. **No credentials.** laneboard never stores or asks for keys or passwords. Remote access
   goes through the user's own `ssh`; secrets for notifications come from environment variables.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

Tests never touch the network or real machines: ssh is simulated by a small fake `ssh`
script (see `tests/conftest.py`), git repos and agent logs are generated in temp dirs.

Try your change against the demo board:

```bash
python examples/demo/make_demo.py /tmp/laneboard-demo
laneboard -c /tmp/laneboard-demo/laneboard.toml serve
```

Screenshots in `docs/screenshots/` are produced with `scripts/screenshots.py`
(needs `pip install playwright` and an installed Chromium; the script never downloads browsers).

## Guidelines

* Python >= 3.11, standard library only at runtime. Optional extras are fine if clearly optional.
* The web page must stay self-contained: no CDNs, fonts or external requests. It must work at
  360px width and in dark mode.
* Escape everything that comes from a source before rendering.
* New sources: a function `(spec: dict) -> list[Lane]` registered with `@register("name")`,
  plus tests and a README section. A failing source must return an error lane, not raise.
* Agent log adapters: be defensive, read only the tail of files, keep only short snippets,
  and document the observed format and tool version in the module docstring.
* Keep personal data out of the repo: use generic names (`gpu-box`, `~/projects/...`) in
  examples, docs and tests.

## Pull requests

Keep PRs focused, include tests, and describe how you verified the change. By contributing
you agree that your contribution is licensed under the MIT license.
