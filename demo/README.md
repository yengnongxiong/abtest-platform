# demo

A static page that uses the SDK the way a real site would, served by nginx at http://localhost:8080 under `make dev`. It shows your variant and flags, a checkout button that follows the `checkout-button` experiment, and every request the SDK sends.

Read first:
- [index.html](index.html): the page, and how it calls the SDK
- [Dockerfile](Dockerfile): builds the SDK and serves it next to the page
- [write-config.sh](write-config.sh): writes the API address and the (public) client key into the page when the container starts
