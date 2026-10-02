# Does my MEV protection work?

Paste a transaction hash — Ethereum, Base, Tron or Solana — to check whether it was sandwiched as protected order flow, and see the attack structure and the bot behind it.

Data, from

> Heimbach, Solmaz, Öz, Ferreira Torres. *No Place to Hide: An Analysis on Protected Order Flow Sandwich Attacks.* arXiv:2609.28115, 2026.

| Chain | Sandwiches | Bots | Protection |
| --- | --- | --- | --- |
| Ethereum | 30,607 | 7 | private submission (private RPCs, order-flow auctions) |
| Base | 1,889 | 4 | private mempool, visible only to the sequencer until inclusion |
| Tron | 38,567 | 12 | public mempool ordered first-come-first-served |
| Solana | 28,042,725 | 8,631 | no public mempool; forwarded only to upcoming leaders |

`eth_reorg/` holds a further 2,576 sandwiches against 2,875 Ethereum private transactions that were briefly exposed when the block carrying them was reorged out. The lookup checks this set automatically when a hash isn't in the main Ethereum data. OFA attribution (MEV-Share, MEVBlocker, Blink, Merkle) is available for Ethereum only.

Static site, no build step. Each on-site dataset is a sibling folder with the same two-file layout: `<folder>/idx/` maps hash prefixes to sandwich ids, `<folder>/s/` holds the sandwich records, so each lookup fetches two small files — `ethereum/`, `base/`, `tron/` and `eth_reorg/` are served this way. Solana is 9.4 GB, too large to host here, so it's read directly from a [Hugging Face dataset](https://huggingface.co/datasets/Lioba/solana_sandwich_attack_dataset) over byte-range requests; token names come from [Jupiter](https://jup.ag). Serve over HTTP (e.g. `python -m http.server`); opening `index.html` from disk will not load the data.

## Python transaction tools

The [Python tools](python/README.md) decode swaps on Solana, Base, Tron and Ethereum and
summarize sandwich rows with dates, block/transaction ordering and swaps for every
supplied leg. RPC endpoints are supplied through environment variables or explicit
arguments. Offline examples, fixtures and tests are included.
