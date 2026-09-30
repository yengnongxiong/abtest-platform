# scripts

Repo tools that aren't part of the app.

[screenshots.py](screenshots.py) regenerates the README's screenshots in [docs/screenshots/](../docs/screenshots). With Playwright, it creates the demo experiment in the dashboard, feeds it both [scenarios](../scenarios), and photographs each step. It needs an empty stack, because the experiment keys must be unused:

```sh
make down                                        # if your usual stack is running
COMPOSE_PROJECT_NAME=abtest-screens make dev     # a separate, empty database; leave it running
uv run --project server playwright install chromium   # once
make screenshots                                 # in a second terminal
COMPOSE_PROJECT_NAME=abtest-screens docker compose down --volumes   # afterwards
```
