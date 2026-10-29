# Lucas v16 provenance

- Repository: https://github.com/lucashsu007-create/fullhouse-hackathon-write-up-repo
- Commit: `8b98ee3efce3b224177609352258dc86db901cce`
- Source: `hero_bots/bot_v16_squeeze_C_metacap/bot.py`
- SHA-256: `ffa1ac5083482a96b240ce2a3dcff8f329333184f9006231d0c42bbbc877810a`
- License: MIT; original copyright notice is preserved in `LICENSE`.

`bot.py` is copied without modifications. `holdem/lucas.py` adapts the public
observation, normalizes chips to BB=100, compacts dealt-in seat numbers, and maps
returned raise totals back to legal local units. Each seat loads a fresh module
so no opponent statistics or private cards are shared between seats.

The adapter replaces the module's `_reconstruct_hand` callback with exact engine
history (street, amount owed, pot before action), rather than guessing streets
from the flat upstream action log. This preserves short all-in calls and raises
under this engine. Final public actions are observed even after a bot folds.
No strategy tables or policy functions are changed. The adapter requires eval7
instead of silently running upstream's missing-evaluator heuristic path.
