# Private Sandwich Lookup

Paste an Ethereum transaction hash to check whether it was sandwiched as private order flow, and see the attack structure, OFA attribution, and the bot behind it.

Data: the 30,607 private-flow sandwiches by 7 persistent bots against 39,461 private Ethereum swaps (1 July 2023 to 30 June 2026) from

> Heimbach, Solmaz, Öz, Ferreira Torres. *No Place to Hide: An Analysis on Protected Order Flow Sandwich Attacks.* arXiv:2609.28115, 2026.

Static site, no build step. `idx/` maps hash prefixes to sandwich ids and `s/` holds the sandwich records, so each lookup fetches two small files. Serve over HTTP (e.g. `python -m http.server`); opening `index.html` from disk will not load the data.
