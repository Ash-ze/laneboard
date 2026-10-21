# Deploying laneboard

This document describes how to publish laneboard to PyPI and how to keep it running on
your own machine. Nothing here is automated; run the steps yourself when you are ready.

## 1. Publishing a release to PyPI (maintainers)

```bash
# 0. clean tree, tests green
pytest -q

# 1. bump the version in BOTH places
#    pyproject.toml            -> [project] version
#    src/laneboard/__init__.py -> __version__
git commit -am "release: v0.1.0" && git tag v0.1.0

# 2. build sdist + wheel
python -m pip install --upgrade build twine
python -m build
twine check dist/*

# 3. dry run on TestPyPI first
twine upload --repository testpypi dist/*
pipx install --index-url https://test.pypi.org/simple/ laneboard   # smoke test in a clean env

# 4. real upload (uses an API token from ~/.pypirc or TWINE_PASSWORD; never commit it)
twine upload dist/*
```

Preferred alternative: PyPI *Trusted Publishing* from a CI workflow, so no long-lived
token exists at all. Configure the trusted publisher on PyPI, then use the official
`pypa/gh-action-pypi-publish` action on tag pushes.

Check the name is still free before the first upload (`pip index versions laneboard`).

## 2. Running it permanently on your own machine

Install once:

```bash
pipx install laneboard
mkdir -p ~/.config/laneboard
laneboard init ~/.config/laneboard/config.toml
$EDITOR ~/.config/laneboard/config.toml
laneboard status            # check every source works
```

If you use ssh sources, make sure `ssh <host> true` works **non-interactively** (key in
ssh-agent or an unencrypted deploy key restricted to that use, host key already accepted).
laneboard runs ssh with `BatchMode=yes` and will report an error instead of prompting.

### Linux: systemd user service

`~/.config/systemd/user/laneboard.service`:

```ini
[Unit]
Description=laneboard read-only agent status board

[Service]
ExecStart=%h/.local/bin/laneboard serve --host 127.0.0.1 --port 8765
Restart=on-failure
# notification secrets, if any (file is chmod 600, not in git)
EnvironmentFile=-%h/.config/laneboard/env
# hardening: laneboard only needs to read
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h/.local/state/laneboard
PrivateTmp=yes

[Install]
WantedBy=default.target
```

```bash
mkdir -p ~/.local/state/laneboard
systemctl --user daemon-reload
systemctl --user enable --now laneboard
journalctl --user -u laneboard -f
loginctl enable-linger "$USER"   # keep it running when you are logged out (optional)
```

Note: `ssh` sources need access to your ssh agent. With systemd, set
`Environment=SSH_AUTH_SOCK=%t/ssh-agent.socket` (or wherever your agent socket lives).

### macOS: launchd

`~/Library/LaunchAgents/dev.laneboard.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.laneboard</string>
  <key>ProgramArguments</key>
  <array><string>/usr/local/bin/laneboard</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

Adjust the binary path to `which laneboard`, then `launchctl load ~/Library/LaunchAgents/dev.laneboard.plist`.

### Static snapshots + alerts with cron (no server at all)

```cron
*/10 * * * * $HOME/.local/bin/laneboard snapshot --html $HOME/private/board.html --notify
```

Put the HTML somewhere only you can read (a private synced folder, a host behind auth).

## 3. Viewing it from your phone

`serve` has no authentication and binds to 127.0.0.1. Recommended options, best first:

1. **Private VPN / tailnet**: run `laneboard serve --host <the machine's VPN address>`
   so it is reachable only inside your private network.
2. **SSH tunnel** from a laptop: `ssh -N -L 8765:127.0.0.1:8765 board-host`, then open
   `http://127.0.0.1:8765`.
3. **Reverse proxy with authentication** (e.g. basic auth or SSO in front of it) if you
   really need it on a public address. Use HTTPS. Never expose the bare server.
