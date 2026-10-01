# Does my MEV protection work?

Paste an Ethereum transaction hash to check whether it was sandwiched as private order flow, and see the attack structure, OFA attribution, and the bot behind it.

Data: the 30,607 private-flow sandwiches by 7 persistent bots against 39,461 private Ethereum swaps (1 July 2023 to 30 June 2026) from

> Heimbach, Solmaz, Öz, Ferreira Torres. *No Place to Hide: An Analysis on Protected Order Flow Sandwich Attacks.* arXiv:2609.28115, 2026.

Static site, no build step. Each dataset is a sibling folder with the same two-file layout: `<folder>/idx/` maps hash prefixes to sandwich ids, `<folder>/s/` holds the sandwich records, so each lookup fetches two small files. `ethereum/` is the private-flow dataset the page queries today; `base/`, `tron/` and `eth_reorg/` hold the paper's other datasets in the same format, not yet wired into the lookup. Serve over HTTP (e.g. `python -m http.server`); opening `index.html` from disk will not load the data.

## Python transaction tools

The [Python tools](python/README.md) decode swaps on Solana, Base and Ethereum and
summarize sandwich rows with dates, block/transaction ordering and swaps for every
supplied leg. RPC endpoints are supplied through environment variables or explicit
arguments. Offline examples, fixtures and tests are included.
