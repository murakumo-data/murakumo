# Security

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub: the repository's
**Security** tab → **Report a vulnerability**
(<https://github.com/murakumo-data/murakumo/security/advisories/new>). Say what you found, how to
reproduce it and which version it affects. You will get a reply within a week, and credit in the
release notes if you want it.

The latest release is the supported one.

## How Murakumo handles what it is trusted with

- **Secrets are never written to the project.** A connection names the environment variable that
  holds each secret (`password_env`, `url_env`); `murakumo.yaml`, the model and the generated code
  hold names only. Generated CI and Terraform read secrets from the CI's secret store and the
  cloud's secret manager.
- **The canvas server is local.** `murakumo canvas` listens on 127.0.0.1 only, answers only
  requests addressed to localhost (so DNS rebinding cannot reach it), and refuses any change that
  a browser says comes from another site. It has no accounts and no authentication: do not expose
  its port to a network.
- **Commands from the canvas are a fixed list.** A job started in the canvas runs `murakumo` with
  arguments built from validated options, never a shell string.
- **Uploads stay in `data/`**, which the project's `.gitignore` excludes, with names limited to
  letters, digits, dots, dashes and underscores.
- **Verify and preview runs are isolated.** On a shared warehouse every schema a run creates has a
  unique prefix and is dropped afterwards.
- **AI features are off until you turn them on**, use your own model provider key from an
  environment variable, and send the model (names, types, descriptions), never rows of data,
  unless you ask for a column's sample values.
