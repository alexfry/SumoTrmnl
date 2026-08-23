# SumoTrmnl

A [TRMNL](https://usetrmnl.com/) private plugin that displays live Grand Sumo
(Makuuchi division) standings and bouts. It has two parts:

- **Plugin templates** — Liquid views in `views/` (`full`, `half_horizontal`,
  `quadrant`, `shared`, plus a separate `profile` view) with `views/settings.yml`.
  These are previewed locally with the `trmnlp` dev server (Ruby gem
  `trmnl_preview`).
- **Data pipeline** — Python scripts in `scripts/` (`fetch.py`, `fetch_profile.py`)
  that pull data from `sumo-api.com` / `sumostats.com` and either write JSON
  (`DATA_OUTPUT_PATH`) or push to a TRMNL webhook. Run hourly in CI by
  `.github/workflows/fetch.yml` (publishes to the `gh-pages` branch).

## Cursor Cloud specific instructions

Environment already provisioned by the startup update script (Ruby via rbenv,
the `trmnl_preview` gem, and Python `pillow`). Notes for running/developing here:

### Toolchain

- The `trmnl_preview` gem requires **Ruby >= 4.0** (its `trmnl-liquid`
  dependency needs 4.0). Ruby is installed with **rbenv** at `~/.rbenv`
  (global `4.0.6`). `rbenv init` is wired into `~/.bashrc`, so a normal login
  shell has `ruby`, `gem`, and `trmnlp` on `PATH`. In a non-login/non-interactive
  shell, either source `~/.bashrc` or call the shims directly
  (`~/.rbenv/shims/trmnlp`).
- Python is the system `python3` (3.12). `pillow` is installed with
  `pip install --break-system-packages` (Ubuntu 24.04 is externally managed);
  it is only needed for the optional Japanese-name PNG rendering in the fetch
  scripts.

### Running the preview server (the "app")

- **Layout gotcha:** `trmnlp` expects a plugin project with `.trmnlp.yml` at the
  root and templates under `src/`, but this repo keeps its config in
  `config.toml` and its templates in `views/`. The update script bridges this by
  creating a local (git-excluded) `.trmnlp.yml` and a `src` -> `views` symlink so
  `bin/dev` / `trmnlp serve` work unmodified. These bridge files are intentionally
  not committed (listed in `.git/info/exclude`); do not commit them.
- Start it with `bin/dev` (runs `trmnlp serve`) from the repo root. It listens on
  `http://localhost:4567`. Preview a view at `/full`, `/half_horizontal`, or
  `/quadrant`; get rendered HTML at `/render/<view>.html` and JSON context at
  `/data`.
- **Inject data:** the server starts with no data. Run `bin/seed` (POSTs
  `data/sample.json` to `/webhook`) to load the bundled sample, or push live data
  with `DATA_OUTPUT_PATH=- python3 scripts/fetch.py` piped to the webhook.
  Sample variables are merged at the template root (e.g. `standings`, `torikumi`).
- The `profile` view is a separate secondary plugin driven by `profile.json`
  (from `fetch_profile.py`); it is not part of trmnlp's default view set, so
  `/render/profile.html` returns 404 under the standard server. The core Sumo
  Stats views (`full`, `half_horizontal`, `quadrant`) are the main product.

### Data pipeline

- Run a fetch without any TRMNL credentials by writing to a file:
  `DATA_OUTPUT_PATH=/tmp/out.json python3 scripts/fetch.py`. It hits the public
  sumo APIs (egress is open) and prints the detected basho/day.
- Pushing to TRMNL requires `TRMNL_PLUGIN_UUID` + `TRMNL_API_KEY` (not needed for
  local preview).
